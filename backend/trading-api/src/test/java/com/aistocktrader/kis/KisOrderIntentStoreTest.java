package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.IdempotencyConflictException;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class KisOrderIntentStoreTest {
    @Mock KisOrderIntentRepository repository;

    @Test
    void idempotencyKeyCannotBeReusedAcrossPaperAndRealEnvironments() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent paperIntent = new KisOrderIntent(key, "same-hash", request, null);
        when(repository.findById(key)).thenReturn(Optional.of(paperIntent));
        KisOrderIntentStore store = new KisOrderIntentStore(repository);

        assertThrows(IdempotencyConflictException.class,
                () -> store.prepare(key, "same-hash", KisEnvironment.REAL, request, null));
    }

    @Test
    void identicalRequestInSameEnvironmentReturnsExistingIntent() {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        KisOrderIntent paperIntent = new KisOrderIntent(key, "same-hash", request, null);
        when(repository.findById(key)).thenReturn(Optional.of(paperIntent));
        KisOrderIntentStore store = new KisOrderIntentStore(repository);

        var prepared = store.prepare(key, "same-hash", KisEnvironment.PAPER, request, null);

        assertFalse(prepared.createdNow());
        assertSame(paperIntent, prepared.intent());
    }
}
