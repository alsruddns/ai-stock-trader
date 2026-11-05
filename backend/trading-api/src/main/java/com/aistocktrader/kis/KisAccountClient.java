package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.util.UriComponentsBuilder;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** KIS read-only account adapter. No order or amend/cancel routes exist in this client. */
@Component
public class KisAccountClient {
    private static final String TOKEN_PATH = "/oauth2/tokenP";
    private final KisProperties properties;
    private final RestClient http;
    private final ObjectMapper mapper;
    private final Map<KisEnvironment, CachedToken> tokens = new ConcurrentHashMap<>();
    private final Map<KisEnvironment, Object> tokenLocks = new ConcurrentHashMap<>();
    private final Map<KisEnvironment, Object> rateLocks = new ConcurrentHashMap<>();
    private final Map<KisEnvironment, Long> lastRequestAt = new ConcurrentHashMap<>();

    public KisAccountClient(KisProperties properties, RestClient.Builder builder, ObjectMapper mapper) {
        this.properties = properties;
        this.http = builder.build();
        this.mapper = mapper;
    }

    public KisAccountSnapshot getBalance(KisEnvironment environment, Market market,
                                         String symbol, BigDecimal unitPrice, String exchange) {
        requireEnabled(environment);
        String account = properties.credentials(environment).accountNumber();
        String cano = account.substring(0, 8);
        String product = account.substring(8, 10);
        if (market == Market.KR) {
            JsonNode balance = getPaginatedBalance(environment, "/uapi/domestic-stock/v1/trading/inquire-balance",
                    environment == KisEnvironment.PAPER ? "VTTC8434R" : "TTTC8434R",
                    Map.ofEntries(Map.entry("CANO", cano), Map.entry("ACNT_PRDT_CD", product),
                            Map.entry("AFHR_FLPR_YN", "N"), Map.entry("OFL_YN", ""),
                            Map.entry("INQR_DVSN", "01"), Map.entry("UNPR_DVSN", "01"),
                            Map.entry("FUND_STTL_ICLD_YN", "N"), Map.entry("FNCG_AMT_AUTO_RDPT_YN", "N"),
                            Map.entry("PRCS_DVSN", "00"), Map.entry("CTX_AREA_FK100", ""),
                            Map.entry("CTX_AREA_NK100", "")), "output1", "output2",
                    "CTX_AREA_FK100", "CTX_AREA_NK100");
            JsonNode summary = firstOrSelf(balance.path("output2"));
            List<KisAccountSnapshot.Position> positions = mapPositions(balance.path("output1"), market);
            BigDecimal orderable = firstAmount(summary, "ord_psbl_cash", "nrcvb_buy_amt");
            if (symbol != null && unitPrice != null) {
                JsonNode buyingPower = get(environment, "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                        environment == KisEnvironment.PAPER ? "VTTC8908R" : "TTTC8908R",
                        Map.of("CANO", cano, "ACNT_PRDT_CD", product, "PDNO", symbol,
                                "ORD_UNPR", unitPrice.toPlainString(), "ORD_DVSN", "00",
                                "CMA_EVLU_AMT_ICLD_YN", "N", "OVRS_ICLD_YN", "N"));
                orderable = firstAmount(firstOrSelf(buyingPower.path("output")), "ord_psbl_cash", "nrcvb_buy_amt");
            }
            return new KisAccountSnapshot(environment, market, "KRW",
                    firstAmount(summary, "dnca_tot_amt", "nass_amt"), orderable, positions, Instant.now(),
                    "KIS_READ_ONLY_REST");
        }

        String validExchange = normalizeUsExchange(exchange);
        JsonNode balance = getPaginatedBalance(environment, "/uapi/overseas-stock/v1/trading/inquire-balance",
                environment == KisEnvironment.PAPER ? "VTTS3012R" : "TTTS3012R",
                Map.of("CANO", cano, "ACNT_PRDT_CD", product, "OVRS_EXCG_CD", validExchange,
                        "TR_CRCY_CD", "USD", "CTX_AREA_FK200", "", "CTX_AREA_NK200", ""),
                "output2", "output1", "CTX_AREA_FK200", "CTX_AREA_NK200");
        JsonNode cashResponse = get(environment, "/uapi/overseas-stock/v1/trading/inquire-present-balance",
                environment == KisEnvironment.PAPER ? "VTRP6504R" : "CTRP6504R",
                Map.of("CANO", cano, "ACNT_PRDT_CD", product, "WCRC_FRCR_DVSN_CD", "02",
                        "NATN_CD", "840", "TR_MKET_CD", "00", "INQR_DVSN_CD", "00"));
        JsonNode cashSummary = findCurrency(cashResponse.path("output2"), "USD");
        BigDecimal cash = firstAmount(cashSummary, "frcr_drwg_psbl_amt_1", "frcr_dncl_amt_2");
        BigDecimal orderable = firstAmount(cashSummary, "frcr_ord_psbl_amt1", "ord_psbl_frcr_amt");
        if (symbol != null && unitPrice != null) {
            JsonNode buyingPower = get(environment, "/uapi/overseas-stock/v1/trading/inquire-psamount",
                    environment == KisEnvironment.PAPER ? "VTTS3007R" : "TTTS3007R",
                    Map.of("CANO", cano, "ACNT_PRDT_CD", product, "OVRS_EXCG_CD", validExchange,
                            "OVRS_ORD_UNPR", unitPrice.toPlainString(), "ITEM_CD", symbol));
            JsonNode output = firstOrSelf(buyingPower.path("output"));
            orderable = firstAmount(output, "ord_psbl_frcr_amt", "frcr_ord_psbl_amt1", "ovrs_ord_psbl_amt");
        }
        return new KisAccountSnapshot(environment, market, "USD", cash, orderable,
                mapPositions(balance.path("output2"), market), Instant.now(), "KIS_READ_ONLY_REST");
    }

