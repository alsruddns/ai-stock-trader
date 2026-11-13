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
    void orderIntentAndResponseKeepExplicitEnvironment() {
        var request = new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent realIntent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL, request, null);
        KisOrderIntent paperIntent = new KisOrderIntent(UUID.randomUUID(), "hash", request, null);

        assertEquals(KisEnvironment.REAL, realIntent.getEnvironment());
        assertEquals("real", KisOrderIntentResponse.from(realIntent).environment());
        assertEquals("paper", KisOrderIntentResponse.from(paperIntent).environment());
    }

    @Test
    void paperReconciliationRejectsRealIntentBeforeAnyBrokerRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        var request = new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent realIntent = new KisOrderIntent(UUID.randomUUID(), "hash", KisEnvironment.REAL, request, null);

        assertThrows(IllegalStateException.class, () -> client(true, builder).reconcilePaperOrder(realIntent));
        server.verify();
    }

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
                .andExpect(jsonPath("$.EXCG_ID_DVSN_CD").value("KRX"))
                .andExpect(jsonPath("$.SLL_TYPE").value(""))
                .andExpect(jsonPath("$.CNDT_PRIC").value(""))
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
    void domesticPaperSellUsesSellTransactionAndGeneralSellType() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/order-cash"))
                .andExpect(method(HttpMethod.POST)).andExpect(header("tr_id", "VTTC0011U"))
                .andExpect(jsonPath("$.PDNO").value("005930"))
                .andExpect(jsonPath("$.ORD_DVSN").value("00"))
                .andExpect(jsonPath("$.ORD_QTY").value("1"))
                .andExpect(jsonPath("$.ORD_UNPR").value("70000"))
                .andExpect(jsonPath("$.EXCG_ID_DVSN_CD").value("KRX"))
                .andExpect(jsonPath("$.SLL_TYPE").value("01"))
                .andExpect(jsonPath("$.CNDT_PRIC").value(""))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"0\",\"output\":{\"ODNO\":\"1235\",\"KRX_FWDG_ORD_ORGNO\":\"ORG01\"}}",
                        org.springframework.http.MediaType.APPLICATION_JSON));

        KisOrderReceipt result = client(true, builder).placePaperLimitOrder(
                Market.KR, "005930", OrderSide.SELL, 1, new BigDecimal("70000"), Currency.KRW, null);

        assertEquals("1235", result.brokerOrderNumber());
        assertEquals("SELL", result.side());
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
                .andExpect(jsonPath("$.SLL_TYPE").value("00"))
                .andExpect(jsonPath("$.ORD_SVR_DVSN_CD").value("0"))
                .andExpect(jsonPath("$.ORD_DVSN").value("00"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"0\",\"output\":{\"ODNO\":\"5678\"}}",
                        org.springframework.http.MediaType.APPLICATION_JSON));

        KisOrderReceipt result = client(true, builder).placePaperLimitOrder(
                Market.US, "aapl", OrderSide.SELL, 1, new BigDecimal("231.59"), Currency.USD, "NAS");

        assertEquals("5678", result.brokerOrderNumber());
        server.verify();
    }

    @Test
    void usPaperBuyUsesBuyTransactionAndServerOrderContract() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/uapi/overseas-stock/v1/trading/order"))
                .andExpect(method(HttpMethod.POST)).andExpect(header("tr_id", "VTTT1002U"))
                .andExpect(jsonPath("$.CANO").value("12345678"))
                .andExpect(jsonPath("$.ACNT_PRDT_CD").value("01"))
                .andExpect(jsonPath("$.OVRS_EXCG_CD").value("NASD"))
                .andExpect(jsonPath("$.PDNO").value("MSFT"))
                .andExpect(jsonPath("$.ORD_QTY").value("1"))
                .andExpect(jsonPath("$.OVRS_ORD_UNPR").value("50.25"))
                .andExpect(jsonPath("$.SLL_TYPE").value(""))
                .andExpect(jsonPath("$.ORD_SVR_DVSN_CD").value("0"))
                .andExpect(jsonPath("$.ORD_DVSN").value("00"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"0\",\"output\":{\"ODNO\":\"5679\"}}",
                        org.springframework.http.MediaType.APPLICATION_JSON));

        KisOrderReceipt result = client(true, builder).placePaperLimitOrder(
                Market.US, "msft", OrderSide.BUY, 1, new BigDecimal("50.25"), Currency.USD, "NASD");

        assertEquals("5679", result.brokerOrderNumber());
        assertEquals("BUY", result.side());
        server.verify();
    }

    @Test
    void domesticPaperCancelIsLockedWithoutSupportedMockPreflightAndSendsNoRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisApiException error = assertThrows(KisApiException.class, () -> client(true, builder).cancelPaperOrder(
                Market.KR, "005930", "1234", "ORG01", 1, null));
        assertEquals(org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE, error.status());
        assertTrue(error.getMessage().contains("PAPER_CANCEL_UNSUPPORTED"));
        server.verify();
    }

    @Test
    void paperOrderReconciliationRetriesOnceAfterHttp502OnSupportedReadEndpoint() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString(
                        "/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(method(HttpMethod.GET)).andExpect(header("tr_id", "VTTC0081R"))
                .andRespond(withStatus(org.springframework.http.HttpStatus.BAD_GATEWAY));
        server.expect(requestTo(org.hamcrest.Matchers.containsString(
                        "/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(method(HttpMethod.GET)).andExpect(header("tr_id", "VTTC0081R"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"output1\":[]}",
                        org.springframework.http.MediaType.APPLICATION_JSON));

        assertTrue(client(true, builder).reconcilePaperOrder(acceptedDomesticPaperIntent()).isEmpty());
        server.verify();
    }

    @Test
    void paperOrderReconciliationDoesNotRetryHttp429OnSupportedReadEndpoint() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString(
                        "/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(method(HttpMethod.GET)).andExpect(header("tr_id", "VTTC0081R"))
                .andRespond(withStatus(org.springframework.http.HttpStatus.TOO_MANY_REQUESTS));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client(true, builder).reconcilePaperOrder(acceptedDomesticPaperIntent()));

        assertEquals(org.springframework.http.HttpStatus.TOO_MANY_REQUESTS, error.status());
        server.verify();
    }

    @Test
    void overseasPaperCancelUsesPaperTransactionAndFullRemainingQuantityContract() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/uapi/overseas-stock/v1/trading/order-rvsecncl"))
                .andExpect(method(HttpMethod.POST)).andExpect(header("tr_id", "VTTT1004U"))
                .andExpect(jsonPath("$.OVRS_EXCG_CD").value("NASD"))
                .andExpect(jsonPath("$.PDNO").value("AAPL"))
                .andExpect(jsonPath("$.ORGN_ODNO").value("5678"))
                .andExpect(jsonPath("$.RVSE_CNCL_DVSN_CD").value("02"))
                .andExpect(jsonPath("$.ORD_QTY").value("0"))
                .andExpect(jsonPath("$.OVRS_ORD_UNPR").value("0"))
                .andExpect(jsonPath("$.QTY_ALL_ORD_YN").value("Y"))
                .andExpect(jsonPath("$.ORD_SVR_DVSN_CD").value("0"))
                .andRespond(withSuccess("{\"rt_cd\":\"0\",\"msg_cd\":\"0\",\"output\":{}}",
                        org.springframework.http.MediaType.APPLICATION_JSON));

        assertDoesNotThrow(() -> client(true, builder).cancelPaperOrder(
                Market.US, "AAPL", "5678", null, 1, "NAS"));
        server.verify();
    }

    @Test
    void paperCancelServerErrorIsUnknownAndIsNotRetried() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/uapi/overseas-stock/v1/trading/order-rvsecncl"))
                .andExpect(method(HttpMethod.POST)).andExpect(header("tr_id", "VTTT1004U"))
                .andRespond(withServerError());

        assertThrows(KisOrderOutcomeUnknownException.class, () -> client(true, builder).cancelPaperOrder(
                Market.US, "AAPL", "5678", null, 1, "NAS"));
        server.verify();
    }

    @Test
    void paperCancelRejectsZeroReconciledQuantityBeforeAnyRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();

        assertThrows(IllegalArgumentException.class, () -> client(true, builder).cancelPaperOrder(
                Market.US, "AAPL", "5678", null, 0, "NAS"));

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
    void brokerOrderRejectionPreservesSafeResponseCodeWithoutBrokerText() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/order-cash")))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("tr_id", "VTTC0012U"))
                .andRespond(withSuccess("""
                        {"rt_cd":"1","msg_cd":"EGW00201","msg1":"private broker diagnostic"}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));

        KisApiException failure = assertThrows(KisApiException.class, () -> client(true, builder).placePaperLimitOrder(
                Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW, null));

        assertEquals("KIS rejected the paper order (code EGW00201).", failure.getMessage());
        assertFalse(failure.getMessage().contains("private broker diagnostic"));
        server.verify();
    }

    @Test
    void brokerOrderRejectionWithMalformedCodeUsesSafeFallback() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/order-cash")))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("tr_id", "VTTC0012U"))
                .andRespond(withSuccess("""
                        {"rt_cd":"1","msg_cd":"EGW00201 private data","msg1":"private broker diagnostic"}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));

        KisApiException failure = assertThrows(KisApiException.class, () -> client(true, builder).placePaperLimitOrder(
                Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW, null));

        assertEquals("KIS rejected the paper order (code unknown).", failure.getMessage());
        assertFalse(failure.getMessage().contains("private broker diagnostic"));
        server.verify();
    }

    @Test
    void brokerOrderRejectionWithNonCodeTextUsesSafeFallback() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/order-cash")))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("tr_id", "VTTC0012U"))
                .andRespond(withSuccess("""
                        {"rt_cd":"1","msg_cd":"rejected","msg1":"private broker diagnostic"}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));

        KisApiException failure = assertThrows(KisApiException.class, () -> client(true, builder).placePaperLimitOrder(
                Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW, null));

        assertEquals("KIS rejected the paper order (code unknown).", failure.getMessage());
        assertFalse(failure.getMessage().contains("private broker diagnostic"));
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
                          {"odno":"1234","ord_gno_brno":"ORG01","pdno":"005930","sll_buy_dvsn_cd":"02","tot_ccld_qty":"1","rmn_qty":"1","avg_prvs":"70000","cncl_yn":"N","rjct_qty":"0"}
                        ]}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));

        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("70000"), Currency.KRW), null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        KisBrokerOrderSnapshot snapshot = client(false, builder).reconcilePaperOrder(intent).orElseThrow();

        assertEquals(1, snapshot.filledQuantity());
        assertEquals(1, snapshot.remainingQuantity());
        assertEquals(new BigDecimal("70000"), snapshot.averageFillPrice());
        server.verify();
    }

    @Test
    void paperReconciliationRequiresReadSwitchesAndNotTheOrderWriteSwitch() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisProperties.Account paper = new KisProperties.Account("paper-key", "paper-secret", "1234567801");
        KisProperties noPaperRead = new KisProperties(true, false, false, false, paper,
                new KisProperties.Account("", "", ""), Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofMillis(100));
        KisOrderIntent intent = acceptedDomesticPaperIntent();

        assertThrows(IllegalStateException.class, () -> new KisOrderClient(noPaperRead, new KisOrderProperties(false),
                new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("250")), builder)
                .reconcilePaperOrder(intent));

        server.verify();
    }

    @Test
    void domesticPaperReconciliationRejectsMalformedQuantityInsteadOfInferringIt() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[
                          {"odno":"1234","ord_gno_brno":"ORG01","pdno":"005930","sll_buy_dvsn_cd":"02","tot_ccld_qty":"1.5","rmn_qty":"1","avg_prvs":"70000"}
                        ]}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("70000"), Currency.KRW), null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        assertTrue(client(true, builder).reconcilePaperOrder(intent).isEmpty());
        server.verify();
    }

    @Test
    void domesticPaperReconciliationRejectsUnknownConflictingOrMalformedOutcomeFlags() {
        assertPaperReconciliationEmpty("\"cncl_yn\":\"X\"");
        assertPaperReconciliationEmpty("\"cncl_yn\":\"Y\",\"cancel_yn\":\"N\"");
        assertPaperReconciliationEmpty("\"rjct_qty\":\"unknown\"");
        assertPaperReconciliationEmpty("\"rjct_qty\":\"-1\"");
    }

    @Test
    void domesticReconciliationDoesNotApplyFillFromMismatchedBrokerIdentity() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[
                          {"odno":"1234","pdno":"000660","sll_buy_dvsn_cd":"02","tot_ccld_qty":"1","rmn_qty":"1"}
                        ]}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash",
                new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                        new BigDecimal("70000"), Currency.KRW), null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        assertTrue(client(true, builder).reconcilePaperOrder(intent).isEmpty());
        server.verify();
    }

    @Test
    void repeatedPaperReconciliationCursorReturnsUnknownWithoutAThirdBrokerRequest() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        var repeatedPage = (org.springframework.test.web.client.ResponseCreator) request -> {
            var response = new org.springframework.mock.http.client.MockClientHttpResponse("""
                    {"rt_cd":"0","ctx_area_fk100":"cursor-a","ctx_area_nk100":"key-a","output1":[]}
                    """.getBytes(java.nio.charset.StandardCharsets.UTF_8), org.springframework.http.HttpStatus.OK);
            response.getHeaders().setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
            response.getHeaders().set("tr_cont", "M");
            return response;
        };
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(header("tr_cont", ""))
                .andRespond(repeatedPage);
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(header("tr_cont", "N"))
                .andExpect(queryParam("CTX_AREA_FK100", "cursor-a"))
                .andExpect(queryParam("CTX_AREA_NK100", "key-a"))
                .andRespond(repeatedPage);

        var request = new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", request, null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        assertTrue(client(true, builder).reconcilePaperOrder(intent).isEmpty());
        server.verify();
    }

    @Test
    void missingPaperReconciliationCursorReturnsUnknownWithoutRepeatingFirstPage() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(header("tr_cont", ""))
                .andRespond(request -> {
                    var response = new org.springframework.mock.http.client.MockClientHttpResponse("""
                            {"rt_cd":"0","ctx_area_fk100":"","ctx_area_nk100":"","output1":[]}
                            """.getBytes(java.nio.charset.StandardCharsets.UTF_8), org.springframework.http.HttpStatus.OK);
                    response.getHeaders().setContentType(org.springframework.http.MediaType.APPLICATION_JSON);
                    response.getHeaders().set("tr_cont", "M");
                    return response;
                });

        var request = new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", request, null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        assertTrue(client(true, builder).reconcilePaperOrder(intent).isEmpty());
        server.verify();
    }

    @Test
    void overseasPaperReconciliationQueriesTheMockWholeDayAndMatchesOrderLocally() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/overseas-stock/v1/trading/inquire-ccnl?")))
                .andExpect(header("tr_id", "VTTS3035R"))
                .andExpect(queryParam("PDNO", ""))
                .andExpect(queryParam("SLL_BUY_DVSN", "00"))
                .andExpect(queryParam("CCLD_NCCS_DVSN", "00"))
                .andExpect(queryParam("OVRS_EXCG_CD", ""))
                .andExpect(queryParam("ODNO", ""))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output":[
                          {"odno":"5678","pdno":"AAPL","sll_buy_dvsn":"02","ft_ccld_qty":"1","nccs_qty":"1","ft_ccld_unpr3":"231.59"}
                        ]}
                        """, org.springframework.http.MediaType.APPLICATION_JSON));
        var request = new com.aistocktrader.order.OrderRequest(Market.US, "AAPL", OrderSide.BUY, 2,
                new BigDecimal("231.59"), Currency.USD);
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", request, "NASD");
        intent.accept(new KisOrderReceipt("paper", "US", "AAPL", "BUY", "5678", "", "0", Instant.now()));

        KisBrokerOrderSnapshot snapshot = client(true, builder).reconcilePaperOrder(intent).orElseThrow();

        assertEquals(1, snapshot.filledQuantity());
        assertEquals(1, snapshot.remainingQuantity());
        assertEquals(new BigDecimal("231.59"), snapshot.averageFillPrice());
        server.verify();
    }

    private static KisOrderClient client(boolean enabled, RestClient.Builder builder) {
        KisProperties.Account paper = new KisProperties.Account("paper-key", "paper-secret", "1234567801");
        KisProperties properties = new KisProperties(true, true, false, false, paper,
                new KisProperties.Account("", "", ""), Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ofMillis(100));
        return new KisOrderClient(properties, new KisOrderProperties(enabled),
                new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("250")), builder);
    }

    private static KisOrderIntent acceptedDomesticPaperIntent() {
        var request = new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", request, null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));
        return intent;
    }


    private static void assertPaperReconciliationEmpty(String outcomeFields) {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(token(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"odno":"1234","ord_gno_brno":"ORG01","pdno":"005930","sll_buy_dvsn_cd":"02","tot_ccld_qty":"0","rmn_qty":"1",%s}]}
                        """.formatted(outcomeFields), org.springframework.http.MediaType.APPLICATION_JSON));
        var request = new com.aistocktrader.order.OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent intent = new KisOrderIntent(UUID.randomUUID(), "hash", request, null);
        intent.accept(new KisOrderReceipt("paper", "KR", "005930", "BUY", "1234", "ORG01", "0", Instant.now()));

        assertTrue(client(true, builder).reconcilePaperOrder(intent).isEmpty());
        server.verify();
    }

    private static String token() {
        String expiry = LocalDateTime.now().plusHours(12).format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        return "{\"access_token\":\"test-token\",\"access_token_token_expired\":\"" + expiry + "\"}";
    }
}
