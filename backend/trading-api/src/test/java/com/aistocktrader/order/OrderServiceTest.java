package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;
import java.util.List;
import java.time.Instant;
import org.springframework.test.util.ReflectionTestUtils;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class OrderServiceTest {
    @Mock OrderRepository orders;
    @Mock PaperFillRepository fills;
    @Mock PaperPositionRepository positions;
    @Mock PaperCashRepository cashBalances;
    @Mock PaperStrategyRiskRepository strategyRisks;
    @Mock PaperOrderAmendmentRepository amendments;
    private final UUID idempotencyKey = UUID.fromString("d8d449d8-f154-4c5a-b1d0-6ce69f790f51");
    private TradingSafetyProperties safeConfig() {
        return new TradingSafetyProperties("paper", false, new BigDecimal("1000000"), new BigDecimal("100"));
    }
    private OrderRequest request(BigDecimal price, int quantity) {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, quantity, price, Currency.KRW);
    }
    @Test
    void persistsOnlySimulatedOrdersWithinTheNotionalLimit() {
        OrderRecord persisted = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty(), Optional.of(persisted));
        when(orders.insertIfIdempotencyKeyAbsent("KR", "005930", "BUY", 2,
                new BigDecimal("70000"), "KRW", null, null, idempotencyKey)).thenReturn(1);
        OrderRecord result = service().submit(request(new BigDecimal("70000"), 2), idempotencyKey);
        assertSame(persisted, result);
        assertEquals(OrderStatus.PENDING, result.getStatus());
        assertEquals("005930", result.getSymbol());
        assertEquals(idempotencyKey, result.getIdempotencyKey());
        verify(orders).insertIfIdempotencyKeyAbsent("KR", "005930", "BUY", 2,
                new BigDecimal("70000"), "KRW", null, null, idempotencyKey);
    }

    @Test
    void persistsStrategyAndModelAttributionAndIncludesItInIdempotencyMatching() {
        OrderRequest attributed = new OrderRequest(Market.US, "AAPL", OrderSide.BUY, 1,
                new BigDecimal("100"), Currency.USD, "ORB_VWAP", "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        OrderRecord persisted = new OrderRecord(attributed, idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty(), Optional.of(persisted));
        when(orders.insertIfIdempotencyKeyAbsent("US", "AAPL", "BUY", 1, new BigDecimal("100"), "USD",
                "ORB_VWAP", "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", idempotencyKey)).thenReturn(1);

        OrderRecord result = service().submit(attributed, idempotencyKey);

        assertEquals("ORB_VWAP", result.getStrategyId());
        assertEquals("lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", result.getModelId());
        verify(orders).insertIfIdempotencyKeyAbsent("US", "AAPL", "BUY", 1, new BigDecimal("100"), "USD",
                "ORB_VWAP", "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", idempotencyKey);
    }
    @Test
    void rejectsAnOrderThatExceedsTheRiskLimitBeforePersistence() {
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty());
        assertThrows(IllegalArgumentException.class,
                () -> service().submit(request(new BigDecimal("600000"), 2), idempotencyKey));
        verify(orders, never()).save(any());
    }
    @Test
    void returnsTheExistingOrderWhenAnIdenticalRequestIsRetried() {
        OrderRequest request = request(new BigDecimal("70000"), 2);
        OrderRecord existing = new OrderRecord(request, idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.of(existing));
        OrderRecord result = service().submit(request, idempotencyKey);
        assertSame(existing, result);
        verify(orders, never()).save(any());
    }

    @Test
    void resolvesConcurrentSameKeyInsertByReturningTheCommittedOrder() {
        OrderRequest request = request(new BigDecimal("70000"), 2);
        OrderRecord committed = new OrderRecord(request, idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty(), Optional.of(committed));
        when(orders.insertIfIdempotencyKeyAbsent("KR", "005930", "BUY", 2,
                new BigDecimal("70000"), "KRW", null, null, idempotencyKey)).thenReturn(0);

        OrderRecord result = service().submit(request, idempotencyKey);

        assertSame(committed, result);
        verify(orders, times(2)).findByIdempotencyKey(idempotencyKey);
    }

    @Test
    void rejectsConcurrentSameKeyInsertWhenCommittedRequestDiffers() {
        OrderRecord committed = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty(), Optional.of(committed));
        when(orders.insertIfIdempotencyKeyAbsent("KR", "005930", "BUY", 2,
                new BigDecimal("71000"), "KRW", null, null, idempotencyKey)).thenReturn(0);

        assertThrows(IdempotencyConflictException.class, () -> service().submit(
                request(new BigDecimal("71000"), 2), idempotencyKey));
    }

    @Test
    void reconcilesPaperOrderByIdempotencyKeyAfterAnAmbiguousWrite() {
        OrderRecord existing = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.of(existing));
        assertSame(existing, service().getPaperOrderByIdempotencyKey(idempotencyKey));
    }
    @Test
    void rejectsReusingTheSameKeyForADifferentOrder() {
        OrderRecord existing = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.of(existing));
        assertThrows(IdempotencyConflictException.class, () -> service()
                .submit(request(new BigDecimal("71000"), 2), idempotencyKey));
        verify(orders, never()).save(any());
    }
    @Test
    void rejectsLiveTradingConfigurationAtStartup() {
        assertThrows(IllegalStateException.class,
                () -> new TradingSafetyProperties("paper", true, BigDecimal.TEN, BigDecimal.TEN));
        assertThrows(IllegalStateException.class,
                () -> new TradingSafetyProperties("live", false, BigDecimal.TEN, BigDecimal.TEN));
        assertThrows(IllegalStateException.class,
                () -> new TradingSafetyProperties("real", true, false, BigDecimal.TEN, BigDecimal.TEN));
        assertDoesNotThrow(() -> new TradingSafetyProperties("real", true, true, BigDecimal.TEN, BigDecimal.TEN));
    }

    @Test
    void paperFillsAdvanceOrderStateAndUpdatePositionAtWeightedAverageCost() {
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 3), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 11L);
        when(fills.findByFillKey(any())).thenReturn(Optional.empty());
        when(orders.findByIdForUpdate(11L)).thenReturn(Optional.of(order));
        when(positions.findInstrumentForUpdate(Market.KR, "005930", Currency.KRW)).thenReturn(Optional.empty());
        when(positions.save(any(PaperPosition.class))).thenAnswer(invocation -> invocation.getArgument(0));
        PaperCashBalance cash = new PaperCashBalance(Currency.KRW, new BigDecimal("1000000"));
        when(cashBalances.findByCurrencyForUpdate(Currency.KRW)).thenReturn(Optional.of(cash));
        when(cashBalances.save(any(PaperCashBalance.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(orders.save(any(OrderRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));

        OrderRecord partial = service().recordPaperFill(11L,
                new PaperFillRequest(UUID.randomUUID(), 1, new BigDecimal("69900")));
        assertEquals(OrderStatus.PARTIALLY_FILLED, partial.getStatus());
        assertEquals(1, partial.getFilledQuantity());
        assertEquals(new BigDecimal("69900.000000"), partial.getAverageFillPrice());
        assertEquals(0, new BigDecimal("930100").compareTo(cash.getBalance()));

        order.applyFill(2, new BigDecimal("70200"));
        assertEquals(OrderStatus.FILLED, order.getStatus());
        assertEquals(3, order.getFilledQuantity());
        assertEquals(new BigDecimal("70100.000000"), order.getAverageFillPrice());
    }

    @Test
    void configuredPaperCostsReduceCashAndAddBuyFeesToPositionCostBasis() {
        OrderRecord buy = new OrderRecord(new OrderRequest(Market.KR, "005930", OrderSide.BUY, 10,
                new BigDecimal("100"), Currency.KRW), idempotencyKey);
        OrderRecord sell = new OrderRecord(new OrderRequest(Market.KR, "005930", OrderSide.SELL, 10,
                new BigDecimal("110"), Currency.KRW), UUID.randomUUID());
        ReflectionTestUtils.setField(buy, "id", 21L);
        ReflectionTestUtils.setField(sell, "id", 22L);
        PaperCashBalance cash = new PaperCashBalance(Currency.KRW, new BigDecimal("5000"));
        PaperCostProperties properties = new PaperCostProperties(true, new BigDecimal("10"), new BigDecimal("20"),
                new BigDecimal("1"), new BigDecimal("0"));
        PaperTransactionCostCalculator calculator = new PaperTransactionCostCalculator(properties);
        when(fills.findByFillKey(any())).thenReturn(Optional.empty());
        when(orders.findByIdForUpdate(21L)).thenReturn(Optional.of(buy));
        when(orders.findByIdForUpdate(22L)).thenReturn(Optional.of(sell));
        when(positions.findInstrumentForUpdate(Market.KR, "005930", Currency.KRW)).thenReturn(Optional.empty());
        when(positions.save(any(PaperPosition.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(cashBalances.findByCurrencyForUpdate(Currency.KRW)).thenReturn(Optional.of(cash));
        when(cashBalances.save(any(PaperCashBalance.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(orders.save(any(OrderRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));
        OrderService service = new OrderService(orders, safeConfig(), fills, positions, cashBalances,
                strategyRisks, calculator);
        allowFillInsert();

        service.recordPaperFill(21L, new PaperFillRequest(UUID.randomUUID(), 10, new BigDecimal("100")));
        assertEquals(new BigDecimal("3999"), cash.getBalance());
        var savedPosition = org.mockito.ArgumentCaptor.forClass(PaperPosition.class);
        verify(positions).save(savedPosition.capture());
        PaperPosition position = savedPosition.getValue();
        assertEquals(new BigDecimal("100.100000"), position.getAverageCost());
        when(positions.findInstrumentForUpdate(Market.KR, "005930", Currency.KRW)).thenReturn(Optional.of(position));
        service.recordPaperFill(22L, new PaperFillRequest(UUID.randomUUID(), 10, new BigDecimal("110")));

        assertEquals(new BigDecimal("5096"), cash.getBalance());
        assertEquals(0, position.getQuantity());
        var recordedCosts = org.mockito.ArgumentCaptor.forClass(BigDecimal.class);
        verify(fills, times(2)).insertIfFillKeyAbsent(any(), any(), anyInt(), any(), recordedCosts.capture(),
                any(), any(), any());
        assertEquals(new BigDecimal("1"), recordedCosts.getAllValues().get(0));
        assertEquals(new BigDecimal("3"), recordedCosts.getAllValues().get(1));
    }

    @Test
    void strategyEntryFillPersistsStopAndTargetInSameServiceTransaction() {
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 12L);
        PaperPosition position = new PaperPosition(Market.KR, "005930", Currency.KRW);
        ReflectionTestUtils.setField(position, "id", 55L);
        when(fills.findByFillKey(any())).thenReturn(Optional.empty());
        when(orders.findByIdForUpdate(12L)).thenReturn(Optional.of(order));
        when(positions.findInstrumentForUpdate(Market.KR, "005930", Currency.KRW)).thenReturn(Optional.empty());
        when(positions.save(any(PaperPosition.class))).thenReturn(position);
        when(cashBalances.findByCurrencyForUpdate(Currency.KRW))
                .thenReturn(Optional.of(new PaperCashBalance(Currency.KRW, new BigDecimal("500000"))));
        when(cashBalances.save(any(PaperCashBalance.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(strategyRisks.findByPositionId(55L)).thenReturn(Optional.empty());
        when(strategyRisks.save(any(PaperStrategyRisk.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(orders.save(any(OrderRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));

        OrderRecord result = serviceWithStrategyRisks().recordPaperFill(12L, new PaperFillRequest(
                UUID.randomUUID(), 2, new BigDecimal("70000"), new BigDecimal("69000"), new BigDecimal("72000")));

        assertEquals(OrderStatus.FILLED, result.getStatus());
        verify(strategyRisks).save(argThat(risk -> risk.getPositionId().equals(55L)
                && risk.getStopPrice().compareTo(new BigDecimal("69000")) == 0
                && risk.getTargetPrice().compareTo(new BigDecimal("72000")) == 0));
    }

    @Test
    void strategyEntryFillRejectsUnsafeStopAndTargetBeforeMutatingPosition() {
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 1), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 13L);
        when(fills.findByFillKey(any())).thenReturn(Optional.empty());
        when(orders.findByIdForUpdate(13L)).thenReturn(Optional.of(order));

        assertThrows(IllegalArgumentException.class, () -> serviceWithStrategyRisks().recordPaperFill(13L,
                new PaperFillRequest(UUID.randomUUID(), 1, new BigDecimal("70000"), new BigDecimal("71000"), new BigDecimal("72000"))));
        verify(positions, never()).save(any());
        verify(strategyRisks, never()).save(any());
    }

    @Test
    void closingStrategyPositionRemovesItsPersistentExitRisk() {
        OrderRequest sellRequest = new OrderRequest(Market.KR, "005930", OrderSide.SELL, 2,
                new BigDecimal("72000"), Currency.KRW);
        OrderRecord order = new OrderRecord(sellRequest, UUID.randomUUID());
        ReflectionTestUtils.setField(order, "id", 14L);
        PaperPosition position = new PaperPosition(Market.KR, "005930", Currency.KRW);
        position.apply(OrderSide.BUY, 2, new BigDecimal("70000"));
        ReflectionTestUtils.setField(position, "id", 56L);
        when(fills.findByFillKey(any())).thenReturn(Optional.empty());
        when(orders.findByIdForUpdate(14L)).thenReturn(Optional.of(order));
        when(positions.findInstrumentForUpdate(Market.KR, "005930", Currency.KRW)).thenReturn(Optional.of(position));
        when(positions.save(any(PaperPosition.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(cashBalances.findByCurrencyForUpdate(Currency.KRW))
                .thenReturn(Optional.of(new PaperCashBalance(Currency.KRW, new BigDecimal("100000"))));
        when(cashBalances.save(any(PaperCashBalance.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(orders.save(any(OrderRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));

        OrderRecord result = serviceWithStrategyRisks().recordPaperFill(14L,
                new PaperFillRequest(UUID.randomUUID(), 2, new BigDecimal("72000")));

        assertEquals(OrderStatus.FILLED, result.getStatus());
        assertEquals(0, position.getQuantity());
        verify(strategyRisks).deleteByPositionId(56L);
    }

    @Test
    void paperPositionPreventsSellingMoreThanHeld() {
        PaperPosition position = new PaperPosition(Market.KR, "005930", Currency.KRW);
        position.apply(OrderSide.BUY, 2, new BigDecimal("70000"));
        assertEquals(2, position.getQuantity());
        assertThrows(IllegalArgumentException.class,
                () -> position.apply(OrderSide.SELL, 3, new BigDecimal("71000")));
        position.apply(OrderSide.SELL, 2, new BigDecimal("71000"));
        assertEquals(0, position.getQuantity());
    }

    @Test
    void paperCashRejectsOverspendingWithoutChangingBalance() {
        PaperCashBalance cash = new PaperCashBalance(Currency.USD, new BigDecimal("100.00"));
        cash.debit(new BigDecimal("80.00"));
        assertEquals(new BigDecimal("20.00"), cash.getBalance());
        assertThrows(IllegalArgumentException.class, () -> cash.debit(new BigDecimal("20.01")));
        assertEquals(new BigDecimal("20.00"), cash.getBalance());
    }

    @Test
    void paperOrderCancellationAndExpiryStopFutureFills() {
        OrderRecord cancelled = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        cancelled.cancelPaperOrder();
        assertEquals(OrderStatus.CANCELLED, cancelled.getStatus());
        assertThrows(IllegalArgumentException.class,
                () -> cancelled.applyFill(1, new BigDecimal("70000")));

        OrderRecord expired = new OrderRecord(request(new BigDecimal("70000"), 2), UUID.randomUUID());
        expired.applyFill(1, new BigDecimal("70000"));
        expired.expirePaperOrder();
        assertEquals(OrderStatus.EXPIRED, expired.getStatus());
        assertEquals(1, expired.getFilledQuantity());
        assertThrows(IllegalArgumentException.class,
                () -> expired.applyFill(1, new BigDecimal("70000")));
    }

    @Test
    void repeatedPaperFillKeyReturnsPriorOrderWithoutApplyingTheFillTwice() {
        UUID fillKey = UUID.randomUUID();
        PaperFillRequest fillRequest = new PaperFillRequest(fillKey, 1, new BigDecimal("70000"));
        PaperFillRecord prior = new PaperFillRecord(11L, fillRequest);
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 11L);
        order.applyFill(1, new BigDecimal("70000"));
        when(fills.findByFillKey(fillKey)).thenReturn(Optional.of(prior));
        when(orders.findById(11L)).thenReturn(Optional.of(order));

        OrderRecord result = service().recordPaperFill(11L, fillRequest);

        assertSame(order, result);
        assertEquals(1, result.getFilledQuantity());
        verify(orders, never()).save(any());
        verify(positions, never()).save(any());
    }

    @Test
    void reusedPaperFillKeyWithDifferentDetailsIsRejected() {
        UUID fillKey = UUID.randomUUID();
        PaperFillRecord prior = new PaperFillRecord(11L,
                new PaperFillRequest(fillKey, 1, new BigDecimal("70000")));
        when(fills.findByFillKey(fillKey)).thenReturn(Optional.of(prior));

        assertThrows(IdempotencyConflictException.class, () -> service().recordPaperFill(11L,
                new PaperFillRequest(fillKey, 2, new BigDecimal("70000"))));
        verify(orders, never()).findById(any());
        verify(positions, never()).save(any());
    }

    @Test
    void reusedPaperFillKeyWithDifferentStrategyRiskIsRejected() {
        UUID fillKey = UUID.randomUUID();
        PaperFillRecord prior = new PaperFillRecord(11L,
                new PaperFillRequest(fillKey, 1, new BigDecimal("70000"), new BigDecimal("69000"), new BigDecimal("72000")));
        when(fills.findByFillKey(fillKey)).thenReturn(Optional.of(prior));

        assertThrows(IdempotencyConflictException.class, () -> serviceWithStrategyRisks().recordPaperFill(11L,
                new PaperFillRequest(fillKey, 1, new BigDecimal("70000"), new BigDecimal("69500"), new BigDecimal("72000"))));
        verify(orders, never()).findById(any());
        verify(strategyRisks, never()).save(any());
    }

    @Test
    void amendsOpenPaperOrderAndPersistsThePriorAndNewTerms() {
        UUID amendmentKey = UUID.randomUUID();
        OrderRecord order = spy(new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey));
        ReflectionTestUtils.setField(order, "id", 11L);
        when(orders.findByIdForUpdate(11L)).thenReturn(Optional.of(order));
        when(amendments.findByOrderIdAndIdempotencyKey(11L, amendmentKey)).thenReturn(Optional.empty());
        when(amendments.save(any(PaperOrderAmendment.class))).thenAnswer(call -> call.getArgument(0));
        when(orders.save(order)).thenReturn(order);

        OrderRecord result = serviceWithAmendments().amendPaperOrder(11L, amendmentKey,
                new PaperOrderAmendmentRequest(3, new BigDecimal("71000")));

        assertSame(order, result);
        assertEquals(3, result.getQuantity());
        assertEquals(0, result.getFilledQuantity());
        assertEquals(new BigDecimal("71000"), result.getLimitPrice());
        assertEquals(OrderStatus.PENDING, result.getStatus());
        verify(amendments).save(argThat(record -> record.getOrderId().equals(11L)
                && record.getIdempotencyKey().equals(amendmentKey)
                && record.getOldQuantity() == 2
                && record.getNewQuantity() == 3
                && record.getOldLimitPrice().compareTo(new BigDecimal("70000")) == 0
                && record.getNewLimitPrice().compareTo(new BigDecimal("71000")) == 0));
        verify(orders).save(order);
        verify(order, times(1)).amendPaperOrder(3, new BigDecimal("71000"));
    }

    @Test
    void amendmentCannotReduceQuantityBelowAlreadyFilledAmount() {
        UUID amendmentKey = UUID.randomUUID();
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 3), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 12L);
        order.applyFill(2, new BigDecimal("70000"));
        when(orders.findByIdForUpdate(12L)).thenReturn(Optional.of(order));
        when(amendments.findByOrderIdAndIdempotencyKey(12L, amendmentKey)).thenReturn(Optional.empty());

        assertThrows(IllegalArgumentException.class, () -> serviceWithAmendments().amendPaperOrder(12L,
                amendmentKey, new PaperOrderAmendmentRequest(1, new BigDecimal("71000"))));

        assertEquals(3, order.getQuantity());
        assertEquals(OrderStatus.PARTIALLY_FILLED, order.getStatus());
        verify(amendments, never()).save(any());
        verify(orders, never()).save(any());
    }

    @Test
    void amendsPartiallyFilledOrderWithoutChangingItsFillHistory() {
        UUID amendmentKey = UUID.randomUUID();
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 3), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 14L);
        order.applyFill(1, new BigDecimal("69900"));
        when(orders.findByIdForUpdate(14L)).thenReturn(Optional.of(order));
        when(amendments.findByOrderIdAndIdempotencyKey(14L, amendmentKey)).thenReturn(Optional.empty());
        when(amendments.save(any(PaperOrderAmendment.class))).thenAnswer(call -> call.getArgument(0));
        when(orders.save(order)).thenReturn(order);

        OrderRecord result = serviceWithAmendments().amendPaperOrder(14L, amendmentKey,
                new PaperOrderAmendmentRequest(4, new BigDecimal("70500")));

        assertSame(order, result);
        assertEquals(4, result.getQuantity());
        assertEquals(1, result.getFilledQuantity());
        assertEquals(new BigDecimal("69900.000000"), result.getAverageFillPrice());
        assertEquals(OrderStatus.PARTIALLY_FILLED, result.getStatus());
    }

    @Test
    void repeatedIdenticalAmendmentKeyDoesNotWriteAnotherRevision() {
        UUID amendmentKey = UUID.randomUUID();
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 13L);
        PaperOrderAmendment previous = new PaperOrderAmendment(13L, amendmentKey, 2, 3,
                new BigDecimal("70000"), new BigDecimal("71000"));
        order.amendPaperOrder(3, new BigDecimal("71000"));
        when(orders.findByIdForUpdate(13L)).thenReturn(Optional.of(order));
        when(amendments.findByOrderIdAndIdempotencyKey(13L, amendmentKey)).thenReturn(Optional.of(previous));

        OrderRecord result = serviceWithAmendments().amendPaperOrder(13L, amendmentKey,
                new PaperOrderAmendmentRequest(3, new BigDecimal("71000")));

        assertSame(order, result);
        verify(amendments, never()).save(any());
        verify(orders, never()).save(any());
    }

    @Test
    void amendmentKeyCannotBeReusedWithDifferentValues() {
        UUID amendmentKey = UUID.randomUUID();
        OrderRecord order = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 15L);
        PaperOrderAmendment previous = new PaperOrderAmendment(15L, amendmentKey, 2, 3,
                new BigDecimal("70000"), new BigDecimal("71000"));
        when(orders.findByIdForUpdate(15L)).thenReturn(Optional.of(order));
        when(amendments.findByOrderIdAndIdempotencyKey(15L, amendmentKey)).thenReturn(Optional.of(previous));

        assertThrows(IdempotencyConflictException.class, () -> serviceWithAmendments().amendPaperOrder(15L,
                amendmentKey, new PaperOrderAmendmentRequest(4, new BigDecimal("72000"))));

        assertEquals(2, order.getQuantity());
        assertEquals(new BigDecimal("70000"), order.getLimitPrice());
        verify(amendments, never()).save(any());
        verify(orders, never()).save(any());
    }

    @Test
    void configuredPaperTimeoutExpiresOpenOrdersButZeroDisablesTheSweep() {
        OrderRecord pending = new OrderRecord(request(new BigDecimal("70000"), 2), idempotencyKey);
        OrderRecord partial = new OrderRecord(request(new BigDecimal("70000"), 2), UUID.randomUUID());
        partial.applyFill(1, new BigDecimal("70000"));
        when(orders.findOpenOrdersCreatedBefore(anyList(), any(Instant.class)))
                .thenReturn(List.of(pending, partial));
        when(orders.saveAll(any(Iterable.class))).thenReturn(List.of(pending, partial));

        assertEquals(2, service().expireStalePaperOrders(30));
        assertEquals(OrderStatus.EXPIRED, pending.getStatus());
        assertEquals(OrderStatus.EXPIRED, partial.getStatus());
        assertEquals(0, service().expireStalePaperOrders(0));
        verify(orders, times(1)).findOpenOrdersCreatedBefore(anyList(), any(Instant.class));
    }

    private OrderService service() {
        allowFillInsert();
        return new OrderService(orders, safeConfig(), fills, positions, cashBalances);
    }
    private OrderService serviceWithStrategyRisks() {
        allowFillInsert();
        return new OrderService(orders, safeConfig(), fills, positions, cashBalances, strategyRisks);
    }
    private OrderService serviceWithAmendments() {
        allowFillInsert();
        return new OrderService(orders, safeConfig(), fills, positions, cashBalances, strategyRisks, amendments);
    }
    private void allowFillInsert() {
        lenient().when(fills.insertIfFillKeyAbsent(any(), any(), anyInt(), any(), any(), any(), any(), any()))
                .thenReturn(1);
    }
}
