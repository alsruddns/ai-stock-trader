package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.config.TradingSafetyProperties;
import com.fasterxml.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.stereotype.Component;
import org.springframework.web.util.UriComponentsBuilder;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.LocalDate;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/** Internal paper-only KIS order adapter. No public trading controller uses it in this phase. */
@Component
public class KisOrderClient {
    private static final Logger log = LoggerFactory.getLogger(KisOrderClient.class);
    private static final int MAX_TRANSIENT_READ_RETRIES = 1;
    private static final String DOMESTIC_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash";
    private static final String OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order";
    private static final String OVERSEAS_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl";

    private final KisProperties properties;
    private final KisOrderProperties orderProperties;
    private final TradingSafetyProperties safety;
    private final RestClient http;
    private final KisRestRateLimiter rateLimiter;
    private final KisAccessTokenProvider tokenProvider;

    public KisOrderClient(KisProperties properties, KisOrderProperties orderProperties,
                          TradingSafetyProperties safety, RestClient.Builder builder) {
        this(properties, orderProperties, safety, builder, new KisRestRateLimiter());
    }

    public KisOrderClient(KisProperties properties, KisOrderProperties orderProperties,
                          TradingSafetyProperties safety, RestClient.Builder builder,
                          KisRestRateLimiter rateLimiter) {
        this(properties, orderProperties, safety, builder, rateLimiter,
                new KisAccessTokenProvider(properties, builder, rateLimiter));
    }

    @Autowired
    public KisOrderClient(KisProperties properties, KisOrderProperties orderProperties,
                          TradingSafetyProperties safety, RestClient.Builder builder,
                          KisRestRateLimiter rateLimiter, KisAccessTokenProvider tokenProvider) {
        this.properties = properties;
        this.orderProperties = orderProperties;
        this.safety = safety;
        this.http = builder.build();
        this.rateLimiter = rateLimiter;
        this.tokenProvider = tokenProvider;
    }

