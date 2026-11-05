package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.OrderRequest;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.Locale;
import java.util.Map;

/** Real-account order adapter. Calls are impossible unless all production switches and read preflight gates are open. */
@Component
public class KisRealOrderClient {
    private static final String DOMESTIC_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash";
    private static final String OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order";
    private static final String DOMESTIC_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl";
    private static final String OVERSEAS_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl";

    private final KisProperties properties;
    private final TradingSafetyProperties safety;
    private final RestClient http;
    private final Object tokenLock = new Object();
    private final Object rateLock = new Object();
    private volatile CachedToken token;
    private long lastRequestAt;

    public KisRealOrderClient(KisProperties properties, TradingSafetyProperties safety, RestClient.Builder builder) {
        this.properties = properties;
        this.safety = safety;
        this.http = builder.build();
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
                || brokerOrderNumber.isBlank() || remainingQuantity < 0) {
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
            path = DOMESTIC_CANCEL_PATH;
            trId = "TTTC0013U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "KRX_FWDG_ORD_ORGNO", brokerOrganizationNumber, "ORGN_ODNO", brokerOrderNumber,
                    "ORD_DVSN", "00", "RVSE_CNCL_DVSN_CD", "02", "ORD_QTY", Integer.toString(remainingQuantity),
                    "ORD_UNPR", "0", "QTY_ALL_ORD_YN", "Y", "EXCG_ID_DVSN_CD", "KRX");
        } else {
            path = OVERSEAS_CANCEL_PATH;
            trId = "TTTT1004U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "OVRS_EXCG_CD", usExchange(exchange), "PDNO", symbol.toUpperCase(Locale.ROOT),
                    "ORGN_ODNO", brokerOrderNumber, "RVSE_CNCL_DVSN_CD", "02", "ORD_QTY", "0",
                    "OVRS_ORD_UNPR", "0", "MGCO_APTM_ODNO", "", "ORD_SVR_DVSN_CD", "0");
        }
        post(path, trId, body, null, null, null);
    }

    private JsonNode post(String path, String trId, Map<String, String> body,
                          OrderRequest approvedRequest, String exchange, KisLiveRiskDecision riskDecision) {
        KisProperties.Account credentials = properties.credentials(KisEnvironment.REAL);
        String accessToken = accessToken(credentials); // Token failure occurs before any order request.
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

    private String accessToken(KisProperties.Account credentials) {
        CachedToken cached = token;
        if (cached != null && cached.expiresAt().isAfter(Instant.now().plusSeconds(300))) return cached.value();
        synchronized (tokenLock) {
            cached = token;
            if (cached != null && cached.expiresAt().isAfter(Instant.now().plusSeconds(300))) return cached.value();
            try {
                throttle();
                JsonNode response = http.post().uri(KisEnvironment.REAL.baseUrl() + "/oauth2/tokenP")
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(Map.of("grant_type", "client_credentials", "appkey", credentials.appKey(),
                                "appsecret", credentials.appSecret()))
                        .retrieve().body(JsonNode.class);
                if (response == null || response.path("access_token").asText().isBlank()) {
                    throw new KisApiException(HttpStatus.BAD_GATEWAY, "KIS authentication response was incomplete.");
                }
                Instant expiry = LocalDateTime.parse(response.path("access_token_token_expired").asText().replace(" ", "T"))
                        .toInstant(ZoneOffset.ofHours(9));
                token = new CachedToken(response.path("access_token").asText(), expiry);
                return token.value();
            } catch (KisApiException exception) {
                throw exception;
            } catch (ResourceAccessException | RestClientResponseException exception) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS authentication failed; check real-account connectivity and credentials.");
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                throw new KisApiException(HttpStatus.SERVICE_UNAVAILABLE, "KIS authentication was interrupted.");
            } catch (RuntimeException exception) {
                throw new KisApiException(HttpStatus.BAD_GATEWAY,
                        "KIS authentication failed; check real-account connectivity and credentials.");
            }
        }
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

    private void throttle() throws InterruptedException {
        long minimum = properties.minRequestInterval().toMillis();
        if (minimum <= 0) return;
        synchronized (rateLock) {
            long now = System.currentTimeMillis();
            long delay = minimum - (now - lastRequestAt);
            if (delay > 0) Thread.sleep(delay);
            lastRequestAt = System.currentTimeMillis();
        }
    }

    private static void validate(Market market, String symbol, OrderSide side, int quantity,
                                 BigDecimal price, Currency currency) {
        if (market == null || side == null || quantity <= 0 || price == null || price.signum() <= 0 || currency == null) {
            throw new IllegalArgumentException("A valid market, side, quantity, price, and currency are required.");
        }
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

    private record CachedToken(String value, Instant expiresAt) { }
}
