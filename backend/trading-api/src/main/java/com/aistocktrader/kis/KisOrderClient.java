package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.config.TradingSafetyProperties;
import com.fasterxml.jackson.databind.JsonNode;
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
import java.util.concurrent.ConcurrentHashMap;

/** Internal paper-only KIS order adapter. No public trading controller uses it in this phase. */
@Component
public class KisOrderClient {
    private static final String DOMESTIC_ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash";
    private static final String OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order";
    private static final String DOMESTIC_CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl";
    private static final String OVERSEAS_CANCEL_PATH = "/uapi/overseas-stock/v1/trading/order-rvsecncl";

    private final KisProperties properties;
    private final KisOrderProperties orderProperties;
    private final TradingSafetyProperties safety;
    private final RestClient http;
    private volatile CachedToken token;

    public KisOrderClient(KisProperties properties, KisOrderProperties orderProperties,
                          TradingSafetyProperties safety, RestClient.Builder builder) {
        this.properties = properties;
        this.orderProperties = orderProperties;
        this.safety = safety;
        this.http = builder.build();
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
        if (symbol == null || symbol.isBlank() || brokerOrderNumber == null || brokerOrderNumber.isBlank()
                || remainingQuantity < 0) {
            throw new IllegalArgumentException("A symbol, original broker order, and non-negative remaining quantity are required.");
        }
        String account = properties.credentials(KisEnvironment.PAPER).accountNumber();
        String path;
        String trId;
        Map<String, String> body;
        if (market == Market.KR) {
            if (brokerOrganizationNumber == null || brokerOrganizationNumber.isBlank()) {
                throw new IllegalArgumentException("Domestic cancellation requires the broker organization number.");
            }
            path = DOMESTIC_CANCEL_PATH;
            trId = "VTTC0013U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "KRX_FWDG_ORD_ORGNO", brokerOrganizationNumber, "ORGN_ODNO", brokerOrderNumber,
                    "ORD_DVSN", "00", "RVSE_CNCL_DVSN_CD", "02", "ORD_QTY", Integer.toString(remainingQuantity),
                    "ORD_UNPR", "0", "QTY_ALL_ORD_YN", "Y", "EXCG_ID_DVSN_CD", "KRX");
        } else {
            path = OVERSEAS_CANCEL_PATH;
            trId = "VTTT1004U";
            body = Map.of("CANO", account.substring(0, 8), "ACNT_PRDT_CD", account.substring(8),
                    "OVRS_EXCG_CD", usExchange(exchange), "PDNO", symbol.toUpperCase(Locale.ROOT),
                    "ORGN_ODNO", brokerOrderNumber, "RVSE_CNCL_DVSN_CD", "02", "ORD_QTY", "0",
                    "OVRS_ORD_UNPR", "0", "MGCO_APTM_ODNO", "", "ORD_SVR_DVSN_CD", "0");
        }
        post(path, trId, body);
    }

    private JsonNode post(String path, String trId, Map<String, String> body) {
        KisProperties.Account credentials = properties.credentials(KisEnvironment.PAPER);
        try {
            JsonNode response = http.post().uri(KisEnvironment.PAPER.baseUrl() + path)
                    .contentType(MediaType.APPLICATION_JSON).accept(MediaType.APPLICATION_JSON)
                    .header("authorization", "Bearer " + accessToken(credentials))
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
        } catch (ResourceAccessException ex) {
            throw new KisOrderOutcomeUnknownException("KIS paper order outcome is unknown; reconcile broker orders before taking further action.", ex);
        } catch (RestClientResponseException ex) {
            throw new KisOrderOutcomeUnknownException("KIS paper order outcome is unknown; reconcile broker orders before taking further action.", ex);
        }
    }

    private String accessToken(KisProperties.Account credentials) {
        CachedToken current = token;
        if (current != null && Instant.now().plusSeconds(300).isBefore(current.expiresAt())) return current.value();
        synchronized (this) {
            current = token;
            if (current != null && Instant.now().plusSeconds(300).isBefore(current.expiresAt())) return current.value();
            try {
                JsonNode response = http.post().uri(KisEnvironment.PAPER.baseUrl() + "/oauth2/tokenP")
                        .contentType(MediaType.APPLICATION_JSON).body(Map.of("grant_type", "client_credentials",
                                "appkey", credentials.appKey(), "appsecret", credentials.appSecret()))
                        .retrieve().body(JsonNode.class);
                if (response == null || response.path("access_token").asText().isBlank()) {
                    throw new KisApiException(org.springframework.http.HttpStatus.BAD_GATEWAY, "KIS authentication response was incomplete.");
                }
                Instant expires = LocalDateTime.parse(response.path("access_token_token_expired").asText().replace(" ", "T"))
                        .toInstant(ZoneOffset.ofHours(9));
                token = new CachedToken(response.path("access_token").asText(), expires);
                return token.value();
            } catch (KisApiException ex) {
                throw ex;
            } catch (RuntimeException ex) {
                throw new KisApiException(org.springframework.http.HttpStatus.BAD_GATEWAY, "KIS authentication failed; check paper credentials.");
            }
        }
    }

    private void requirePaperOrdersEnabled() {
        if (!orderProperties.paperEnabled()) throw new IllegalStateException("KIS paper orders are disabled.");
        if (safety.liveTradingEnabled() || !"paper".equalsIgnoreCase(safety.mode())) {
            throw new IllegalStateException("KIS paper orders are disabled outside the locked paper mode.");
        }
        if (!properties.credentials(KisEnvironment.PAPER).configured()) throw new IllegalStateException("KIS paper credentials are not configured.");
    }

    private static void validate(Market market, String symbol, OrderSide side, int quantity, BigDecimal price, Currency currency) {
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
        return code.matches("[A-Za-z0-9_-]{1,32}") ? code : "unknown";
    }

    private record CachedToken(String value, Instant expiresAt) { }
}
