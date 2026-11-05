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
    void realAndPaperTokensAndDomainsAreKeptSeparate() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
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
    void usBalanceUsesUsdCashPositionsAndSymbolSpecificBuyingPower() {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/overseas-stock/v1/trading/inquire-balance?")))
                .andExpect(header("tr_id", "VTTS3012R"))
                .andExpect(queryParam("OVRS_EXCG_CD", "NASD"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output1":{},"output2":[{"ovrs_pdno":"AAPL","ovrs_item_name":"Apple","ovrs_cblc_qty":"2","pchs_avg_pric":"190","now_pric2":"200","ovrs_stck_evlu_amt":"400","frcr_evlu_pfls_amt":"20","evlu_pfls_rt":"5"}]}
                        """, MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/overseas-stock/v1/trading/inquire-present-balance?")))
                .andExpect(header("tr_id", "VTRP6504R"))
                .andExpect(queryParam("NATN_CD", "840"))
                .andRespond(withSuccess("""
                        {"rt_cd":"0","output2":[{"crcy_cd":"USD","frcr_drwg_psbl_amt_1":"100","frcr_ord_psbl_amt1":"90"}]}
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
        assertEquals(new BigDecimal("100"), result.cashBalance());
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
    void requestTimeoutMapsToGatewayTimeoutWithoutEchoingTransportDetails() throws Exception {
        RestClient.Builder builder = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(builder).build();
        KisAccountClient client = new KisAccountClient(properties(true), builder, new ObjectMapper());
        server.expect(requestTo(PAPER_URL + "/oauth2/tokenP"))
                .andRespond(withSuccess(token("paper-token"), MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.startsWith(PAPER_URL + "/uapi/domestic-stock/")))
                .andRespond(withException(new SocketTimeoutException("private timeout detail")));

        KisApiException error = assertThrows(KisApiException.class,
                () -> client.getBalance(KisEnvironment.PAPER, Market.KR, null, null, null));
        assertEquals(HttpStatus.GATEWAY_TIMEOUT, error.status());
        assertFalse(error.getMessage().contains("private timeout detail"));
        server.verify();
    }

    private KisProperties properties(boolean enabled) {
        return new KisProperties(enabled, false,
                new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                new KisProperties.Account("real-key", "real-secret", "8765432101"),
                Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    }

    private String token(String value) {
        return "{\"access_token\":\"" + value + "\",\"access_token_token_expired\":\"" + EXPIRY + "\"}";
    }

    private String domesticBalance() {
        return "{\"rt_cd\":\"0\",\"output1\":[],\"output2\":[{\"dnca_tot_amt\":\"1\",\"ord_psbl_cash\":\"1\"}]}";
    }
}
