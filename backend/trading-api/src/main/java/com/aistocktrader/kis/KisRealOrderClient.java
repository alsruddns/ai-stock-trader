package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.OrderRequest;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.util.UriComponentsBuilder;

/** Real-account order adapter. Calls are impossible unless all production switches and read preflight gates are open. */
@Component
public class KisRealOrderClient {
    private static final Logger log = LoggerFactory.getLogger(KisRealOrderClient.class);
    private static final int MAX_TRANSIENT_READ_RETRIES = 1;
    private static final String DOMESTIC_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash";
    private static final String OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order";
    private static final String DOMESTIC_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl";
    private static final String OVERSEAS_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl";
    private static final String DOMESTIC_CANCEL_PREFLIGHT_PATH = "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl";

    private final KisProperties properties;
    private final TradingSafetyProperties safety;
    private final RestClient http;
    private final KisRestRateLimiter rateLimiter;
    private final KisAccessTokenProvider tokenProvider;

    public KisRealOrderClient(KisProperties properties, TradingSafetyProperties safety, RestClient.Builder builder) {
        this(properties, safety, builder, new KisRestRateLimiter());
    }

    public KisRealOrderClient(KisProperties properties, TradingSafetyProperties safety, RestClient.Builder builder,
                              KisRestRateLimiter rateLimiter) {
        this(properties, safety, builder, rateLimiter,
                new KisAccessTokenProvider(properties, builder, rateLimiter));
    }

    @Autowired
    public KisRealOrderClient(KisProperties properties, TradingSafetyProperties safety, RestClient.Builder builder,
                              KisRestRateLimiter rateLimiter, KisAccessTokenProvider tokenProvider) {
        this.properties = properties;
        this.safety = safety;
        this.http = builder.build();
        this.rateLimiter = rateLimiter;
        this.tokenProvider = tokenProvider;
    }