    private JsonNode get(KisEnvironment environment, String path, String trId, Map<String, String> params) {
        return getPage(environment, path, trId, params, "").body();
    }

    private JsonNode getPaginatedBalance(KisEnvironment environment, String path, String trId,
                                         Map<String, String> initialParams, String positionsField,
                                         String summaryField, String fkField, String nkField) {
        Map<String, String> params = new HashMap<>(initialParams);
        ArrayNode positions = mapper.createArrayNode();
        JsonNode summary = mapper.createObjectNode();
        String continuation = "";
        for (int pageNumber = 0; pageNumber < 20; pageNumber++) {
            ApiPage page = getPage(environment, path, trId, params, continuation);
            JsonNode payload = page.body();
            if (pageNumber == 0) summary = firstOrSelf(payload.path(summaryField));
            JsonNode rows = payload.path(positionsField);
            if (rows.isArray()) rows.forEach(positions::add);
            else if (rows.isObject()) positions.add(rows);

            String responseContinuation = page.continuation();
            if (!"M".equalsIgnoreCase(responseContinuation) && !"F".equalsIgnoreCase(responseContinuation)) {
                ObjectNode merged = mapper.createObjectNode();
                merged.set(positionsField, positions);
                merged.set(summaryField, summary);
                return merged;
            }
            if (pageNumber == 19) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS account balance exceeded the supported pagination limit.");
            }
            params.put(fkField, payload.path(fkField.toLowerCase(java.util.Locale.ROOT)).asText(""));
            params.put(nkField, payload.path(nkField.toLowerCase(java.util.Locale.ROOT)).asText(""));
            continuation = "N";
        }
        throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS account balance pagination did not complete.");
    }

    private ApiPage getPage(KisEnvironment environment, String path, String trId,
                            Map<String, String> params, String continuation) {
        KisProperties.Account credentials = properties.credentials(environment);
        try {
            throttle(environment);
            UriComponentsBuilder uri = UriComponentsBuilder.fromUriString(environment.baseUrl() + path);
            params.forEach(uri::queryParam);
            var request = http.get().uri(uri.build().encode().toUri())
                    .accept(MediaType.APPLICATION_JSON)
                    .header("authorization", "Bearer " + accessToken(environment))
                    .header("appkey", credentials.appKey())
                    .header("appsecret", credentials.appSecret())
                    .header("tr_id", trId)
                    .header("custtype", "P");
            if (!continuation.isBlank()) request.header("tr_cont", continuation);
            var entity = request.retrieve().toEntity(JsonNode.class);
            JsonNode response = entity.getBody();
            if (response == null || !"0".equals(response.path("rt_cd").asText())) {
                throw brokerError(response);
            }
            return new ApiPage(response, entity.getHeaders().getFirst("tr_cont"));
        } catch (KisApiException ex) {
            throw ex;
        } catch (RestClientResponseException ex) {
            HttpStatus status = ex.getStatusCode().value() == 429 ? HttpStatus.TOO_MANY_REQUESTS
                    : HttpStatus.BAD_GATEWAY;
            throw new KisApiException(status, status == HttpStatus.TOO_MANY_REQUESTS
                    ? "KIS rate limit reached; wait before requesting again."
                    : "KIS account request failed; check the environment and KIS status.");
        } catch (ResourceAccessException ex) {
            boolean timeout = causedByTimeout(ex);
            throw new KisApiException(timeout ? HttpStatus.GATEWAY_TIMEOUT : HttpStatus.BAD_GATEWAY,
                    timeout ? "KIS account request timed out." : "KIS account service is unreachable.");
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS request was interrupted.");
        } catch (RuntimeException ex) {
            throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS account request failed; check the environment and KIS status.");
        }
    }

    private String accessToken(KisEnvironment environment) {
        CachedToken cached = tokens.get(environment);
        if (cached != null && cached.expiresAt().isAfter(Instant.now().plusSeconds(300))) return cached.value();
        synchronized (tokenLocks.computeIfAbsent(environment, ignored -> new Object())) {
            cached = tokens.get(environment);
            if (cached != null && cached.expiresAt().isAfter(Instant.now().plusSeconds(300))) return cached.value();
            KisProperties.Account credentials = properties.credentials(environment);
            try {
                throttle(environment);
                JsonNode payload = http.post().uri(environment.baseUrl() + TOKEN_PATH)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(Map.of("grant_type", "client_credentials", "appkey", credentials.appKey(),
                                "appsecret", credentials.appSecret()))
                        .retrieve().body(JsonNode.class);
                String token = payload == null ? "" : payload.path("access_token").asText();
                String expiry = payload == null ? "" : payload.path("access_token_token_expired").asText();
                if (token.isBlank() || expiry.isBlank()) {
                    throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS authentication response was incomplete.");
                }
                Instant expiresAt = LocalDateTime.parse(expiry.replace(" ", "T"))
                        .toInstant(ZoneOffset.ofHours(9));
                tokens.put(environment, new CachedToken(token, expiresAt));
                return token;
            } catch (KisApiException ex) {
                throw ex;
            } catch (RestClientResponseException ex) {
                throw new KisApiException(ex.getStatusCode().value() == 429
                        ? HttpStatus.TOO_MANY_REQUESTS : HttpStatus.BAD_GATEWAY,
                        "KIS authentication failed; check the selected environment and credentials.");
            } catch (ResourceAccessException ex) {
                boolean timeout = causedByTimeout(ex);
                throw new KisApiException(timeout ? HttpStatus.GATEWAY_TIMEOUT : HttpStatus.BAD_GATEWAY,
                        timeout ? "KIS authentication request timed out." : "KIS authentication service is unreachable.");
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS authentication was interrupted.");
            } catch (RuntimeException ex) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS authentication failed; check the selected environment and credentials.");
            }
        }
    }

    private void requireEnabled(KisEnvironment environment) {
        if (!properties.accountReadEnabled()
                || (environment == KisEnvironment.PAPER && !properties.paperAccountReadEnabled())
                || (environment == KisEnvironment.REAL && !properties.realAccountReadEnabled())) {
            throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS account reads are disabled.");
        }
        if (!properties.credentials(environment).configured()) {
            throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE,
                    "KIS credentials are not configured for the selected environment.");
        }
    }

    private void throttle(KisEnvironment environment) throws InterruptedException {
        long minimum = properties.minRequestInterval().toMillis();
        if (minimum <= 0) return;
        synchronized (rateLocks.computeIfAbsent(environment, ignored -> new Object())) {
            long now = System.currentTimeMillis();
            long previous = lastRequestAt.getOrDefault(environment, 0L);
            long delay = minimum - (now - previous);
            if (delay > 0) Thread.sleep(delay);
            lastRequestAt.put(environment, System.currentTimeMillis());
        }
    }

    private KisApiException brokerError(JsonNode payload) {
        String code = payload == null ? "unknown" : payload.path("msg_cd").asText("unknown");
        if (!code.matches("[A-Za-z0-9_-]{1,32}")) code = "unknown";
        if (code.toLowerCase(java.util.Locale.ROOT).contains("egw00201")) {
            return new KisApiException(HttpStatus.TOO_MANY_REQUESTS, "KIS rate limit reached; wait before requesting again.");
        }
        return new KisApiException(HttpStatus.BAD_GATEWAY, "KIS rejected the account request (code " + code + ").");
    }

    private String normalizeUsExchange(String exchange) {
        String value = exchange == null || exchange.isBlank() ? "NASD" : exchange.trim().toUpperCase(java.util.Locale.ROOT);
        if (!List.of("NASD", "NYSE", "AMEX").contains(value)) {
            throw new KisApiException(HttpStatus.BAD_REQUEST, "US exchange must be NASD, NYSE, or AMEX.");
        }
        return value;
    }

    private List<KisAccountSnapshot.Position> mapPositions(JsonNode rows, Market market) {
        List<KisAccountSnapshot.Position> positions = new ArrayList<>();
        if (!rows.isArray()) return positions;
        for (JsonNode row : rows) {
            String symbol = value(row, market == Market.KR ? "pdno" : "ovrs_pdno");
            if (symbol.isBlank()) continue;
            positions.add(new KisAccountSnapshot.Position(symbol,
                    value(row, market == Market.KR ? "prdt_name" : "ovrs_item_name"),
                    amount(row, market == Market.KR ? "hldg_qty" : "ovrs_cblc_qty"),
                    amount(row, market == Market.KR ? "ord_psbl_qty" : "ord_psbl_qty"),
                    amount(row, market == Market.KR ? "pchs_avg_pric" : "pchs_avg_pric"),
                    amount(row, market == Market.KR ? "prpr" : "now_pric2"),
                    amount(row, market == Market.KR ? "evlu_amt" : "ovrs_stck_evlu_amt"),
                    amount(row, market == Market.KR ? "evlu_pfls_amt" : "frcr_evlu_pfls_amt"),
                    amount(row, market == Market.KR ? "evlu_pfls_rt" : "evlu_pfls_rt")));
        }
        return positions;
    }

    private JsonNode firstOrSelf(JsonNode node) {
        return node.isArray() ? node.isEmpty() ? mapper.createObjectNode() : node.get(0) : node;
    }

    private JsonNode findCurrency(JsonNode rows, String currency) {
        if (rows.isArray()) {
            for (JsonNode row : rows) if (currency.equalsIgnoreCase(value(row, "crcy_cd"))) return row;
        }
        return firstOrSelf(rows);
    }

    private BigDecimal firstAmount(JsonNode node, String... fields) {
        for (String field : fields) {
            BigDecimal amount = amount(node, field);
            if (amount != null) return amount;
        }
        return null;
    }

    private BigDecimal amount(JsonNode node, String field) {
        String value = value(node, field);
        if (value.isBlank()) return null;
        try { return new BigDecimal(value); }
        catch (NumberFormatException ex) { throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS returned an invalid account amount."); }
    }

    private String value(JsonNode node, String field) {
        JsonNode value = node.path(field);
        return value.isMissingNode() || value.isNull() ? "" : value.asText();
    }

    private boolean causedByTimeout(Throwable exception) {
        Throwable current = exception;
        while (current != null) {
            if (current instanceof java.net.http.HttpTimeoutException
                    || current instanceof java.net.SocketTimeoutException) return true;
            current = current.getCause();
        }
        return false;
    }

    private record CachedToken(String value, Instant expiresAt) {}
    private record ApiPage(JsonNode body, String continuation) {}
}
