package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.IdempotencyConflictException;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.OrderService;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** Opt-in local PostgreSQL persistence test. Does not make KIS network requests. */
@EnabledIfEnvironmentVariable(named = "RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", matches = "true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class KisOrderIntentPostgresIntegrationTest {
    @Autowired KisOrderIntentStore store;
    @Autowired KisOrderIntentRepository repository;
    @Autowired OrderService paperOrders;
    @Autowired com.aistocktrader.order.OrderRepository paperOrderRepository;
    @Autowired EntityManager entityManager;

    @Test
    void persistsAndReloadsPaperAndRealIntentsWithSameBrokerNumberAcrossEnvironments() {
        UUID realKey = UUID.randomUUID();
        UUID paperKey = UUID.randomUUID();
        String brokerNumber = "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        OrderRequest request = request();
        try {
            store.prepare(realKey, hash("real"), KisEnvironment.REAL, request, null);
            store.accept(realKey, receipt("real", brokerNumber));
            store.prepare(paperKey, hash("paper"), KisEnvironment.PAPER, request, null);
            store.accept(paperKey, receipt("paper", brokerNumber));

            KisOrderIntent reloadedReal = repository.findById(realKey).orElseThrow();
            KisOrderIntent reloadedPaper = repository.findById(paperKey).orElseThrow();
            assertEquals(KisEnvironment.REAL, reloadedReal.getEnvironment());
            assertEquals(KisEnvironment.PAPER, reloadedPaper.getEnvironment());
            assertEquals(KisOrderStatus.ACCEPTED, reloadedReal.getStatus());
            assertEquals(KisOrderStatus.ACCEPTED, reloadedPaper.getStatus());
            assertEquals(brokerNumber, reloadedReal.getBrokerOrderNumber());
            assertEquals(brokerNumber, reloadedPaper.getBrokerOrderNumber());
        } finally {
            repository.deleteAllById(List.of(realKey, paperKey));
        }
    }

    @Test
    void durableIdempotencyRejectsCrossEnvironmentKeyReuse() {
        UUID key = UUID.randomUUID();
        OrderRequest request = request();
        try {
            store.prepare(key, hash("same"), KisEnvironment.PAPER, request, null);
            assertThrows(IdempotencyConflictException.class,
                    () -> store.prepare(key, hash("same"), KisEnvironment.REAL, request, null));
            assertEquals(KisEnvironment.PAPER, repository.findById(key).orElseThrow().getEnvironment());
        } finally {
            repository.deleteById(key);
        }
    }

    @Test
    void pendingPaperOrderCanBeRecoveredByIdempotencyKeyAfterPersistenceContextIsCleared() {
        UUID key = UUID.randomUUID();
        try {
            var submitted = paperOrders.submit(request(), key);
            entityManager.clear();

            var recovered = paperOrders.getPaperOrderByIdempotencyKey(key);
            var retried = paperOrders.submit(request(), key);

            assertEquals(submitted.getId(), recovered.getId());
            assertEquals(submitted.getId(), retried.getId());
            assertEquals(com.aistocktrader.order.OrderStatus.PENDING, recovered.getStatus());
            assertEquals(1L, paperOrderRepository.findByIdempotencyKey(key).stream().count());
        } finally {
            paperOrderRepository.findByIdempotencyKey(key).ifPresent(paperOrderRepository::delete);
        }
    }

    private static OrderRequest request() {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
    }

    private static KisOrderReceipt receipt(String environment, String brokerNumber) {
        return new KisOrderReceipt(environment, "KR", "005930", "BUY", brokerNumber,
                "ITORG01", "0", Instant.now());
    }

    private static String hash(String label) {
        return label + "-" + UUID.randomUUID();
    }
}
