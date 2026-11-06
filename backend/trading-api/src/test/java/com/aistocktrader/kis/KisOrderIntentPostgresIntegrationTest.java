package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.IdempotencyConflictException;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.OrderService;
import com.aistocktrader.order.PaperCashBalance;
import com.aistocktrader.order.PaperCashRepository;
import com.aistocktrader.order.PaperFillRepository;
import com.aistocktrader.order.PaperFillRequest;
import com.aistocktrader.order.PaperPositionRepository;
import com.aistocktrader.TradingApiApplication;
import org.springframework.transaction.annotation.Transactional;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ConfigurableApplicationContext;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

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
    @Autowired PaperCashRepository paperCashRepository;
    @Autowired PaperFillRepository paperFillRepository;
    @Autowired PaperPositionRepository paperPositionRepository;
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
    @Transactional
    void persistsCancelRejectionUntilLaterBrokerReconciliationRestoresOpenOrder() {
        UUID key = UUID.randomUUID();
        try {
            store.prepare(key, hash("cancel-rejected"), KisEnvironment.PAPER, request(), null);
            store.accept(key, receipt("paper", "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));
            store.markCancelPending(key);
            store.recordCancelRejection(key, "EGW00201");
            entityManager.flush();
            entityManager.clear();

            KisOrderIntent rejected = repository.findById(key).orElseThrow();
            assertEquals(KisOrderStatus.CANCEL_PENDING, rejected.getStatus());
            assertEquals("EGW00201", rejected.getBrokerResponseCode());

            store.reconcile(key, new KisBrokerOrderSnapshot(0, 1, null, false, false));
            entityManager.flush();
            entityManager.clear();

            KisOrderIntent recovered = repository.findById(key).orElseThrow();
            assertEquals(KisOrderStatus.ACCEPTED, recovered.getStatus());
            assertEquals(1, recovered.getRemainingQuantity());
            assertEquals("EGW00201", recovered.getBrokerResponseCode());
        } finally {
            repository.deleteById(key);
        }
    }

    @Test
    @Transactional
    void persistsRealCancelRejectionUntilLaterBrokerReconciliationRestoresOpenOrder() {
        UUID key = UUID.randomUUID();
        try {
            store.prepare(key, hash("real-cancel-rejected"), KisEnvironment.REAL, request(), null);
            store.accept(key, receipt("real", "IR" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));
            store.markCancelPending(key);
            store.recordCancelRejection(key, "EGW00201");
            entityManager.flush();
            entityManager.clear();

            KisOrderIntent rejected = repository.findById(key).orElseThrow();
            assertEquals(KisEnvironment.REAL, rejected.getEnvironment());
            assertEquals(KisOrderStatus.CANCEL_PENDING, rejected.getStatus());
            assertEquals("EGW00201", rejected.getBrokerResponseCode());

            store.reconcile(key, new KisBrokerOrderSnapshot(0, 1, null, false, false));
            entityManager.flush();
            entityManager.clear();

            KisOrderIntent recovered = repository.findById(key).orElseThrow();
            assertEquals(KisOrderStatus.ACCEPTED, recovered.getStatus());
            assertEquals(1, recovered.getRemainingQuantity());
            assertEquals("EGW00201", recovered.getBrokerResponseCode());
        } finally {
            repository.deleteById(key);
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

    @Test
    void concurrentPaperSubmissionsWithTheSameKeyResolveToOnePersistedOrder() throws Exception {
        UUID key = UUID.randomUUID();
        var executor = Executors.newFixedThreadPool(2);
        var start = new CountDownLatch(1);
        try {
            var first = executor.submit(() -> {
                start.await();
                return paperOrders.submit(request(), key);
            });
            var second = executor.submit(() -> {
                start.await();
                return paperOrders.submit(request(), key);
            });
            start.countDown();

            var firstResult = first.get(15, TimeUnit.SECONDS);
            var secondResult = second.get(15, TimeUnit.SECONDS);

            assertEquals(firstResult.getId(), secondResult.getId());
            assertEquals(1L, paperOrderRepository.findByIdempotencyKey(key).stream().count());
        } finally {
            executor.shutdownNow();
            paperOrderRepository.findByIdempotencyKey(key).ifPresent(paperOrderRepository::delete);
        }
    }

    @Test
    @Transactional
    void paperFillAndCashSettlementPersistOnceForAnIdempotentFillKey() {
        UUID orderKey = UUID.randomUUID();
        UUID fillKey = UUID.randomUUID();
        String symbol = String.format(java.util.Locale.ROOT, "%06d",
                java.util.concurrent.ThreadLocalRandom.current().nextInt(100000, 1_000_000));
        OrderRequest request = new OrderRequest(Market.KR, symbol, OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);

        paperCashRepository.save(new PaperCashBalance(Currency.KRW, new BigDecimal("1000000")));
        var order = paperOrders.submit(request, orderKey);
        var fillRequest = new PaperFillRequest(fillKey, 1, new BigDecimal("70000"));

        paperOrders.recordPaperFill(order.getId(), fillRequest);
        BigDecimal settledCash = paperCashRepository.findById(Currency.KRW).orElseThrow().getBalance();
        paperOrders.recordPaperFill(order.getId(), fillRequest);
        entityManager.flush();
        entityManager.clear();

        var recoveredOrder = paperOrders.getPaperOrderByIdempotencyKey(orderKey);
        BigDecimal recoveredCash = paperCashRepository.findById(Currency.KRW).orElseThrow().getBalance();
        var position = paperPositionRepository.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0).stream()
                .filter(item -> item.getSymbol().equals(symbol)).findFirst().orElseThrow();

        assertEquals(com.aistocktrader.order.OrderStatus.FILLED, recoveredOrder.getStatus());
        assertEquals(1, recoveredOrder.getFilledQuantity());
        assertEquals(0, settledCash.compareTo(recoveredCash));
        assertEquals(1, position.getQuantity());
        assertEquals(1L, paperFillRepository.findByFillKey(fillKey).stream().count());
    }

    @Test
    @Transactional
    void paperStrategyAndModelAttributionSurvivesEntryPositionAndExitFill() {
        String modelId = "lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        String symbol = String.format(java.util.Locale.ROOT, "%06d",
                java.util.concurrent.ThreadLocalRandom.current().nextInt(100000, 1_000_000));
        paperCashRepository.save(new PaperCashBalance(Currency.KRW, new BigDecimal("1000000")));
        var buy = paperOrders.submit(new OrderRequest(Market.KR, symbol, OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW, "ORB_VWAP", modelId), UUID.randomUUID());
        paperOrders.recordPaperFill(buy.getId(), new PaperFillRequest(UUID.randomUUID(), 1,
                new BigDecimal("70000"), new BigDecimal("69000"), new BigDecimal("71000")));
        entityManager.flush();
        entityManager.clear();

        var risk = paperOrders.paperStrategyRisks().stream()
                .filter(item -> item.symbol().equals(symbol)).findFirst().orElseThrow();
        assertEquals("ORB_VWAP", risk.strategyId());
        assertEquals(modelId, risk.modelId());

        var sell = paperOrders.submit(new OrderRequest(Market.KR, symbol, OrderSide.SELL, 1,
                new BigDecimal("71000"), Currency.KRW, risk.strategyId(), risk.modelId()), UUID.randomUUID());
        paperOrders.recordPaperFill(sell.getId(), new PaperFillRequest(UUID.randomUUID(), 1, new BigDecimal("71000")));
        entityManager.flush();
        entityManager.clear();

        var sellFill = paperFillRepository.findAllWithTradeDetails().stream()
                .filter(item -> item.getOrderId().equals(sell.getId())).findFirst().orElseThrow();
        assertEquals("ORB_VWAP", sellFill.getStrategyId());
        assertEquals(modelId, sellFill.getModelId());
    }

    @Test
    void pendingPaperOrderCanBeRecoveredAfterSpringApplicationContextRestart() {
        UUID key = UUID.randomUUID();
        try {
            Long submittedId;
            try (ConfigurableApplicationContext firstRun = new SpringApplicationBuilder(TradingApiApplication.class)
                    .web(WebApplicationType.NONE).properties("spring.main.banner-mode=off").run()) {
                submittedId = firstRun.getBean(OrderService.class).submit(request(), key).getId();
            }

            try (ConfigurableApplicationContext restarted = new SpringApplicationBuilder(TradingApiApplication.class)
                    .web(WebApplicationType.NONE).properties("spring.main.banner-mode=off").run()) {
                OrderService restartedOrders = restarted.getBean(OrderService.class);
                var recovered = restartedOrders.getPaperOrderByIdempotencyKey(key);
                var replayed = restartedOrders.submit(request(), key);

                assertEquals(submittedId, recovered.getId());
                assertEquals(submittedId, replayed.getId());
                assertEquals(com.aistocktrader.order.OrderStatus.PENDING, recovered.getStatus());
            }
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
