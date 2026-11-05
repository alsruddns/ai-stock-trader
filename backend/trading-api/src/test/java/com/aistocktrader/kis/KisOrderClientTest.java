package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.config.TradingSafetyProperties;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.time.Instant;
import java.time.ZoneId;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.*;

class KisOrderClientTest {
    @Test
    void domesticPaperLimitOrderUsesPaperCredentialsAndLimitOnlyContract() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andExpect(method(HttpMethod.POST)).andExpect(jsonPath("$.appkey").value("paper-key"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/order-cash"))
                .andExpect(method(HttpMethod.POST)).andExpect(header("tr_id", "VTTC0012U"))
                .andExpect(header("appkey", "paper-key"))
                .andExpect(jsonPath("$.CANO").value("12345678"))
                .andExpect(jsonPath("$.ACNT_PRDT_CD").value("01"))
                .andExpect(jsonPath("$.PDNO").value("005930"))
                .andExpect(jsonPath("$.ORD_DVSN").value("00"))
                .andExpect(jsonPath("$.ORD_QTY").value("2"))
                .andExpect(jsonPath("$.ORD_UNPR").value("70000"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"0\",\"output\":{\"ODNO\":\"1234\",\"KRX_FWDG_ORD_ORGNO\":\"ORG01\"}}",
                        org.springframework.http.MediaType.APPLICATION_JSON));

        KisOrderReceipt result = client(true, builder).placePaperLimitOrder(
                Market.KR, "005930", OrderSide.BUY, 2, new BigDecimal("70000"), Currency.KRW, null);

        assertEquals("1234", result.brokerOrderNumber());
        assertEquals("ORG01", result.brokerOrganizationNumber());
        assertEquals("paper", result.environment());
        server.verify();
    }

    @Test
    void usPaperSellUsesOverseasOrderTransactionAndMappedExchange() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/uapi/overseas-stock/v1/trading/order"))
                .andExpect(header("tr_id", "VTTT1001U"))
                .andExpect(jsonPath("$.OVRS_EXCG_CD").value("NASD"))
                .andExpect(jsonPath("$.PDNO").value("AAPL"))
                .andExpect(jsonPath("$.OVRS_ORD_UNPR").value("231.59"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"0\",\"output\":{\"ODNO\":\"5678\"}}",
                        org.springframework.http.MediaType.APPLICATION_JSON));

        KisOrderReceipt result = client(true, builder).placePaperLimitOrder(
                Market.US, "aapl", OrderSide.SELL, 1, new BigDecimal("231.59"), Currency.USD, "NAS");

        assertEquals("5678", result.brokerOrderNumber());
        server.verify();
    }

    @Test
    void paperOrderSwitchIsOffByDefaultAndNeverAttemptsNetwork() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisOrderClient client = client(false, builder);
        assertThrows(IllegalStateException.class, () -> client.placePaperLimitOrder(
                Market.KR, "005930", OrderSide.BUY, 1, BigDecimal.ONE, Currency.KRW, null));
        server.verify();
    }

    @Test
    void invalidInputIsRejectedBeforeNetworkRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        assertThrows(IllegalArgumentException.class, () -> client(true, builder).placePaperLimitOrder(
                Market.US, "AAPL", OrderSide.BUY, 1, BigDecimal.ONE, Currency.USD, "LSE"));
        server.verify();
    }

    @Test
    void paperNotionalLimitIsAppliedBeforeBrokerCall() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        assertThrows(IllegalArgumentException.class, () -> client(true, builder).placePaperLimitOrder(
                Market.US, "AAPL", OrderSide.BUY, 1, new BigDecimal("250.01"), Currency.USD, "NAS"));
        server.verify();
    }

    @Test
    void domesticReconciliationMapsPartialFillFromBrokerHistory() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(header("tr_id", "VTTC0081R"))
                .andExpect(queryParam("ODNO", "1234"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","ctx_area_fk100":"","ctx_area_nk100":"","output1":[
                          {"odno":"1234","tot_ccld_qty":"1","rmn_qty":"1","avg_prvs":"70000","cncl_yn":"N","rjct_qty":"0"}
                        ]}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));

        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("70000"), Currency.KRW), null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        KisBrokerOrderSnapshot snapshot = client(true, builder).reconcilePaperOrder(intent).orElseThrow();

        assertEquals(1, snapshot.filledQuantity());
        assertEquals(1, snapshot.remainingQuantity());
        assertEquals(new BigDecimal("70000"), snapshot.averageFillPrice());
        server.verify();
    }

    private static KisOrderClient client(boolean enabled, RestClient.Builder builder) {
        KisProperties.Account paper = new KisProperties.Account("paper-key", "paper-secret", "1234567801");
        KisProperties properties = new KisProperties(false, false, paper,
                new KisProperties.Account("", "", ""), Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofMillis(100));
        return new KisOrderClient(properties, new KisOrderProperties(enabled),
                new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("250")), builder);
    }

    private static String token() {
        String expiry = LocalDateTime.now().plusHours(12).format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        return "{\"access_token\":\"test-token\",\"access_token_token_expired\":\"" + expiry + "\"}";
    }
}
