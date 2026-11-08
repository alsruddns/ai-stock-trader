package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.net.SocketTimeoutException;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.*;

class KisAccountClientTest {
    private static final String PAPER_URL = "https://openapivts.koreainvestment.com:29443";
    private static final String REAL_URL = "https://openapi.koreainvestment.com:9443";
    private static final String EXPIRY = OffsetDateTime.now(ZoneOffset.ofHours(9)).plusHours(12)
            .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));

    @Test
    void domesticBalanceUsesPaperCredentialsAndOrderableAmountEndpoint() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());

        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andExpect(method(HttpMethod.POST))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/v1/trading/inquire-balance?")))
                .andExpect(method(HttpMethod.GET)).andExpect(header("tr_id", "VTTC8434R"))
                .andExpect(header("appkey", "paper-key"))
                .andExpect(queryParam("CANO", "12345678"))
                .andExpect(queryParam("ACNT_PRDT_CD", "01"))
                .andExpect(queryParam("OFL_YN", ""))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"pdno":"005930","prdt_name":"Samsung","hldg_qty":"2","ord_psbl_qty":"2","pchs_avg_pric":"70000","prpr":"71000","evlu_amt":"142000","evlu_pfls_amt":"2000","evlu_pfls_rt":"1.43","CANO":"must-not-leak"}],"output2":[{"dnca_tot_amt":"1000000","ord_psbl_cash":"900000"}]}
                        """, MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/v1/trading/inquire-psbl-order?")))
                .andExpect(method(HttpMethod.GET)).andExpect(header("tr_id", "VTTC8908R"))
                .andExpect(queryParam("PDNO", "005930"))
                .andExpect(queryParam("ORD_UNPR", "71000"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output":{"ord_psbl_cash":"880000","nrcvb_buy_amt":"870000"}}
                        """, MediaType.APPLICATION_JSON));

        KisAccountSnapshot result = client.getBalance(KisEnvironment.PAPER, Market.KR, "005930",
                new BigDecimal("71000"), "NASD");

        assertEquals(KisEnvironment.PAPER, result.environment());
        assertEquals(new BigDecimal("1000000"), result.cashBalance());
        assertEquals(new BigDecimal("880000"), result.orderableCash());
        assertEquals(1, result.positions().size());
        assertEquals("005930", result.positions().getFirst().symbol());
        assertFalse(result.toString().contains("must-not-leak"));
        server.verify();
    }

    @Test
    void domesticBalanceFollowsContinuationPagesAndMergesEveryPosition() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());

        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/v1/trading/inquire-balance?")))
                .andExpect(header("tr_id", "VTTC8434R"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"pdno":"005930","hldg_qty":"2"}],"output2":[{"dnca_tot_amt":"1000000"}],"ctx_area_fk100":"next-fk","ctx_area_nk100":"next-nk"}
                        """, MediaType.APPLICATION_JSON).header("tr_cont", "M"));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/v1/trading/inquire-balance?")))
                .andExpect(header("tr_id", "VTTC8434R"))
                .andExpect(header("tr_cont", "N"))
                .andExpect(queryParam("CTX_AREA_FK100", "next-fk"))
                .andExpect(queryParam("CTX_AREA_NK100", "next-nk"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"pdno":"000660","hldg_qty":"3"}],"output2":[{"dnca_tot_amt":"1000000"}]}
                        """, MediaType.APPLICATION_JSON));

        KisAccountSnapshot result = client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null);

        assertEquals(2, result.positions().size());
        assertEquals("005930", result.positions().get(0).symbol());
        assertEquals("000660", result.positions().get(1).symbol());
        assertEquals(new BigDecimal("1000000"), result.cashBalance());
        server.verify();
    }

    @Test
    void netAssetAmountIsNotMappedAsDomesticCashBalance() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());

        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(
                        PAPER_URL + "/uapi/domestic-stock/v1/trading/inquire-balance?")))
                .andExpect(header("tr_id", "VTTC8434R"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[],"output2":[{"nass_amt":"9000000"}]}
                        """, MediaType.APPLICATION_JSON));

        KisAccountSnapshot result = client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null);

        assertNull(result.cashBalance());
        assertNull(result.orderableCash());
        assertEquals(new BigDecimal("9000000"), result.accountEquity());
        server.verify();
    }

    @Test
    void missingPositionRowsRejectTheBalanceInsteadOfAppearingAsAnEmptyAccount() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(
                        PAPER_URL + "/uapi/domestic-stock/v1/trading/inquire-balance?")))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output2":[{"dnca_tot_amt":"1000000","nass_amt":"1000000"}]}
                        """, MediaType.APPLICATION_JSON));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null));

        assertEquals(HttpStatus.BAD_GATEWAY, error.status());
        assertFalse(error.getMessage().contains("1000000"));
        server.verify();
    }

    @Test
    void unidentifiedPositionRejectsTheWholeBalanceInsteadOfDroppingThatHolding() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(
                        PAPER_URL + "/uapi/domestic-stock/v1/trading/inquire-balance?")))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"pdno":"","hldg_qty":"1"}],
                         "output2":[{"dnca_tot_amt":"1000000","nass_amt":"1000000"}]}
                        """, MediaType.APPLICATION_JSON));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null));

        assertEquals(HttpStatus.BAD_GATEWAY, error.status());
        server.verify();
    }

    @Test
    void realAndPaperTokensAndDomainsAreKeptSeparate() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(propertiesBothEnvironments(), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andExpect(header("authorization", "Bearer paper-token"))
                .andExpect(header("appkey", "paper-key"))
                .andRespond(withSuccess(domesticBalance(), MediaType.APPLICATION_JSON));
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("real-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(REAL_URL + "/uapi/domestic-stock/")))
                .andExpect(header("authorization", "Bearer real-token"))
                .andExpect(header("appkey", "real-key"))
                .andExpect(header("tr_id", "TTTC8434R"))
                .andRespond(withSuccess(domesticBalance(), MediaType.APPLICATION_JSON));

        assertEquals(KisEnvironment.PAPER, client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null).environment());
        assertEquals(KisEnvironment.REAL, client.getBalance(KisEnvironment.REAL, Market.KR, null, null, null).environment());
        server.verify();
    }

    @Test
    void usBalanceLeavesUnverifiedCashUnknownAndUsesSymbolSpecificBuyingPower() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/overseas-stock/v1/trading/inquire-balance?")))
                .andExpect(header("tr_id", "VTTS3012R"))
                .andExpect(queryParam("OVRS_EXCG_CD", "NASD"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"ovrs_pdno":"AAPL","ovrs_item_name":"Apple","ovrs_cblc_qty":"2","pchs_avg_pric":"190","now_pric2":"200","ovrs_stck_evlu_amt":"400","frcr_evlu_pfls_amt":"20","evlu_pfls_rt":"5"}],"output2":{"unverified_field":"100"}}
                        """, MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/overseas-stock/v1/trading/inquire-psamount?")))
                .andExpect(header("tr_id", "VTTS3007R"))
                .andExpect(queryParam("ITEM_CD", "AAPL"))
                .andExpect(queryParam("OVRS_ORD_UNPR", "200"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output":{"ord_psbl_frcr_amt":"75","ord_psbl_qty":"0"}}
                        """, MediaType.APPLICATION_JSON));

        KisAccountSnapshot result = client.getBalance(KisEnvironment.PAPER, Market.US, "AAPL",
                new BigDecimal("200"), "NASD");

        assertEquals("USD", result.currency());
        assertNull(result.cashBalance());
        assertNull(result.accountEquity());
        assertEquals(new BigDecimal("75"), result.orderableCash());
        assertEquals("AAPL", result.positions().getFirst().symbol());
        server.verify();
    }

    @Test
    void disabledAccountReadsFailBeforeAnyHttpCall() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(false), builder, new ObjectMapper());

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.REAL, Market.KR, null, null, null));
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, error.status());
        server.verify();
    }

    @Test
    void realAccountReadsRequireTheirOwnEnvironmentSwitch() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisProperties separated = new KisProperties(true, true, false, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "8765432101"),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
        KisAccountClient client = new KisAccountClient(separated, builder, new ObjectMapper());

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.REAL, Market.KR, null, null, null));

        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, error.status());
        server.verify();
    }

    @Test
    void legacyGlobalReadSwitchDoesNotEnableRealAccountReads() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.REAL, Market.KR, null, null, null));

        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, error.status());
        server.verify();
    }

    @Test
    void rateLimitResponseIsReportedWithoutLeakingBrokerBody() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withStatus(HttpStatus.TOO_MANY_REQUESTS).body("private broker response"));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null));
        assertEquals(HttpStatus.TOO_MANY_REQUESTS, error.status());
        assertFalse(error.getMessage().contains("private broker response"));
        server.verify();
    }

    @Test
    void httpServerErrorExposesOnlySafeBrokerCodeForDiagnosis() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withStatus(HttpStatus.INTERNAL_SERVER_ERROR)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("{\"msg_cd\":\"OPSQ2000\",\"msg1\":\"private broker detail\",\"token\":\"never-show\"}"));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withStatus(HttpStatus.INTERNAL_SERVER_ERROR)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("{\"msg_cd\":\"OPSQ2000\",\"msg1\":\"private broker detail\",\"token\":\"never-show\"}"));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null));

        assertEquals("KIS account request failed (HTTP 500, code OPSQ2000); check the environment and KIS status.",
                error.getMessage());
        assertFalse(error.getMessage().contains("private broker detail"));
        assertFalse(error.getMessage().contains("never-show"));
        server.verify();
    }

    @Test
    void brokerRateLimitCodeMapsHttp500ToRetryable429WithoutLeakingBody() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withStatus(HttpStatus.INTERNAL_SERVER_ERROR)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body("{\"msg_cd\":\"EGW00201\",\"msg1\":\"private rate-limit detail\"}"));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null));

        assertEquals(HttpStatus.TOO_MANY_REQUESTS, error.status());
        assertEquals("KIS rate limit reached; wait before requesting again.", error.getMessage());
        assertFalse(error.getMessage().contains("private rate-limit detail"));
        server.verify();
    }

    @Test
    void requestTimeoutMapsToGatewayTimeoutWithoutEchoingTransportDetails() throws Exception {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withException(new SocketTimeoutException("private timeout detail")));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withException(new SocketTimeoutException("private timeout detail")));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null));
        assertEquals(HttpStatus.GATEWAY_TIMEOUT, error.status());
        assertFalse(error.getMessage().contains("private timeout detail"));
        server.verify();
    }

    @Test
    void paperAccountReadRetriesOnceAfterGatewayFailure() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withStatus(HttpStatus.BAD_GATEWAY));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withSuccess(domesticBalance(), MediaType.APPLICATION_JSON));

        KisAccountSnapshot result = client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null);

        assertEquals(KisEnvironment.PAPER, result.environment());
        assertEquals(new BigDecimal("1"), result.cashBalance());
        server.verify();
    }

    @Test
    void paperAccountReadRetriesOnceAfterEgW00002() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withSuccess(
                        "{\"rt_cd\":\"1\",\"msg_cd\":\"EGW00002\",\"msg1\":\"transient\"}",
                        MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withSuccess(domesticBalance(), MediaType.APPLICATION_JSON));

        KisAccountSnapshot result = client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null);

        assertEquals(KisEnvironment.PAPER, result.environment());
        server.verify();
    }

    @Test
    void domesticRealizedPnlStatementUsesOfficialRealReadOnlyContractAndPages() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(propertiesBothEnvironments(), builder, new ObjectMapper());
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("real-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(
                        REAL_URL + "/uapi/domestic-stock/v1/trading/inquire-period-profit?")))
                .andExpect(method(HttpMethod.GET)).andExpect(header("tr_id", "TTTC8708R"))
                .andExpect(queryParam("CANO", "87654321"))
                .andExpect(queryParam("INQR_STRT_DT", "20261102"))
                .andExpect(queryParam("INQR_END_DT", "20261106"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"pdno":"005930","rlzt_pfls_amt":"120"}],"output2":{"total":"120"},"ctx_area_fk100":"fk-next","ctx_area_nk100":"nk-next"}
                        """, MediaType.APPLICATION_JSON).header("tr_cont", "M"));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(
                        REAL_URL + "/uapi/domestic-stock/v1/trading/inquire-period-profit?")))
                .andExpect(header("tr_id", "TTTC8708R")).andExpect(header("tr_cont", "N"))
                .andExpect(queryParam("CTX_AREA_FK100", "fk-next"))
                .andExpect(queryParam("CTX_AREA_NK100", "nk-next"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"pdno":"000660","rlzt_pfls_amt":"80"}],"output2":{"total":"120"}}
                        """, MediaType.APPLICATION_JSON));

        KisRealizedPnlStatement result = client.getRealizedPnlStatement(Market.KR,
                java.time.LocalDate.parse("2026-11-02"), java.time.LocalDate.parse("2026-11-06"), "NASD");

        assertEquals(2, result.details().size());
        assertEquals("120", result.summary().path("total").asText());
        assertEquals("KIS_READ_ONLY_REALIZED_PNL_REST", result.source());
        assertFalse(result.toString().contains("real-secret"));
        server.verify();
    }

    @Test
    void usRealizedPnlStatementUsesOfficialRealReadOnlyContract() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(propertiesBothEnvironments(), builder, new ObjectMapper());
        server.expect(requestTo(REAL_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("real-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(
                        REAL_URL + "/uapi/overseas-stock/v1/trading/inquire-period-profit?")))
                .andExpect(method(HttpMethod.GET)).andExpect(header("tr_id", "TTTS3039R"))
                .andExpect(queryParam("OVRS_EXCG_CD", "NASD"))
                .andExpect(queryParam("CRCY_CD", "USD"))
                .andExpect(queryParam("WCRC_FRCR_DVSN_CD", "02"))
                .andExpect(queryParam("CTX_AREA_FK200", ""))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":[{"ovrs_pdno":"AAPL","ovrs_rlzt_pfls_amt":"7.25"}],"output2":{"ovrs_rlzt_pfls_amt":"7.25"}}
                        """, MediaType.APPLICATION_JSON));

        KisRealizedPnlStatement result = client.getRealizedPnlStatement(Market.US,
                java.time.LocalDate.parse("2026-11-02"), java.time.LocalDate.parse("2026-11-06"), "NASD");

        assertEquals("AAPL", result.details().get(0).path("ovrs_pdno").asText());
        assertEquals("NASD", result.exchange());
        server.verify();
    }

    @Test
    void realizedPnlStatementRequiresRealReadSwitchAndBoundedDateRange() {
        RestClient.Builder builder = RestClient.builder();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());

        KisApiException disabled = assertThrows(KisApiException.class, () -> client.getRealizedPnlStatement(Market.KR,
                java.time.LocalDate.parse("2026-11-01"), java.time.LocalDate.parse("2026-11-02"), null));
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, disabled.status());
        KisApiException tooWide = assertThrows(KisApiException.class, () ->
                new KisAccountClient(propertiesBothEnvironments(), RestClient.builder(), new ObjectMapper())
                        .getRealizedPnlStatement(Market.US, java.time.LocalDate.parse("2026-10-01"),
                                java.time.LocalDate.parse("2026-11-02"), "NASD"));
        assertEquals(HttpStatus.BAD_REQUEST, tooWide.status());
    }

    private KisProperties properties(boolean enabled) {
        return new KisProperties(enabled, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "8765432101"),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }

    private KisProperties propertiesBothEnvironments() {
        return new KisProperties(true, true, true, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "8765432101"),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }

    private String token(String value) {
        return "{\"access_token\":\"" + value + "\",\"access_token_token_expired\":\"" + EXPIRY + "\"}";
    }

    private String domesticBalance() {
        return "{\"rt_cd\":\"0\",\"output1\":[],\"output2\":[{\"dnca_tot_amt\":\"1\",\"nass_amt\":\"1\",\"ord_psbl_cash\":\"1\"}]}";
    }
}
