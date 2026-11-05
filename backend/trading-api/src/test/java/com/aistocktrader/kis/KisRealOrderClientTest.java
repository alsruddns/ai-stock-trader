package com.aistocktrader.kis;

import com.aistocktrader.config.TradingSafetyProperties;
import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.OrderRequest;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.math.BigDecimal;
import java.net.SocketTimeoutException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withException;

class KisRealOrderClientTest {
    private static final String REAL_URL = "https://openapi.koreainvestment.com:9443";
    private static final String EXPIRY = OffsetDateTime.now(ZoneOffset.ofHours(9)).plusHours(12)
            .format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));

    @Test
    void disabledProductionGatesBlockBeforeTokenOrOrderRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisRealOrderClient client = new KisRealOrderClient(properties(true, true),
                new TradingSafetyProperties("paper", false, BigDecimal.valueOf(1_000_000), BigDecimal.valueOf(100)), builder);

        assertThrows(IllegalStateException.class, () -> client.placeRealLimitOrder(Market.KR, "005930",
                OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW, null, approvedKrBuy()));
        server.verify();
    }

    @Test
    void unapprovedRiskDecisionBlocksBeforeTokenOrOrderRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisRealOrderClient client = new KisRealOrderClient(properties(true, true), realSafety(), builder);

        assertThrows(IllegalStateException.class, () -> client.placeRealLimitOrder(Market.KR, "005930",
                OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW, null,
                rejectedKrBuy()));
        server.verify();
    }

    @Test
    void domesticRealBuyUsesOfficialRealTransactionAndAccountCredentials() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token(), MediaType.APPLICATION_JSON));
        server.expect(requestTo(REAL_URL + "/uapi/domestic-stock/v1/trading/order-cash"))
                .andExpect(header("authorization", "Bearer real-token"))
                .andExpect(header("appkey", "real-key"))
                .andExpect(header("tr_id", "TTTC0012U"))
                .andExpect(jsonPath("$.CANO").value("87654321"))
                .andExpect(jsonPath("$.ACNT_PRDT_CD").value("01"))
                .andExpect(jsonPath("$.PDNO").value("005930"))
                .andExpect(jsonPath("$.ORD_QTY").value("1"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","msg_cd":"MCA00000","output":{"ODNO":"7001","KRX_FWDG_ORD_ORGNO":"ORG01"}}
                        """, MediaType.APPLICATION_JSON));

        KisOrderReceipt receipt = new KisRealOrderClient(properties(true, true), realSafety(), builder)
                .placeRealLimitOrder(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("70000"), Currency.KRW, null, approvedKrBuy());

        assertEquals("real", receipt.environment());
        assertEquals("7001", receipt.brokerOrderNumber());
        assertEquals("ORG01", receipt.brokerOrganizationNumber());
        server.verify();
    }

    @Test
    void overseasRealSellUsesOfficialUsRealTransaction() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token(), MediaType.APPLICATION_JSON));
        server.expect(requestTo(REAL_URL + "/uapi/overseas-stock/v1/trading/order"))
                .andExpect(header("tr_id", "TTTT1006U"))
                .andExpect(jsonPath("$.OVRS_EXCG_CD").value("NASD"))
                .andExpect(jsonPath("$.PDNO").value("AAPL"))
                .andExpect(jsonPath("$.ORD_QTY").value("2"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","msg_cd":"MCA00000","output":{"ODNO":"7002"}}
                        """, MediaType.APPLICATION_JSON));

        KisOrderReceipt receipt = new KisRealOrderClient(properties(true, true), realSafety(), builder)
                .placeRealLimitOrder(Market.US, "AAPL", OrderSide.SELL, 2,
                        new BigDecimal("10"), Currency.USD, "NASD", approvedUsSell());

        assertEquals("real", receipt.environment());
        assertEquals("SELL", receipt.side());
        server.verify();
    }

    @Test
    void domesticRealCancelUsesOriginalBrokerIdentifiers() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token(), MediaType.APPLICATION_JSON));
        server.expect(requestTo(REAL_URL + "/uapi/domestic-stock/v1/trading/order-rvsecncl"))
                .andExpect(header("tr_id", "TTTC0013U"))
                .andExpect(jsonPath("$.KRX_FWDG_ORD_ORGNO").value("ORG01"))
                .andExpect(jsonPath("$.ORGN_ODNO").value("7001"))
                .andExpect(jsonPath("$.RVSE_CNCL_DVSN_CD").value("02"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"MCA00000\",\"output\":{\"ODNO\":\"7003\"}}",
                        MediaType.APPLICATION_JSON));

        assertDoesNotThrow(() -> new KisRealOrderClient(properties(true, true), realSafety(), builder)
                .cancelRealOrder(Market.KR, "005930", "7001", "ORG01", 1, null));
        server.verify();
    }

    @Test
    void overseasRealCancelUsesOfficialUsRealTransaction() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token(), MediaType.APPLICATION_JSON));
        server.expect(requestTo(REAL_URL + "/uapi/overseas-stock/v1/trading/order-rvsecncl"))
                .andExpect(header("tr_id", "TTTT1004U"))
                .andExpect(jsonPath("$.OVRS_EXCG_CD").value("NYSE"))
                .andExpect(jsonPath("$.ORGN_ODNO").value("7111"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"MCA00000\",\"output\":{}}",
                        MediaType.APPLICATION_JSON));

        assertDoesNotThrow(() -> new KisRealOrderClient(properties(true, true), realSafety(), builder)
                .cancelRealOrder(Market.US, "MSFT", "7111", null, 2, "NYSE"));
        server.verify();
    }

    @Test
    void realOrderTimeoutIsMarkedUnknownAndNotAutomaticallyRetried() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token(), MediaType.APPLICATION_JSON));
        server.expect(requestTo(REAL_URL + "/uapi/domestic-stock/v1/trading/order-cash"))
                .andRespond(withException(new SocketTimeoutException("timeout")));

        assertThrows(KisOrderOutcomeUnknownException.class, () ->
                new KisRealOrderClient(properties(true, true), realSafety(), builder)
                        .placeRealLimitOrder(Market.KR, "005930", OrderSide.BUY, 1,
                                new BigDecimal("70000"), Currency.KRW, null, approvedKrBuy()));
        server.verify();
    }

    @Test
    void realOrderRequiresRealAccountReadPreflightSwitch() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();

        assertThrows(IllegalStateException.class, () ->
                new KisRealOrderClient(properties(true, false), realSafety(), builder)
                        .placeRealLimitOrder(Market.KR, "005930", OrderSide.BUY, 1,
                                new BigDecimal("70000"), Currency.KRW, null, approvedKrBuy()));
        server.verify();
    }

    @Test
    void orderAboveConfiguredNotionalFailsBeforeHttpCall() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisRealOrderClient client = new KisRealOrderClient(properties(true, true), realSafety(), builder);

        assertThrows(IllegalArgumentException.class, () -> client.placeRealLimitOrder(Market.KR, "005930",
                OrderSide.BUY, 20, new BigDecimal("70000"), Currency.KRW, null, approvedLargeKrBuy()));
        server.verify();
    }

    @Test
    void riskApprovalCannotBeReusedForDifferentOrderQuantity() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();

        assertThrows(IllegalStateException.class, () -> new KisRealOrderClient(properties(true, true), realSafety(), builder)
                .placeRealLimitOrder(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("70000"), Currency.KRW, null, approvedKrBuy()));
        server.verify();
    }

    private static TradingSafetyProperties realSafety() {
        return new TradingSafetyProperties("real", true, true,
                new BigDecimal("1000000"), new BigDecimal("100"));
    }

    private static KisProperties properties(boolean globalRead, boolean realRead) {
        return new KisProperties(globalRead, false, realRead, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "8765432101"),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }

    private static KisLiveRiskDecision approvedKrBuy() {
        KisLiveOrderRiskGate gate = new KisLiveOrderRiskGate(realSafety(),
                new KisLiveRiskLimits(new BigDecimal("1"), new BigDecimal("0.25"), new BigDecimal("10"), 3, 3));
        return gate.assess(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("70000"), Currency.KRW), null,
                new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("1000000"),
                        new BigDecimal("100000"), List.of(), java.time.Instant.now(), "KIS_READ_ONLY_REST"),
                new KisLiveRiskContext(true, true, true, true, false, 0, new BigDecimal("0.5"), 0,
                        new BigDecimal("1000000"), new BigDecimal("100")));
    }

    private static KisLiveRiskDecision rejectedKrBuy() {
        KisLiveOrderRiskGate gate = new KisLiveOrderRiskGate(realSafety(),
                new KisLiveRiskLimits(new BigDecimal("1"), new BigDecimal("0.25"), new BigDecimal("10"), 3, 3));
        return gate.assess(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                        new BigDecimal("70000"), Currency.KRW), null,
                new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("1000000"),
                        new BigDecimal("100000"), List.of(), java.time.Instant.now(), "KIS_READ_ONLY_REST"),
                new KisLiveRiskContext(false, true, true, true, false, 0, BigDecimal.ONE, 3,
                        new BigDecimal("1000000"), new BigDecimal("100")));
    }

    private static KisLiveRiskDecision approvedLargeKrBuy() {
        TradingSafetyProperties permissiveRiskCap = new TradingSafetyProperties("real", true, true,
                new BigDecimal("2000000"), new BigDecimal("100"));
        KisLiveOrderRiskGate gate = new KisLiveOrderRiskGate(permissiveRiskCap,
                new KisLiveRiskLimits(new BigDecimal("1"), new BigDecimal("0.25"), new BigDecimal("10"), 3, 3));
        return gate.assess(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 20,
                        new BigDecimal("70000"), Currency.KRW), null,
                new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("20000000"),
                        new BigDecimal("2000000"), List.of(), java.time.Instant.now(), "KIS_READ_ONLY_REST"),
                new KisLiveRiskContext(true, true, true, true, false, 0, BigDecimal.ZERO, 0,
                        new BigDecimal("20000000"), new BigDecimal("100")));
    }

    private static KisLiveRiskDecision approvedUsSell() {
        KisLiveOrderRiskGate gate = new KisLiveOrderRiskGate(realSafety(),
                new KisLiveRiskLimits(new BigDecimal("1"), new BigDecimal("0.25"), new BigDecimal("10"), 3, 3));
        KisAccountSnapshot.Position held = new KisAccountSnapshot.Position("AAPL", "Apple",
                BigDecimal.valueOf(4), BigDecimal.valueOf(4), BigDecimal.TEN, BigDecimal.TEN,
                BigDecimal.valueOf(40), BigDecimal.ZERO, BigDecimal.ZERO);
        return gate.assess(new OrderRequest(Market.US, "AAPL", OrderSide.SELL, 2,
                        BigDecimal.TEN, Currency.USD), "NASD",
                new KisAccountSnapshot(KisEnvironment.REAL, Market.US, "USD", new BigDecimal("10000"),
                        new BigDecimal("1000"), List.of(held), java.time.Instant.now(), "KIS_READ_ONLY_REST"),
                new KisLiveRiskContext(false, true, true, true, false, 1, null, 3, null, null));
    }

    private static String token() {
        return "{\"access_token\":\"real-token\",\"access_token_token_expired\":\"" + EXPIRY + "\"}";
    }
}
