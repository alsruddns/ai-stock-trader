package com.aistocktrader.order;

import com.aistocktrader.config.TradingSafetyProperties;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DomesticPaperCostSafetyTest {
    @Mock OrderRepository orders;
    @Mock PaperFillRepository fills;
    @Mock PaperPositionRepository positions;
    @Mock PaperCashRepository cashBalances;
    @Mock PaperStrategyRiskRepository strategyRisks;
    @Mock PaperOrderAmendmentRepository amendments;

    private final UUID idempotencyKey = UUID.fromString("5c3aa218-4e89-49e3-a889-56549c37516d");

    @Test
    void automatedBuySubmissionIsBlockedWhenDomesticPaperCostsAreUnavailable() {
        PaperTransactionCostCalculator calculator = calculator(false, BigDecimal.ZERO,
                BigDecimal.ZERO);
        when(orders.findByIdempotencyKey(idempotencyKey)).thenReturn(Optional.empty());

        assertThrows(IllegalStateException.class, () -> service(calculator).submit(
                new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1, new BigDecimal("70000"), Currency.KRW),
                idempotencyKey));

        verify(orders, never()).insertIfIdempotencyKeyAbsent(any(), any(), any(), anyInt(), any(), any(),
                any(), any(), any());
    }

    @Test
    void automatedBuyFillIsBlockedWhenDomesticPaperCostsAreUnavailable() {
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        OrderRecord order = new OrderRecord(request, idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 31L);
        when(fills.findByFillKey(any())).thenReturn(Optional.empty());
        when(orders.findByIdForUpdate(31L)).thenReturn(Optional.of(order));

        assertThrows(IllegalStateException.class, () -> service(calculator(true, BigDecimal.ZERO,
                new BigDecimal("20"))).recordPaperFill(31L,
                new PaperFillRequest(UUID.randomUUID(), 1, new BigDecimal("70000"))));

        verify(fills, never()).insertIfFillKeyAbsent(any(), any(), anyInt(), any(), any(), any(), any(), any());
        verify(cashBalances, never()).findByCurrencyForUpdate(any());
    }

    @Test
    void paperSellFillRemainsAvailableForExitWhenDomesticPaperCostsAreUnavailable() {
        OrderRequest sellRequest = new OrderRequest(Market.KR, "005930", OrderSide.SELL, 1,
                new BigDecimal("71000"), Currency.KRW);
        OrderRecord order = new OrderRecord(sellRequest, idempotencyKey);
        ReflectionTestUtils.setField(order, "id", 32L);
        PaperPosition position = new PaperPosition(Market.KR, "005930", Currency.KRW);
        position.apply(OrderSide.BUY, 1, new BigDecimal("70000"));
        when(fills.findByFillKey(any())).thenReturn(Optional.empty());
        when(orders.findByIdForUpdate(32L)).thenReturn(Optional.of(order));
        when(positions.findInstrumentForUpdate(Market.KR, "005930", Currency.KRW)).thenReturn(Optional.of(position));
        when(positions.save(any(PaperPosition.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(cashBalances.findByCurrencyForUpdate(Currency.KRW))
                .thenReturn(Optional.of(new PaperCashBalance(Currency.KRW, new BigDecimal("100000"))));
        when(cashBalances.save(any(PaperCashBalance.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(orders.save(any(OrderRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));

        OrderRecord result = service(calculator(false, BigDecimal.ZERO, BigDecimal.ZERO)).recordPaperFill(32L,
                new PaperFillRequest(UUID.randomUUID(), 1, new BigDecimal("71000")));

        assertEquals(OrderStatus.FILLED, result.getStatus());
        assertEquals(0, position.getQuantity());
    }

    private OrderService service(PaperTransactionCostCalculator calculator) {
        lenient().when(fills.insertIfFillKeyAbsent(any(), any(), anyInt(), any(), any(), any(), any(), any()))
                .thenReturn(1);
        TradingSafetyProperties safeConfig = new TradingSafetyProperties("paper", false,
                new BigDecimal("1000000"), new BigDecimal("100"));
        return new OrderService(orders, safeConfig, fills, positions, cashBalances, strategyRisks,
                calculator, amendments, true);
    }

    private PaperTransactionCostCalculator calculator(boolean enabled, BigDecimal krCommissionBps,
                                                       BigDecimal krSellCostBps) {
        return new PaperTransactionCostCalculator(new PaperCostProperties(enabled, krCommissionBps,
                krSellCostBps, BigDecimal.ZERO, BigDecimal.ZERO));
    }
}