    public KisOrderReceipt placePaperLimitOrder(Market market, String symbol, OrderSide side,
                                                 int quantity, BigDecimal price, Currency currency,
                                                 String exchange) {
        requirePaperOrdersEnabled();
        validate(market, symbol, side, quantity, price, currency);
        BigDecimal notional = price.multiply(BigDecimal.valueOf(quantity));
        BigDecimal maximum = currency == Currency.KRW ? safety.maxOrderNotionalKrw() : safety.maxOrderNotionalUsd();
        if (notional.compareTo(maximum) > 0) {
            throw new IllegalArgumentException("KIS paper order exceeds the configured notional limit.");
        }
        KisProperties.Account credentials = properties.credentials(KisEnvironment.PAPER);
        String account = credentials.accountNumber();
        String path;
        String trId;
        Map<String, String> body;
        if (market == Market.KR) {
            path = DOMESTIC_ORDER_PATH;
            trId = side == OrderSide.BUY ? "VTTC0012U" : "VTTC0011U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "PDNO", symbol, "ORD_DVSN", "00", "ORD_QTY", Integer.toString(quantity),
                    "ORD_UNPR", price.toPlainString(), "EXCG_ID_DVSN_CD", "KRX",
                    "SLL_TYPE", side == OrderSide.SELL ? "01" : "", "CNDT_PRIC", "");
        } else {
            String kisExchange = usExchange(exchange);
            path = OVERSEAS_ORDER_PATH;
            trId = side == OrderSide.BUY ? "VTTT1002U" : "VTTT1001U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "OVRS_EXCG_CD", kisExchange, "PDNO", symbol.toUpperCase(Locale.ROOT),
                    "ORD_DVSN", "00", "ORD_QTY", Integer.toString(quantity),
                    "OVRS_ORD_UNPR", price.toPlainString(), "SLL_TYPE", side == OrderSide.SELL ? "00" : "",
                    "ORD_SVR_DVSN_CD", "0");
        }
        JsonNode result = post(path, trId, body);
        JsonNode output = result.path("output");
        return new KisOrderReceipt("paper", market.name(), symbol.toUpperCase(Locale.ROOT), side.name(),
                text(output, "ODNO"), text(output, "KRX_FWDG_ORD_ORGNO"),
                text(result, "msg_cd"), Instant.now());
    }

    public void cancelPaperOrder(Market market, String symbol, String brokerOrderNumber,
                                  String brokerOrganizationNumber, int remainingQuantity,
                                  String exchange) {
        requirePaperOrdersEnabled();
        if (market == null || symbol == null || symbol.isBlank() || brokerOrderNumber == null || brokerOrderNumber.isBlank()
                || remainingQuantity <= 0) {
            throw new IllegalArgumentException("A symbol, original broker order, and positive reconciled remaining quantity are required.");
        }
        if (market == Market.KR) {
            throw new KisApiException(org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE,
                    "Domestic PAPER cancellation is locked until KIS documents a supported mock cancel-quantity preflight (code PAPER_CANCEL_UNSUPPORTED).");
        }
        String account = properties.credentials(KisEnvironment.PAPER).accountNumber();
        Map<String, String> body = Map.ofEntries(Map.entry("CANO", account.substring(0, 8)),
                Map.entry("ACNT_PRDT_CD", account.substring(8)), Map.entry("OVRS_EXCG_CD", usExchange(exchange)),
                Map.entry("PDNO", symbol.toUpperCase(Locale.ROOT)), Map.entry("ORGN_ODNO", brokerOrderNumber),
                Map.entry("RVSE_CNCL_DVSN_CD", "02"), Map.entry("ORD_QTY", "0"),
                Map.entry("OVRS_ORD_UNPR", "0"), Map.entry("QTY_ALL_ORD_YN", "Y"),
                Map.entry("MGCO_APTM_ODNO", ""), Map.entry("ORD_SVR_DVSN_CD", "0"));
        post(OVERSEAS_CANCEL_PATH, "VTTT1004U", body);
    }

    public Optional<KisBrokerOrderSnapshot> reconcilePaperOrder(KisOrderIntent intent) {
        requirePaperReconciliationReadEnabled();
        if (intent.getEnvironment() != KisEnvironment.PAPER) {
            throw new IllegalStateException("Paper reconciliation cannot access a real-environment order intent.");
        }
        KisProperties.Account credentials = properties.credentials(KisEnvironment.PAPER);
        String account = credentials.accountNumber();
        LocalDate orderDate = intent.getCreatedAt().atZone(intent.getMarket() == Market.KR
                ? ZoneId.of("Asia/Seoul") : ZoneId.of("America/New_York")).toLocalDate();
        String date = orderDate.format(DateTimeFormatter.BASIC_ISO_DATE);
        String path;
        String trId;
        MultiValueMap<String, String> params = new LinkedMultiValueMap<>();
        params.add("CANO", account.substring(0, 8));
        params.add("ACNT_PRDT_CD", account.substring(8));
        String continuation = "";
        String nextKey = "";
        String nextSearch = "";
        Set<List<String>> seenCursors = new HashSet<>();
        for (int page = 0; page < 10; page++) {
            if (intent.getMarket() == Market.KR) {
                path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld";
                trId = "VTTC0081R";
                params.set("INQR_STRT_DT", date); params.set("INQR_END_DT", date);
                params.set("SLL_BUY_DVSN_CD", "00"); params.set("PDNO", intent.getSymbol());
                params.set("CCLD_DVSN", "00"); params.set("INQR_DVSN", "00");
                params.set("INQR_DVSN_3", "00"); params.set("ORD_GNO_BRNO", "");
                params.set("ODNO", intent.getBrokerOrderNumber()); params.set("INQR_DVSN_1", "");
                params.set("EXCG_ID_DVSN_CD", "KRX");
                params.set("CTX_AREA_FK100", nextSearch); params.set("CTX_AREA_NK100", nextKey);
            } else {
                path = "/uapi/overseas-stock/v1/trading/inquire-ccnl";
                trId = "VTTS3035R";
                params.set("PDNO", ""); params.set("ORD_STRT_DT", date); params.set("ORD_END_DT", date);
                params.set("SLL_BUY_DVSN", "00"); params.set("CCLD_NCCS_DVSN", "00");
                params.set("OVRS_EXCG_CD", ""); params.set("SORT_SQN", "DS");
                params.set("ORD_DT", ""); params.set("ORD_GNO_BRNO", ""); params.set("ODNO", "");
                params.set("CTX_AREA_FK200", nextSearch); params.set("CTX_AREA_NK200", nextKey);
            }
            BrokerQueryResponse queryResponse = getPaper(path, trId, params, continuation, credentials);
            JsonNode response = queryResponse.body();
            if (!"0".equals(response.path("rt_cd").asText())) {
                throw new KisApiException(org.springframework.http.HttpStatus.BAD_GATEWAY,
                        "KIS order reconciliation failed (code " + safeCode(response.path("msg_cd").asText("unknown")) + ").");
            }
            JsonNode rows = response.path(intent.getMarket() == Market.KR ? "output1" : "output");
            if (rows.isArray()) {
                for (JsonNode row : rows) {
                    if (intent.getBrokerOrderNumber().equals(firstText(row, "odno", "ODNO"))) {
                        if (!KisBrokerOrderSnapshotValidator.matchesBrokerIdentity(row, intent)) return Optional.empty();
                        return parseSnapshot(row, intent);
                    }
                }
            }
            String headerContinuation = queryResponse.continuation();
            if (!"M".equalsIgnoreCase(headerContinuation) && !"F".equalsIgnoreCase(headerContinuation)) break;
            if (intent.getMarket() == Market.KR) {
                nextSearch = response.path("ctx_area_fk100").asText("");
                nextKey = response.path("ctx_area_nk100").asText("");
            } else {
                nextSearch = response.path("ctx_area_fk200").asText("");
                nextKey = response.path("ctx_area_nk200").asText("");
            }
            if (nextSearch.isBlank() || nextKey.isBlank()
                    || !seenCursors.add(List.of(nextSearch, nextKey))) {
                // Missing or repeated page cursors cannot prove an order state. Return no
                // snapshot so callers keep the intent unresolved and never act on stale rows.
                return Optional.empty();
            }
            continuation = "N";
        }
        return Optional.empty();
    }

    private BrokerQueryResponse getPaper(String path, String trId, MultiValueMap<String, String> params,
                              String continuation, KisProperties.Account credentials) {
        var uriBuilder = UriComponentsBuilder.fromUriString(KisEnvironment.PAPER.baseUrl() + path);
        params.forEach((key, values) -> values.forEach(value -> uriBuilder.queryParam(key, value)));
        for (int attempt = 0; attempt <= MAX_TRANSIENT_READ_RETRIES; attempt++) {
            try {
                rateLimiter.acquire(KisEnvironment.PAPER, credentials.appKey(), properties.minRequestInterval());
                var response = http.get().uri(uriBuilder.build().encode().toUri())
                        .header("authorization", "Bearer " + tokenProvider.accessToken(KisEnvironment.PAPER))
                        .header("appkey", credentials.appKey()).header("appsecret", credentials.appSecret())
                        .header("tr_id", trId).header("custtype", "P").header("tr_cont", continuation)
                        .accept(MediaType.APPLICATION_JSON).retrieve().toEntity(JsonNode.class);
                JsonNode body = response.getBody();
                String code = body == null ? "unknown" : safeCode(body.path("msg_cd").asText("unknown"));
                if (attempt < MAX_TRANSIENT_READ_RETRIES && isTransientKisReadError(body)) {
                    log.warn("Transient KIS PAPER read response; retrying once (path={}, code={}).", path, code);
                    waitBeforeReadRetry();
                    continue;
                }
                return new BrokerQueryResponse(body, response.getHeaders().getFirst("tr_cont"));
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS order query was interrupted.");
            } catch (ResourceAccessException exception) {
                if (attempt < MAX_TRANSIENT_READ_RETRIES) {
                    log.warn("Transient KIS PAPER read transport failure; retrying once (path={}).", path);
                    waitBeforeReadRetry();
                    continue;
                }
                throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS order query transport failed.");
            } catch (RestClientResponseException exception) {
                int status = exception.getStatusCode().value();
                if (status == HttpStatus.TOO_MANY_REQUESTS.value()) {
                    throw new KisApiException(HttpStatus.TOO_MANY_REQUESTS, "KIS order query was rate limited.");
                }
                if (attempt < MAX_TRANSIENT_READ_RETRIES && isTransientHttpStatus(status)) {
                    log.warn("Transient KIS PAPER read HTTP failure; retrying once (path={}, status={}).", path, status);
                    waitBeforeReadRetry();
                    continue;
                }
                throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS order query failed.");
            }
        }
        throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS order query failed after one transient retry.");
    }

    private static boolean isTransientKisReadError(JsonNode body) {
        if (body == null || "0".equals(body.path("rt_cd").asText())) return false;
        String code = safeCode(body.path("msg_cd").asText("unknown"));
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
            throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS order query retry was interrupted.");
        }
    }

    private record BrokerQueryResponse(JsonNode body, String continuation) { }

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

    private JsonNode post(String path, String trId, Map<String, String> body) {
        KisProperties.Account credentials = properties.credentials(KisEnvironment.PAPER);
        try {
            String bearer = tokenProvider.accessToken(KisEnvironment.PAPER);
            rateLimiter.acquire(KisEnvironment.PAPER, credentials.appKey(), properties.minRequestInterval());
            JsonNode response = http.post().uri(KisEnvironment.PAPER.baseUrl() + path)
                    .contentType(MediaType.APPLICATION_JSON).accept(MediaType.APPLICATION_JSON)
                    .header("authorization", "Bearer " + bearer)
                    .header("appkey", credentials.appKey()).header("appsecret", credentials.appSecret())
                    .header("tr_id", trId).header("custtype", "P").body(body).retrieve().body(JsonNode.class);
            if (response == null || !"0".equals(response.path("rt_cd").asText())) {
                String code = response == null ? "unknown" : response.path("msg_cd").asText("unknown");
                throw new KisApiException(org.springframework.http.HttpStatus.BAD_GATEWAY,
                        "KIS rejected the paper order (code " + safeCode(code) + ").");
            }
            return response;
        } catch (KisApiException ex) {
            throw ex;
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new KisApiException(org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE,
                    "KIS request was interrupted before submission.");
        } catch (ResourceAccessException ex) {
            throw new KisOrderOutcomeUnknownException("KIS paper order outcome is unknown; reconcile broker orders before taking further action.", ex);
        } catch (RestClientResponseException ex) {
            throw new KisOrderOutcomeUnknownException("KIS paper order outcome is unknown; reconcile broker orders before taking further action.", ex);
        }
    }

    private void requirePaperOrdersEnabled() {
        if (!orderProperties.paperEnabled()) throw new IllegalStateException("KIS paper orders are disabled.");
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("KIS paper orders are disabled outside the locked paper mode.");
        }
        if (!properties.credentials(KisEnvironment.PAPER).configured()) throw new IllegalStateException("KIS paper credentials are not configured.");
    }

    private void requirePaperReconciliationReadEnabled() {
        if (!properties.accountReadEnabled() || !properties.paperAccountReadEnabled()) {
            throw new IllegalStateException("KIS PAPER order reconciliation requires both account-read switches.");
        }
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("KIS PAPER order reconciliation is disabled outside PAPER mode.");
        }
        if (!properties.credentials(KisEnvironment.PAPER).configured()) {
            throw new IllegalStateException("KIS PAPER credentials are not configured.");
        }
    }

    private static void validate(Market market, String symbol, OrderSide side, int quantity, BigDecimal price, Currency currency) {
        if (market == null || side == null || quantity <= 0 || price == null || price.signum() <= 0 || currency == null) {
            throw new IllegalArgumentException("A valid market, side, quantity, price, and currency are required.");
        }
        KisDomesticOrderPriceValidator.validate(market, symbol, price);
        String normalized = symbol == null ? "" : symbol.trim().toUpperCase(Locale.ROOT);
        if (market == Market.KR && (!normalized.matches("\\d{6}") || currency != Currency.KRW)) {
            throw new IllegalArgumentException("Domestic KIS orders require a six-digit symbol and KRW.");
        }
        if (market == Market.US && (!normalized.matches("[A-Z][A-Z0-9.\\-]{0,14}") || currency != Currency.USD)) {
            throw new IllegalArgumentException("US KIS orders require a valid symbol and USD.");
        }
    }

    private static String usExchange(String exchange) {
        return switch (exchange == null ? "" : exchange.toUpperCase(Locale.ROOT)) {
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
        // Preserve only KIS error identifiers (for example EGW00002 or OPSQ2000),
        // not arbitrary response text such as the non-code value "rejected".
        return code.matches("(?i)[A-Z]{2,4}[0-9]{4,5}") ? code.toUpperCase(Locale.ROOT) : "unknown";
    }
}