    public KisOrderReceipt placeRealLimitOrder(Market market, String symbol, OrderSide side,
                                                int quantity, BigDecimal price, Currency currency,
                                                String exchange, KisLiveRiskDecision riskDecision) {
        OrderRequest request = new OrderRequest(market, symbol, side, quantity, price, currency);
        if (riskDecision == null || !riskDecision.approves(request, exchange)) throw riskApprovalRequired();
        requireRealOrdersEnabled();
        validate(market, symbol, side, quantity, price, currency);
        BigDecimal notional = price.multiply(BigDecimal.valueOf(quantity));
        BigDecimal maximum = currency == Currency.KRW ? safety.maxOrderNotionalKrw() : safety.maxOrderNotionalUsd();
        if (notional.compareTo(maximum) > 0) {
            throw new IllegalArgumentException("KIS real order exceeds the configured notional limit.");
        }

        KisProperties.Account credentials = properties.credentials(KisEnvironment.REAL);
        String account = credentials.accountNumber();
        String path;
        String trId;
        Map<String, String> body;
        if (market == Market.KR) {
            path = DOMESTIC_ORDER_PATH;
            trId = side == OrderSide.BUY ? "TTTC0012U" : "TTTC0011U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "PDNO", symbol, "ORD_DVSN", "00", "ORD_QTY", Integer.toString(quantity),
                    "ORD_UNPR", price.toPlainString(), "EXCG_ID_DVSN_CD", "KRX",
                    "SLL_TYPE", side == OrderSide.SELL ? "01" : "", "CNDT_PRIC", "");
        } else {
            String kisExchange = usExchange(exchange);
            path = OVERSEAS_ORDER_PATH;
            trId = side == OrderSide.BUY ? "TTTT1002U" : "TTTT1006U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "OVRS_EXCG_CD", kisExchange, "PDNO", symbol.toUpperCase(Locale.ROOT),
                    "ORD_DVSN", "00", "ORD_QTY", Integer.toString(quantity),
                    "OVRS_ORD_UNPR", price.toPlainString(), "SLL_TYPE", side == OrderSide.SELL ? "00" : "",
                    "ORD_SVR_DVSN_CD", "0");
        }

        JsonNode response = post(path, trId, body, request, exchange, riskDecision);
        JsonNode output = response.path("output");
        return new KisOrderReceipt("real", market.name(), symbol.toUpperCase(Locale.ROOT), side.name(),
                text(output, "ODNO"), text(output, "KRX_FWDG_ORD_ORGNO"), text(response, "msg_cd"), Instant.now());
    }

    public void cancelRealOrder(Market market, String symbol, String brokerOrderNumber,
                                String brokerOrganizationNumber, int remainingQuantity, String exchange) {
        requireRealOrdersEnabled();
        if (market == null || symbol == null || symbol.isBlank() || brokerOrderNumber == null
                || brokerOrderNumber.isBlank() || remainingQuantity <= 0) {
            throw new IllegalArgumentException("A valid market, symbol, original order number, and remaining quantity are required.");
        }
        KisProperties.Account credentials = properties.credentials(KisEnvironment.REAL);
        String account = credentials.accountNumber();
        String path;
        String trId;
        Map<String, String> body;
        if (market == Market.KR) {
            if (brokerOrganizationNumber == null || brokerOrganizationNumber.isBlank()) {
                throw new IllegalArgumentException("Domestic cancellation requires the broker organization number.");
            }
            int cancelableQuantity = inquireDomesticCancelableQuantity(credentials, account,
                    symbol, brokerOrderNumber, brokerOrganizationNumber);
            if (cancelableQuantity != remainingQuantity) {
                throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE,
                        "KIS domestic cancel preflight did not match the latest open quantity (code REAL_CANCEL_PREFLIGHT_BLOCKED).");
            }
            path = DOMESTIC_CANCEL_PATH;
            trId = "TTTC0013U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "KRX_FWDG_ORD_ORGNO", brokerOrganizationNumber, "ORGN_ODNO", brokerOrderNumber,
                    "ORD_DVSN", "00", "RVSE_CNCL_DVSN_CD", "02", "ORD_QTY", "0",
                    "ORD_UNPR", "0", "QTY_ALL_ORD_YN", "Y", "EXCG_ID_DVSN_CD", "KRX");
        } else {
            path = OVERSEAS_CANCEL_PATH;
            trId = "TTTT1004U";
            body = Map.ofEntries(Map.entry("CANO", account.substring(0, 8)),
                    Map.entry("ACNT_PRDT_CD", account.substring(8)),
                    Map.entry("OVRS_EXCG_CD", usExchange(exchange)),
                    Map.entry("PDNO", symbol.toUpperCase(Locale.ROOT)), Map.entry("ORGN_ODNO", brokerOrderNumber),
                    Map.entry("RVSE_CNCL_DVSN_CD", "02"), Map.entry("ORD_QTY", "0"),
                    Map.entry("OVRS_ORD_UNPR", "0"), Map.entry("QTY_ALL_ORD_YN", "Y"),
                    Map.entry("MGCO_APTM_ODNO", ""), Map.entry("ORD_SVR_DVSN_CD", "0"));
        }
        post(path, trId, body, null, null, null);
    }

    private int inquireDomesticCancelableQuantity(KisProperties.Account credentials, String account,
                                                   String symbol, String brokerOrderNumber,
                                                   String brokerOrganizationNumber) {
        requireRealAccountReadEnabled();
        String continuation = "";
        String nextSearch = "";
        String nextKey = "";
        Set<List<String>> seenCursors = new HashSet<>();
        for (int page = 0; page < 10; page++) {
            MultiValueMap<String, String> params = new LinkedMultiValueMap<>();
            params.add("CANO", account.substring(0, 8));
            params.add("ACNT_PRDT_CD", account.substring(8));
            params.add("INQR_DVSN_1", "1");
            params.add("INQR_DVSN_2", "0");
            params.add("CTX_AREA_FK100", nextSearch);
            params.add("CTX_AREA_NK100", nextKey);
            BrokerQueryResponse query;
            try {
                query = get(DOMESTIC_CANCEL_PREFLIGHT_PATH, "TTTC0084R", params, continuation, credentials);
            } catch (KisApiException exception) {
                throw cancelPreflightBlocked();
            }
            JsonNode response = query.body();
            if (response == null || !"0".equals(response.path("rt_cd").asText())) {
                throw cancelPreflightBlocked();
            }
            JsonNode rows = response.path("output");
            if (rows.isArray()) {
                for (JsonNode row : rows) {
                    if (symbol.equalsIgnoreCase(firstText(row, "pdno", "PDNO"))
                            && brokerOrderNumber.equals(firstText(row, "odno", "ODNO"))
                            && brokerOrganizationNumber.equals(firstText(row, "ord_gno_brno", "ORD_GNO_BRNO"))) {
                        Integer quantity = integer(row, "psbl_qty", "PSBL_QTY");
                        if (quantity == null || quantity <= 0) throw cancelPreflightBlocked();
                        return quantity;
                    }
                }
            } else if (rows.isObject()
                    && symbol.equalsIgnoreCase(firstText(rows, "pdno", "PDNO"))
                    && brokerOrderNumber.equals(firstText(rows, "odno", "ODNO"))
                    && brokerOrganizationNumber.equals(firstText(rows, "ord_gno_brno", "ORD_GNO_BRNO"))) {
                Integer quantity = integer(rows, "psbl_qty", "PSBL_QTY");
                if (quantity == null || quantity <= 0) throw cancelPreflightBlocked();
                return quantity;
            }
            continuation = query.continuation();
            if (!"M".equalsIgnoreCase(continuation) && !"F".equalsIgnoreCase(continuation)) break;
            continuation = "N";
            nextSearch = response.path("ctx_area_fk100").asText("");
            nextKey = response.path("ctx_area_nk100").asText("");
            if (nextSearch.isBlank() || nextKey.isBlank()) throw cancelPreflightBlocked();
        }
        throw cancelPreflightBlocked();
    }

    private static KisApiException cancelPreflightBlocked() {
        return new KisApiException(HttpStatus.SERVICE_UNAVAILABLE,
                "KIS domestic cancel preflight could not confirm a matching cancelable quantity (code REAL_CANCEL_PREFLIGHT_BLOCKED).");
    }

    /** Read-only lookup for restart/timeout reconciliation. An absent broker row is never treated as rejection. */
    public Optional<KisBrokerOrderSnapshot> reconcileRealOrder(KisOrderIntent intent) {
        requireRealAccountReadEnabled();
        if (intent == null || intent.getEnvironment() != KisEnvironment.REAL
                || intent.getBrokerOrderNumber() == null || intent.getBrokerOrderNumber().isBlank()) {
            return Optional.empty();
        }
        KisProperties.Account credentials = properties.credentials(KisEnvironment.REAL);
        String account = credentials.accountNumber();
        LocalDate orderDate = intent.getCreatedAt().atZone(intent.getMarket() == Market.KR
                ? ZoneId.of("Asia/Seoul") : ZoneId.of("America/New_York")).toLocalDate();
        String date = orderDate.format(java.time.format.DateTimeFormatter.BASIC_ISO_DATE);
        String continuation = "";
        String nextSearch = "";
        String nextKey = "";
        Set<List<String>> seenCursors = new HashSet<>();
        for (int page = 0; page < 10; page++) {
            MultiValueMap<String, String> params = new LinkedMultiValueMap<>();
            params.add("CANO", account.substring(0, 8));
            params.add("ACNT_PRDT_CD", account.substring(8));
            String path;
            String trId;
            String rowsField;
            if (intent.getMarket() == Market.KR) {
                path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld";
                trId = "TTTC0081R";
                rowsField = "output1";
                params.add("INQR_STRT_DT", date); params.add("INQR_END_DT", date);
                params.add("SLL_BUY_DVSN_CD", "00"); params.add("PDNO", intent.getSymbol());
                params.add("CCLD_DVSN", "00"); params.add("INQR_DVSN", "00");
                params.add("INQR_DVSN_3", "00"); params.add("ORD_GNO_BRNO", "");
                params.add("ODNO", intent.getBrokerOrderNumber()); params.add("INQR_DVSN_1", "");
                params.add("EXCG_ID_DVSN_CD", "KRX");
                params.add("CTX_AREA_FK100", nextSearch); params.add("CTX_AREA_NK100", nextKey);
            } else {
                path = "/uapi/overseas-stock/v1/trading/inquire-ccnl";
                trId = "TTTS3035R";
                rowsField = "output";
                params.add("PDNO", intent.getSymbol()); params.add("ORD_STRT_DT", date); params.add("ORD_END_DT", date);
                params.add("SLL_BUY_DVSN", "00"); params.add("CCLD_NCCS_DVSN", "00");
                params.add("OVRS_EXCG_CD", usExchange(intent.getExchangeCode())); params.add("SORT_SQN", "DS");
                // KIS documents this query as a date-range lookup; overseas order-number filters are not reliable.
                params.add("ORD_DT", ""); params.add("ORD_GNO_BRNO", ""); params.add("ODNO", "");
                params.add("CTX_AREA_FK200", nextSearch); params.add("CTX_AREA_NK200", nextKey);
            }
            BrokerQueryResponse query = get(path, trId, params, continuation, credentials);
            JsonNode response = query.body();
            if (!"0".equals(response.path("rt_cd").asText())) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS real order reconciliation failed (code " + safeCode(response.path("msg_cd").asText("unknown")) + ").");
            }
            JsonNode rows = response.path(rowsField);
            if (rows.isArray()) {
                for (JsonNode row : rows) {
                    String brokerNumber = firstText(row, "odno", "ODNO");
                    if (intent.getBrokerOrderNumber().equals(brokerNumber)) {
                        if (!KisBrokerOrderSnapshotValidator.matchesBrokerIdentity(row, intent)) return Optional.empty();
                        return parseSnapshot(row, intent);
                    }
                }
            }
            continuation = query.continuation();
            if (!"M".equalsIgnoreCase(continuation) && !"F".equalsIgnoreCase(continuation)) break;
            if (intent.getMarket() == Market.KR) {
                nextSearch = response.path("ctx_area_fk100").asText("");
                nextKey = response.path("ctx_area_nk100").asText("");
            } else {
                nextSearch = response.path("ctx_area_fk200").asText("");
                nextKey = response.path("ctx_area_nk200").asText("");
            }
            if (nextSearch.isBlank() || nextKey.isBlank()
                    || !seenCursors.add(List.of(nextSearch, nextKey))) {
                // A missing or repeated cursor cannot establish the broker's order state.
                // Keep the intent unresolved instead of re-reading a stale page.
                return Optional.empty();
            }
            continuation = "N";
        }
        return Optional.empty();
    }

    /** Read-only complete session execution lookup used to detect trades outside this application's ledger. */
    public List<KisBrokerSessionExecution> getRealSessionExecutions(Market market, LocalDate sessionDate) {
        requireRealAccountReadEnabled();
        if (market == null || sessionDate == null) {
            throw new IllegalArgumentException("A market and exchange-local session date are required.");
        }
        KisProperties.Account credentials = properties.credentials(KisEnvironment.REAL);
        String account = credentials.accountNumber();
        String date = sessionDate.format(DateTimeFormatter.BASIC_ISO_DATE);
        String path = market == Market.KR
                ? "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
                : "/uapi/overseas-stock/v1/trading/inquire-ccnl";
        String trId = market == Market.KR ? "TTTC8001R" : "TTTS3035R";
        String rowsField = market == Market.KR ? "output1" : "output";
        String fkField = market == Market.KR ? "CTX_AREA_FK100" : "CTX_AREA_FK200";
        String nkField = market == Market.KR ? "CTX_AREA_NK100" : "CTX_AREA_NK200";
        String continuation = "";
        String nextSearch = "";
        String nextKey = "";
        var executions = new LinkedHashMap<String, KisBrokerSessionExecution>();
        Set<List<String>> seenCursors = new HashSet<>();
        for (int pageNumber = 0; pageNumber < 20; pageNumber++) {
            MultiValueMap<String, String> params = new LinkedMultiValueMap<>();
            params.add("CANO", account.substring(0, 8));
            params.add("ACNT_PRDT_CD", account.substring(8));
            if (market == Market.KR) {
                params.add("INQR_STRT_DT", date); params.add("INQR_END_DT", date);
                params.add("SLL_BUY_DVSN_CD", "00"); params.add("INQR_DVSN", "01");
                params.add("PDNO", ""); params.add("CCLD_DVSN", "01");
                params.add("ORD_GNO_BRNO", ""); params.add("ODNO", "");
                params.add("INQR_DVSN_3", "00"); params.add("INQR_DVSN_1", "");
                params.add("EXCG_ID_DVSN_CD", "ALL");
                params.add("CTX_AREA_FK100", nextSearch); params.add("CTX_AREA_NK100", nextKey);
            } else {
                params.add("PDNO", ""); params.add("ORD_STRT_DT", date); params.add("ORD_END_DT", date);
                params.add("SLL_BUY_DVSN", "00"); params.add("CCLD_NCCS_DVSN", "01");
                params.add("OVRS_EXCG_CD", "%"); params.add("SORT_SQN", "DS");
                params.add("ORD_DT", ""); params.add("ORD_GNO_BRNO", ""); params.add("ODNO", "");
                params.add("CTX_AREA_FK200", nextSearch); params.add("CTX_AREA_NK200", nextKey);
            }
            BrokerQueryResponse query = get(path, trId, params, continuation, credentials);
            JsonNode response = query.body();
            if (response == null || !"0".equals(response.path("rt_cd").asText())) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS session execution lookup failed (code "
                                + safeCode(response == null ? "unknown" : response.path("msg_cd").asText("unknown")) + ").");
            }
            JsonNode rows = response.path(rowsField);
            if (!rows.isArray() && !rows.isObject()) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS session execution response is incomplete.");
            }
            if (rows.isArray()) {
                for (JsonNode row : rows) addSessionExecution(executions, row);
            } else if (!rows.isEmpty()) {
                addSessionExecution(executions, rows);
            }
            String responseContinuation = query.continuation();
            if (!"M".equalsIgnoreCase(responseContinuation) && !"F".equalsIgnoreCase(responseContinuation)) {
                return List.copyOf(executions.values());
            }
            if (pageNumber == 19) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS session execution lookup exceeded the supported pagination limit.");
            }
            nextSearch = response.path(fkField.toLowerCase(Locale.ROOT)).asText("");
            nextKey = response.path(nkField.toLowerCase(Locale.ROOT)).asText("");
            if (nextSearch.isBlank() || nextKey.isBlank()) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS session execution pagination is incomplete.");
            }
            if (!seenCursors.add(List.of(nextSearch, nextKey))) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS session execution pagination repeated a page cursor.");
            }
            continuation = "N";
        }
        throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS session execution pagination did not complete.");
    }

    private static void addSessionExecution(Map<String, KisBrokerSessionExecution> executions, JsonNode row) {
        String orderNumber = consistentSessionText(row, "odno", "ODNO");
        String symbol = consistentSessionText(row, "pdno", "PDNO", "ovrs_pdno", "OVRS_PDNO");
        String sideCode = consistentSessionText(row, "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD",
                "sll_buy_dvsn", "SLL_BUY_DVSN");
        OrderSide side = switch (sideCode.trim().toUpperCase(Locale.ROOT)) {
            case "01", "SELL" -> OrderSide.SELL;
            case "02", "BUY" -> OrderSide.BUY;
            default -> null;
        };
        String organization = consistentSessionText(row, "ord_gno_brno", "ORD_GNO_BRNO",
                "krx_fwdg_ord_orgno", "KRX_FWDG_ORD_ORGNO");
        if (orderNumber.isBlank() || symbol.isBlank() || side == null) {
            throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS session execution row is incomplete.");
        }
        KisBrokerSessionExecution value = new KisBrokerSessionExecution(orderNumber, symbol, side, organization);
        KisBrokerSessionExecution previous = executions.putIfAbsent(orderNumber, value);
        if (previous != null && (!previous.symbol().equalsIgnoreCase(value.symbol())
                || previous.side() != value.side()
                || (!previous.organizationNumber().isBlank() && !value.organizationNumber().isBlank()
                    && !previous.organizationNumber().equalsIgnoreCase(value.organizationNumber())))) {
            throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS session execution rows conflict.");
        }
    }

    private static String consistentSessionText(JsonNode row, String... fields) {
        String selected = "";
        for (String field : fields) {
            JsonNode candidate = row.path(field);
            if (candidate.isMissingNode() || candidate.isNull()) continue;
            if (!candidate.isTextual() && !candidate.isNumber()) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS session execution row is malformed.");
            }
            String value = candidate.asText("").trim();
            if (value.isBlank()) continue;
            if (!selected.isBlank() && !selected.equalsIgnoreCase(value)) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS session execution row contains conflicting fields.");
            }
            selected = value;
        }
        return selected;
    }

    public record KisBrokerSessionExecution(String orderNumber, String symbol, OrderSide side,
                                           String organizationNumber) { }

    private BrokerQueryResponse get(String path, String trId, MultiValueMap<String, String> params,
                                   String continuation, KisProperties.Account credentials) {
        var uri = UriComponentsBuilder.fromUriString(KisEnvironment.REAL.baseUrl() + path);
        params.forEach((key, values) -> values.forEach(value -> uri.queryParam(key, value)));
        for (int attempt = 0; attempt <= MAX_TRANSIENT_READ_RETRIES; attempt++) {
            try {
                rateLimiter.acquire(KisEnvironment.REAL, credentials.appKey(), properties.minRequestInterval());
                var response = http.get().uri(uri.build().encode().toUri())
                        .header("authorization", "Bearer " + tokenProvider.accessToken(KisEnvironment.REAL))
                        .header("appkey", credentials.appKey()).header("appsecret", credentials.appSecret())
                        .header("tr_id", trId).header("custtype", "P").header("tr_cont", continuation)
                        .accept(MediaType.APPLICATION_JSON).retrieve().toEntity(JsonNode.class);
                JsonNode body = response.getBody();
                if (attempt < MAX_TRANSIENT_READ_RETRIES && isTransientKisReadError(body)) {
                    log.warn("Transient KIS REAL read response; retrying once (path={}, code={}).",
                            path, safeCode(body.path("msg_cd").asText("unknown")));
                    waitBeforeReadRetry();
                    continue;
                }
                return new BrokerQueryResponse(body, response.getHeaders().getFirst("tr_cont"));
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE,
                        "KIS real order query was interrupted.");
            } catch (KisApiException exception) {
                throw exception;
            } catch (ResourceAccessException exception) {
                if (attempt < MAX_TRANSIENT_READ_RETRIES) {
                    log.warn("Transient KIS REAL read transport failure; retrying once (path={}).", path);
                    waitBeforeReadRetry();
                    continue;
                }
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS real order reconciliation transport failed.");
            } catch (RestClientResponseException exception) {
                int status = exception.getStatusCode().value();
                if (status == HttpStatus.TOO_MANY_REQUESTS.value()) {
                    throw new KisApiException(HttpStatus.TOO_MANY_REQUESTS,
                            "KIS real order query was rate limited.");
                }
                String code = safeCode(KisResponseCodes.fromErrorMessage(exception.getResponseBodyAsString(), "unknown"));
                if (attempt < MAX_TRANSIENT_READ_RETRIES
                        && (isTransientHttpStatus(status) || isTransientKisReadCode(code))) {
                    log.warn("Transient KIS REAL read HTTP failure; retrying once (path={}, status={}, code={}).",
                            path, status, code);
                    waitBeforeReadRetry();
                    continue;
                }
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS real order reconciliation request failed.");
            }
        }
        throw new KisApiException(HttpStatus.BAD_GATEWAY,
                "KIS real order reconciliation failed after one transient retry.");
    }

    private static boolean isTransientKisReadError(JsonNode body) {
        if (body == null || "0".equals(body.path("rt_cd").asText())) return false;
        return isTransientKisReadCode(safeCode(body.path("msg_cd").asText("unknown")));
    }

    private static boolean isTransientKisReadCode(String code) {
        return "EGW00001".equals(code) || "EGW00002".equals(code);
    }

    private static boolean isTransientHttpStatus(int status) {
        return status == 500 || status == 502 || status == 503 || status == 504;
    }

    private static void waitBeforeReadRetry() {
        try {
            Thread.sleep(500);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS real order query retry was interrupted.");
        }
    }

    private static Optional<KisBrokerOrderSnapshot> parseSnapshot(JsonNode row, KisOrderIntent intent) {
        String[] filledFields = {"tot_ccld_qty", "ft_ccld_qty", "ccld_qty", "filled_qty"};
        String[] remainingFields = {"rmn_qty", "nccs_qty", "unfilled_qty", "remaining_qty"};
        if (hasMalformedInteger(row, filledFields) || hasMalformedInteger(row, remainingFields)) return Optional.empty();
        Integer filled = integer(row, filledFields);
        Integer remaining = integer(row, remainingFields);
        if (filled == null && remaining == null) return Optional.empty();
        if (filled == null) filled = Math.max(0, intent.getQuantity() - remaining);
        if (remaining == null) remaining = Math.max(0, intent.getQuantity() - filled);
        String[] rejectedFields = {"rjct_qty", "rejected_qty"};
        ParsedFlag cancellationFlag = parseFlag(row, "cncl_yn", "cncl_cfm_yn", "cancel_yn");
        if (!cancellationFlag.valid() || hasMalformedInteger(row, rejectedFields)) return Optional.empty();
        Integer rejectedQuantity = integer(row, rejectedFields);
        if (rejectedQuantity != null && rejectedQuantity < 0) return Optional.empty();
        BigDecimal average = decimal(row, "avg_prvs", "ft_ccld_unpr3", "avg_ccld_unpr");
        return Optional.of(new KisBrokerOrderSnapshot(filled, remaining, average, Boolean.TRUE.equals(cancellationFlag.value()),
                rejectedQuantity != null && rejectedQuantity > 0));
    }

    private record ParsedFlag(boolean valid, Boolean value) { }

    private static ParsedFlag parseFlag(JsonNode row, String... fields) {
        Boolean parsed = null;
        for (String field : fields) {
            JsonNode value = row.path(field);
            if (value.isMissingNode() || value.isNull()) continue;
            if (!value.isTextual()) return new ParsedFlag(false, null);
            String text = value.asText().trim();
            if (text.isBlank()) continue;
            Boolean candidate;
            if ("Y".equalsIgnoreCase(text)) candidate = true;
            else if ("N".equalsIgnoreCase(text)) candidate = false;
            else return new ParsedFlag(false, null);
            if (parsed != null && !parsed.equals(candidate)) return new ParsedFlag(false, null);
            parsed = candidate;
        }
        return new ParsedFlag(true, parsed);
    }

    private static Integer integer(JsonNode row, String... fields) {
        String value = firstText(row, fields);
        try { return value.isBlank() ? null : Integer.parseInt(value); }
        catch (NumberFormatException ignored) { return null; }
    }

    private static boolean hasMalformedInteger(JsonNode row, String... fields) {
        for (String field : fields) {
            JsonNode value = row.path(field);
            if (value.isMissingNode() || value.isNull()) continue;
            if (!value.isNumber() && !value.isTextual()) return true;
            if (value.asText("").isBlank()) continue;
            try { Integer.parseInt(value.asText()); }
            catch (NumberFormatException ignored) { return true; }
        }
        return false;
    }

    private static BigDecimal decimal(JsonNode row, String... fields) {
        String value = firstText(row, fields);
        try { return value.isBlank() ? null : new BigDecimal(value); }
        catch (NumberFormatException ignored) { return null; }
    }

    private static String firstText(JsonNode row, String... fields) {
        for (String field : fields) {
            String value = row.path(field).asText("");
            if (!value.isBlank()) return value;
        }
        return "";
    }

    private record BrokerQueryResponse(JsonNode body, String continuation) { }

    private JsonNode post(String path, String trId, Map<String, String> body,
                          OrderRequest approvedRequest, String exchange, KisLiveRiskDecision riskDecision) {
        KisProperties.Account credentials = properties.credentials(KisEnvironment.REAL);
        String accessToken = tokenProvider.accessToken(KisEnvironment.REAL); // Token failure occurs before any order request.
        if (approvedRequest != null && (riskDecision == null || !riskDecision.approves(approvedRequest, exchange))) {
            throw riskApprovalRequired();
        }
        try {
            throttle();
            if (approvedRequest != null && (riskDecision == null || !riskDecision.approves(approvedRequest, exchange))) {
                throw riskApprovalRequired();
            }
            JsonNode response = http.post().uri(KisEnvironment.REAL.baseUrl() + path)
                    .contentType(MediaType.APPLICATION_JSON).accept(MediaType.APPLICATION_JSON)
                    .header("authorization", "Bearer " + accessToken)
                    .header("appkey", credentials.appKey()).header("appsecret", credentials.appSecret())
                    .header("tr_id", trId).header("custtype", "P").body(body)
                    .retrieve().body(JsonNode.class);
            if (response == null || !"0".equals(response.path("rt_cd").asText())) {
                String code = response == null ? "unknown" : safeCode(response.path("msg_cd").asText("unknown"));
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS rejected the real order request (code " + code + ").");
            }
            return response;
        } catch (KisApiException | KisOrderOutcomeUnknownException exception) {
            throw exception;
        } catch (ResourceAccessException | RestClientResponseException exception) {
            throw new KisOrderOutcomeUnknownException(
                    "KIS real order outcome is unknown; reconcile broker orders before taking further action.", exception);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS request was interrupted before submission.");
        }
    }

    private static IllegalStateException riskApprovalRequired() {
        return new IllegalStateException("A fresh live risk approval for this exact order is required before a real order.");
    }

    private void requireRealOrdersEnabled() {
        if (!"real".equalsIgnoreCase(safety.mode()) || !safety.liveTradingEnabled() || !safety.realOrdersEnabled()) {
            throw new IllegalStateException("Real KIS orders require TRADING_MODE=real, LIVE_TRADING_ENABLED=true, and KIS_REAL_ORDERS_ENABLED=true.");
        }
        if (!properties.accountReadEnabled() || !properties.realAccountReadEnabled()) {
            throw new IllegalStateException("Real KIS orders require both account-read switches for preflight.");
        }
        if (!properties.credentials(KisEnvironment.REAL).configured()) {
            throw new IllegalStateException("Real KIS credentials are not configured.");
        }
    }

    private void requireRealAccountReadEnabled() {
        if (!properties.accountReadEnabled() || !properties.realAccountReadEnabled()) {
            throw new IllegalStateException("Real KIS reconciliation requires both account-read switches.");
        }
        if (!properties.credentials(KisEnvironment.REAL).configured()) {
            throw new IllegalStateException("Real KIS credentials are not configured.");
        }
    }

    private void throttle() throws InterruptedException {
        rateLimiter.acquire(KisEnvironment.REAL, properties.credentials(KisEnvironment.REAL).appKey(),
                properties.minRequestInterval());
    }

    private static void validate(Market market, String symbol, OrderSide side, int quantity,
                                 BigDecimal price, Currency currency) {
        if (market == null || side == null || quantity <= 0 || price == null || price.signum() <= 0 || currency == null) {
            throw new IllegalArgumentException("A valid market, side, quantity, price, and currency are required.");
        }
        KisDomesticOrderPriceValidator.validate(market, price);
        String normalized = symbol == null ? "" : symbol.trim().toUpperCase(Locale.ROOT);
        if (market == Market.KR && (!normalized.matches("\\d{6}") || currency != Currency.KRW)) {
            throw new IllegalArgumentException("Domestic KIS orders require a six-digit symbol and KRW.");
        }
        if (market == Market.US && (!normalized.matches("[A-Z][A-Z0-9.\\-]{0,14}") || currency != Currency.USD)) {
            throw new IllegalArgumentException("US KIS orders require a valid symbol and USD.");
        }
    }

    private static String usExchange(String exchange) {
        return switch (exchange == null ? "" : exchange.trim().toUpperCase(Locale.ROOT)) {
            case "NAS", "NASD" -> "NASD";
            case "NYS", "NYSE" -> "NYSE";
            case "AMS", "AMEX" -> "AMEX";
            default -> throw new IllegalArgumentException("US exchange must be NAS, NYS, or AMS.");
        };
    }

    private static String text(JsonNode node, String field) {
        return node.path(field).asText("");
    }

    private static String safeCode(String code) {
        return code != null && code.matches("[A-Za-z0-9_-]{1,32}") ? code : "unknown";
    }

}
