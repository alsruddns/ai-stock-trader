package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class KisDomesticCostReconciliationServiceTest {
    private static final LocalDate DATE = LocalDate.of(2026, 11, 12);
    private final KisProperties properties = new KisProperties(true, true, true, false,
            new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
            new KisProperties.Account("real-key", "real-secret", "8765432101"),
            Duration.ofSeconds(1), Duration.ofSeconds(1), Duration.ZERO);
    private final KisAccountClient accounts = mock(KisAccountClient.class);
    private final KisRealOrderClient brokerOrders = mock(KisRealOrderClient.class);
    private final KisOrderIntentRepository intents = mock(KisOrderIntentRepository.class);
    private final KisRealTradingCostProperties costs = new KisRealTradingCostProperties(
            "5", "5", "2", "", "", "");
    private final KisDomesticCostReconciliationService service = new KisDomesticCostReconciliationService(
            properties, accounts, brokerOrders, intents, costs);

    @Test
    void reconcilesOnlyWhenTheWholeLocalFillSetMatchesBrokerOrdersAndCosts() throws Exception {
        var buy = order(OrderSide.BUY, "100", "buy-id");
        var sell = order(OrderSide.SELL, "200", "sell-id");
        when(intents.findByEnvironmentAndMarketAndLastFillAtGreaterThanEqualAndLastFillAtLessThanOrderByLastFillAtAsc(
                any(), any(), any(), any())).thenReturn(List.of(buy, sell));
        when(brokerOrders.getRealSessionExecutions(Market.KR, DATE)).thenReturn(List.of(
                execution("100", OrderSide.BUY), execution("200", OrderSide.SELL)));
        when(accounts.getRealizedPnlStatement(Market.KR, DATE, DATE, null)).thenReturn(statement(
                "{\"buy_fee_smtl\":\"5\",\"sll_fee_smtl\":\"5\",\"sll_tltx_smtl\":\"2\"}"));

        var result = service.reconcile(DATE);

        assertEquals(KisDomesticCostReconciler.Status.MATCHED, result.status());
        assertEquals(2, result.matchedOrderCount());
        assertEquals(List.of(), result.unavailableReasons());
        assertEquals(List.of(), result.mismatchedFields());
    }

    @Test
    void rejectsUntrackedManualBrokerExecutionBeforeReadingCostStatement() {
        var localOrder = order(OrderSide.BUY, "100", "buy-id");
        when(intents.findByEnvironmentAndMarketAndLastFillAtGreaterThanEqualAndLastFillAtLessThanOrderByLastFillAtAsc(
                any(), any(), any(), any())).thenReturn(List.of(localOrder));
        when(brokerOrders.getRealSessionExecutions(Market.KR, DATE)).thenReturn(List.of(
                execution("100", OrderSide.BUY), execution("manual", OrderSide.SELL)));

        var result = service.reconcile(DATE);

        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE, result.status());
        assertEquals(List.of("broker_execution_ledger_mismatch"), result.unavailableReasons());
        verify(accounts, never()).getRealizedPnlStatement(any(), any(), any(), any());
    }

    @Test
    void blocksCurrentDateWhenTheBrokerStatementMayStillBeChanging() {
        LocalDate today = LocalDate.now(ZoneId.of("Asia/Seoul"));

        var result = service.reconcile(today);

        assertEquals(KisDomesticCostReconciler.Status.UNAVAILABLE, result.status());
        assertEquals(List.of("session_not_closed"), result.unavailableReasons());
        verify(intents, never()).findByEnvironmentAndMarketAndLastFillAtGreaterThanEqualAndLastFillAtLessThanOrderByLastFillAtAsc(
                any(), any(), any(), any());
    }

    private KisOrderIntent order(OrderSide side, String brokerNumber, String suffix) {
        KisOrderIntent order = mock(KisOrderIntent.class);
        Instant fillAt = DATE.atTime(10, 0).atZone(ZoneId.of("Asia/Seoul")).toInstant();
        when(order.getEnvironment()).thenReturn(KisEnvironment.REAL);
        when(order.getMarket()).thenReturn(Market.KR);
        when(order.getStatus()).thenReturn(KisOrderStatus.FILLED);
        when(order.getFilledQuantity()).thenReturn(1);
        when(order.getAverageFillPrice()).thenReturn(new BigDecimal("10000"));
        when(order.getLastFillAt()).thenReturn(fillAt);
        when(order.getBrokerOrderNumber()).thenReturn(brokerNumber);
        when(order.getBrokerOrganizationNumber()).thenReturn("00001");
        when(order.getIdempotencyKey()).thenReturn(UUID.nameUUIDFromBytes(suffix.getBytes()));
        when(order.getSymbol()).thenReturn("005930");
        when(order.getSide()).thenReturn(side);
        return order;
    }

    private KisRealOrderClient.KisBrokerSessionExecution execution(String orderNumber, OrderSide side) {
        return new KisRealOrderClient.KisBrokerSessionExecution(orderNumber, "005930", side, "00001");
    }

    private KisRealizedPnlStatement statement(String summary) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        return new KisRealizedPnlStatement(Market.KR, DATE, DATE, null, Instant.now(), mapper.createArrayNode(),
                mapper.readTree(summary), "KIS_READ_ONLY_REALIZED_PNL_REST");
    }
}
