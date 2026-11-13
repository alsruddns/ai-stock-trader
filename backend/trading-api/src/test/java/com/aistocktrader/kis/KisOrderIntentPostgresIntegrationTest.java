package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.IdempotencyConflictException;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderRecord;
import com.aistocktrader.order.OrderSide;
import com.aistocktrader.order.OrderService;
import com.aistocktrader.order.PaperCashBalance;
import com.aistocktrader.order.PaperCashRepository;
import com.aistocktrader.order.PaperFillRepository;
import com.aistocktrader.order.PaperFillRequest;
import com.aistocktrader.order.PaperOrderAmendment;
import com.aistocktrader.order.PaperOrderAmendmentRepository;
import com.aistocktrader.order.PaperOrderAmendmentRequest;
import com.aistocktrader.order.PaperPositionRepository;
import com.aistocktrader.order.PaperStrategyRiskRepository;
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
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.PageRequest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import com.aistocktrader.config.TradingSafetyProperties;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.math.BigDecimal;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.queryParam;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

/** Opt-in local PostgreSQL persistence test. Does not make KIS network requests. */
@EnabledIfEnvironmentVariable(named = "RUN_LOCAL_POSTGRES_INTEGRATION_TESTS", matches = "true")
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE)
class KisOrderIntentPostgresIntegrationTest {
    @DynamicPropertySource
    static void enableOnlyPaperOrderOrchestration(DynamicPropertyRegistry properties) {
        properties.add("kis.orders.paper-enabled", () -> true);
        properties.add("trading.mode", () -> "paper");
        properties.add("trading.live-trading-enabled", () -> false);
    }

    @Autowired KisOrderIntentStore store;
    @Autowired KisOrderIntentRepository repository;
    @Autowired OrderService paperOrders;
    @MockitoSpyBean com.aistocktrader.order.OrderRepository paperOrderRepository;
    @Autowired PaperCashRepository paperCashRepository;
    @Autowired PaperFillRepository paperFillRepository;
    @Autowired PaperPositionRepository paperPositionRepository;
    @Autowired PaperStrategyRiskRepository paperStrategyRiskRepository;
    @Autowired PaperOrderAmendmentRepository paperOrderAmendments;
    @Autowired TradingSafetyProperties tradingSafetyProperties;
    @Autowired JdbcTemplate jdbcTemplate;
    @Autowired EntityManager entityManager;
    @Autowired KisOrderOrchestrator kisOrders;
    @Autowired KisOrderIntentRepository kisOrderRepository;
    @MockitoBean KisOrderClient kisOrderClient;
    @MockitoBean KisAccountClient kisAccountClient;
    @MockitoBean KisMarketSessionClient kisMarketSessionClient;
    @MockitoBean KisMarketAiEvidenceClient kisMarketAiEvidenceClient;
    @MockitoBean KisRealOrderOrchestrator kisRealOrderOrchestrator;

    @Test
    void paperOrderAmendmentAuditFailureRollsBackTheOrderTermsInPostgres() {
        UUID orderKey = UUID.randomUUID();
        UUID amendmentKey = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        var created = paperOrders.submit(request, orderKey);
        doThrow(new DataIntegrityViolationException("simulated order persistence failure after audit insert"))
                .when(paperOrderRepository).save(any(OrderRecord.class));

        try {
            assertThrows(DataIntegrityViolationException.class, () -> paperOrders.amendPaperOrder(created.getId(),
                    amendmentKey, new PaperOrderAmendmentRequest(3, new BigDecimal("71000"))));

            var reloaded = paperOrderRepository.findById(created.getId()).orElseThrow();
            assertEquals(2, reloaded.getQuantity());
            assertEquals(new BigDecimal("70000.000000"), reloaded.getLimitPrice());
            assertTrue(paperOrderAmendments.findByOrderIdAndIdempotencyKey(created.getId(), amendmentKey).isEmpty());
        } finally {
            paperOrderRepository.deleteById(created.getId());
        }
    }

    @Test
    void concurrentDuplicatePaperAmendmentsWithSameKeyCreateOneHistoryRow() throws Exception {
        UUID orderKey = UUID.randomUUID();
        UUID amendmentKey = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        var created = paperOrders.submit(request, orderKey);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);

        try {
            var first = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.amendPaperOrder(created.getId(), amendmentKey,
                        new PaperOrderAmendmentRequest(3, new BigDecimal("71000")));
            });
            var second = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.amendPaperOrder(created.getId(), amendmentKey,
                        new PaperOrderAmendmentRequest(3, new BigDecimal("71000")));
            });

            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            var firstResult = first.get(10, TimeUnit.SECONDS);
            var secondResult = second.get(10, TimeUnit.SECONDS);
            assertEquals(3, firstResult.getQuantity());
            assertEquals(3, secondResult.getQuantity());
            assertEquals(0, new BigDecimal("71000").compareTo(firstResult.getLimitPrice()));
            assertEquals(0, new BigDecimal("71000").compareTo(secondResult.getLimitPrice()));

            PaperOrderAmendment history = paperOrderAmendments
                    .findByOrderIdAndIdempotencyKey(created.getId(), amendmentKey).orElseThrow();
            assertEquals(2, history.getOldQuantity());
            assertEquals(3, history.getNewQuantity());
            assertEquals(new BigDecimal("70000.000000"), history.getOldLimitPrice());
            assertEquals(new BigDecimal("71000.000000"), history.getNewLimitPrice());
        } finally {
            start.countDown();
            pool.shutdownNow();
            pool.awaitTermination(10, TimeUnit.SECONDS);
            paperOrderAmendments.findByOrderIdAndIdempotencyKey(created.getId(), amendmentKey)
                    .ifPresent(paperOrderAmendments::delete);
            paperOrderRepository.deleteById(created.getId());
        }
    }

    @Test
    void concurrentReuseOfPaperAmendmentKeyWithDifferentTermsRejectsTheLoser() throws Exception {
        UUID orderKey = UUID.randomUUID();
        UUID amendmentKey = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        var created = paperOrders.submit(request, orderKey);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);

        try {
            var first = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                try {
                    paperOrders.amendPaperOrder(created.getId(), amendmentKey,
                            new PaperOrderAmendmentRequest(3, new BigDecimal("71000")));
                    return true;
                } catch (IdempotencyConflictException expected) {
                    return false;
                }
            });
            var second = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                try {
                    paperOrders.amendPaperOrder(created.getId(), amendmentKey,
                            new PaperOrderAmendmentRequest(4, new BigDecimal("72000")));
                    return true;
                } catch (IdempotencyConflictException expected) {
                    return false;
                }
            });

            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            boolean firstApplied = first.get(10, TimeUnit.SECONDS);
            boolean secondApplied = second.get(10, TimeUnit.SECONDS);
            assertTrue(firstApplied ^ secondApplied);

            int expectedQuantity = firstApplied ? 3 : 4;
            BigDecimal expectedPrice = firstApplied ? new BigDecimal("71000") : new BigDecimal("72000");
            PaperOrderAmendment history = paperOrderAmendments
                    .findByOrderIdAndIdempotencyKey(created.getId(), amendmentKey).orElseThrow();
            var reloaded = paperOrderRepository.findById(created.getId()).orElseThrow();
            assertEquals(expectedQuantity, history.getNewQuantity());
            assertEquals(0, expectedPrice.compareTo(history.getNewLimitPrice()));
            assertEquals(expectedQuantity, reloaded.getQuantity());
            assertEquals(0, expectedPrice.compareTo(reloaded.getLimitPrice()));
        } finally {
            start.countDown();
            pool.shutdownNow();
            pool.awaitTermination(10, TimeUnit.SECONDS);
            paperOrderAmendments.findByOrderIdAndIdempotencyKey(created.getId(), amendmentKey)
                    .ifPresent(paperOrderAmendments::delete);
            paperOrderRepository.deleteById(created.getId());
        }
    }

    @Test
    void concurrentDuplicatePaperOrderSubmissionsWithSameKeyPersistOneOrder() throws Exception {
        UUID key = UUID.randomUUID();
        OrderRequest request = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);

        try {
            var first = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.submit(request, key);
            });
            var second = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.submit(request, key);
            });

            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            var firstResult = first.get(10, TimeUnit.SECONDS);
            var secondResult = second.get(10, TimeUnit.SECONDS);
            assertEquals(firstResult.getId(), secondResult.getId());

            var persisted = paperOrderRepository.findByIdempotencyKey(key).orElseThrow();
            assertEquals(firstResult.getId(), persisted.getId());
            assertEquals(2, persisted.getQuantity());
            assertEquals(0, new BigDecimal("70000").compareTo(persisted.getLimitPrice()));
        } finally {
            start.countDown();
            pool.shutdownNow();
            pool.awaitTermination(10, TimeUnit.SECONDS);
            paperOrderRepository.findByIdempotencyKey(key)
                    .ifPresent(order -> paperOrderRepository.deleteById(order.getId()));
        }
    }

    @Test
    void concurrentReuseOfPaperOrderKeyWithDifferentTermsRejectsTheLoser() throws Exception {
        UUID key = UUID.randomUUID();
        OrderRequest firstRequest = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        OrderRequest secondRequest = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 3,
                new BigDecimal("71000"), Currency.KRW);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);

        try {
            var first = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                try {
                    paperOrders.submit(firstRequest, key);
                    return true;
                } catch (IdempotencyConflictException expected) {
                    return false;
                }
            });
            var second = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                try {
                    paperOrders.submit(secondRequest, key);
                    return true;
                } catch (IdempotencyConflictException expected) {
                    return false;
                }
            });

            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            boolean firstApplied = first.get(10, TimeUnit.SECONDS);
            boolean secondApplied = second.get(10, TimeUnit.SECONDS);
            assertTrue(firstApplied ^ secondApplied);

            OrderRecord persisted = paperOrderRepository.findByIdempotencyKey(key).orElseThrow();
            OrderRequest winner = firstApplied ? firstRequest : secondRequest;
            assertEquals(winner.quantity(), persisted.getQuantity());
            assertEquals(0, winner.limitPrice().compareTo(persisted.getLimitPrice()));
        } finally {
            start.countDown();
            pool.shutdownNow();
            pool.awaitTermination(10, TimeUnit.SECONDS);
            paperOrderRepository.findByIdempotencyKey(key)
                    .ifPresent(order -> paperOrderRepository.deleteById(order.getId()));
        }
    }

    @Test
    @Transactional
    void realBuyProtectiveExitInputsSurvivePostgresReloadAndUseObservedPartialFill() {
        UUID key = UUID.randomUUID();
        OrderRequest realBuy = new OrderRequest(Market.US, "AAPL", OrderSide.BUY, 4,
                new BigDecimal("105"), Currency.USD, "ORB_VWAP", "approved-model-1");
        try {
            store.prepare(key, "a".repeat(64), KisEnvironment.REAL, realBuy, "NASD",
                    new BigDecimal("4"), new BigDecimal("2"));
            entityManager.flush();
            entityManager.clear();

            KisOrderIntent persisted = kisOrderRepository.findById(key).orElseThrow();
            persisted.reconcile(2, 0, new BigDecimal("104.500000"), true, false);
            entityManager.flush();
            entityManager.clear();
            KisOrderIntent reloaded = kisOrderRepository.findById(key).orElseThrow();

            assertEquals(KisEnvironment.REAL, reloaded.getEnvironment());
            assertEquals(KisRealExitStatus.ARMED, reloaded.getRealExitStatus());
            assertEquals(new BigDecimal("4.000000"), reloaded.getExitStopDistancePerShare());
            assertEquals(new BigDecimal("2.0000"), reloaded.getExitRewardRiskMultiple());
            assertEquals(new BigDecimal("100.500000"), reloaded.getExitStopPrice());
            assertEquals(0, reloaded.getExitTargetPrice().compareTo(new BigDecimal("112.500000")));
            assertEquals(List.of(key), kisOrderRepository.findExitManagedRealBuyCandidates(
                            KisEnvironment.REAL, Market.US, "aapl").stream()
                    .map(KisOrderIntent::getIdempotencyKey).toList());

            assertEquals(0, kisOrderRepository.claimRealExit(key, UUID.randomUUID(), "STOP_LOSS", 3,
                    new BigDecimal("100.500000")));
            UUID exitOrderKey = UUID.randomUUID();
            assertEquals(1, kisOrderRepository.claimRealExit(key, exitOrderKey, "STOP_LOSS", 2,
                    new BigDecimal("100.500000")));
            assertEquals(0, kisOrderRepository.claimRealExit(key, UUID.randomUUID(), "STOP_LOSS", 2,
                    new BigDecimal("100.500000")));
            KisOrderIntent claimed = kisOrderRepository.findById(key).orElseThrow();
            assertEquals(KisRealExitStatus.EXIT_PENDING, claimed.getRealExitStatus());
            assertEquals(exitOrderKey, claimed.getRealExitOrderKey());
            assertEquals(2, claimed.getRealExitQuantity());
            assertEquals(new BigDecimal("100.500000"), claimed.getRealExitLimitPrice());
            assertEquals(List.of(key), kisOrderRepository.findExitManagedRealBuyCandidates(
                            KisEnvironment.REAL, Market.US, "AAPL").stream()
                    .map(KisOrderIntent::getIdempotencyKey).toList());
            assertEquals(List.of(key), kisOrderRepository.findTop50ByEnvironmentAndRealExitStatusInOrderByUpdatedAtAsc(
                            KisEnvironment.REAL, List.of(KisRealExitStatus.ARMED, KisRealExitStatus.EXIT_PENDING)).stream()
                    .map(KisOrderIntent::getIdempotencyKey).toList());
            assertEquals(1, kisOrderRepository.markRealExitManualReview(key));
            entityManager.clear();
            assertEquals(KisRealExitStatus.MANUAL_REVIEW,
                    kisOrderRepository.findById(key).orElseThrow().getRealExitStatus());
        } finally {
            kisOrderRepository.deleteById(key);
        }
    }

    @Test
    @Transactional
    void realProtectiveExitMonitorClaimsFreshSellOnceAndPersistsPlanInPostgres() {
        UUID entryKey = UUID.randomUUID();
        OrderRequest buy = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("100"), Currency.KRW, "ORB_VWAP", "approved-model");
        try {
            store.prepare(entryKey, "b".repeat(64), KisEnvironment.REAL, buy, null,
                    new BigDecimal("5"), new BigDecimal("2"));
            entityManager.flush();
            KisOrderIntent entry = kisOrderRepository.findById(entryKey).orElseThrow();
            entry.reconcile(2, 0, new BigDecimal("100"), false, false);
            entityManager.flush();
            entityManager.clear();

            Instant now = Instant.now();
            when(kisMarketSessionClient.session(Market.KR)).thenReturn(new KisMarketSessionClient.MarketSession(
                    Market.KR, java.time.LocalDate.now(), true, true,
                    now.minusSeconds(3600), now.plusSeconds(3600), now));
            when(kisMarketAiEvidenceClient.quoteForExit(Market.KR, "005930", null)).thenReturn(
                    new KisMarketAiEvidenceClient.ExitQuote(Market.KR, "005930", null,
                            new BigDecimal("94"), new BigDecimal("93.99"), new BigDecimal("94.01"), now));
            var position = new KisAccountSnapshot.Position("005930", "Samsung", new BigDecimal("2"),
                    new BigDecimal("2"), new BigDecimal("100"), new BigDecimal("94"),
                    new BigDecimal("188"), new BigDecimal("-12"), new BigDecimal("-6"));
            when(kisAccountClient.getBalance(KisEnvironment.REAL, Market.KR, null, null, null)).thenReturn(
                    new KisAccountSnapshot(KisEnvironment.REAL, Market.KR, "KRW", new BigDecimal("1000"),
                            new BigDecimal("1000"), new BigDecimal("1188"), List.of(position), Instant.now(),
                            "KIS_READ_ONLY_REST"));
            var trading = new TradingSafetyProperties("real", true, true, true,
                    new BigDecimal("1000000"), new BigDecimal("100"));
            var kis = new KisProperties(true, false, true, false,
                    new KisProperties.Account("paper-key", "paper-secret", "1234567801"),
                    new KisProperties.Account("real-key", "real-secret", "1234567801"),
                    java.time.Duration.ofSeconds(3), java.time.Duration.ofSeconds(8), java.time.Duration.ofSeconds(1));
            var monitor = new KisRealProtectiveExitMonitor(true, true, trading, kis,
                    MarketOperationalScope.domesticOnly(), kisOrderRepository,
                    kisAccountClient, kisMarketSessionClient, kisMarketAiEvidenceClient, kisRealOrderOrchestrator);

            monitor.monitorRealPositions();
            entityManager.clear();

            KisOrderIntent claimed = kisOrderRepository.findById(entryKey).orElseThrow();
            assertEquals(KisRealExitStatus.EXIT_PENDING, claimed.getRealExitStatus());
            assertEquals("STOP_LOSS", claimed.getRealExitReason());
            assertEquals(2, claimed.getRealExitQuantity());
            assertEquals(new BigDecimal("93.990000"), claimed.getRealExitLimitPrice());
            assertNotNull(claimed.getRealExitOrderKey());
            verify(kisRealOrderOrchestrator).submit(eq(claimed.getRealExitOrderKey()), any(OrderRequest.class),
                    isNull(), any(KisAccountSnapshot.class));
        } finally {
            kisOrderRepository.deleteById(entryKey);
        }
    }

    @Test
    void concurrentPostgresProtectiveExitClaimsAllowOnlyOneWorker() throws Exception {
        UUID entryKey = UUID.randomUUID();
        UUID exitKeyA = UUID.randomUUID();
        UUID exitKeyB = UUID.randomUUID();
        var pool = Executors.newFixedThreadPool(2);
        try {
            OrderRequest buy = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                    new BigDecimal("100"), Currency.KRW);
            store.prepare(entryKey, "c".repeat(64), KisEnvironment.REAL, buy, null,
                    new BigDecimal("5"), new BigDecimal("2"));
            KisOrderIntent entry = kisOrderRepository.findById(entryKey).orElseThrow();
            entry.reconcile(1, 0, new BigDecimal("100"), false, false);
            kisOrderRepository.saveAndFlush(entry);

            var start = new CountDownLatch(1);
            var claimA = pool.submit(() -> {
                start.await();
                return kisOrderRepository.claimRealExit(entryKey, exitKeyA, "STOP_LOSS", 1,
                        new BigDecimal("95"));
            });
            var claimB = pool.submit(() -> {
                start.await();
                return kisOrderRepository.claimRealExit(entryKey, exitKeyB, "STOP_LOSS", 1,
                        new BigDecimal("95"));
            });
            start.countDown();

            int winners = claimA.get(10, TimeUnit.SECONDS) + claimB.get(10, TimeUnit.SECONDS);
            KisOrderIntent claimed = kisOrderRepository.findById(entryKey).orElseThrow();
            assertEquals(1, winners);
            assertEquals(KisRealExitStatus.EXIT_PENDING, claimed.getRealExitStatus());
            assertTrue(List.of(exitKeyA, exitKeyB).contains(claimed.getRealExitOrderKey()));
        } finally {
            pool.shutdownNow();
            kisOrderRepository.deleteById(entryKey);
        }
    }

    @Test
    @Transactional
    void mockBrokerSubmissionPersistsAcceptedOrderAndIdempotentRetryDoesNotResubmit() {
        UUID key = UUID.randomUUID();
        OrderRequest order = request();
        String brokerNumber = "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        when(kisAccountClient.getBalance(KisEnvironment.PAPER, Market.KR, order.symbol(), order.limitPrice(), ""))
                .thenReturn(new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW",
                        new BigDecimal("100000"), new BigDecimal("100000"), List.of(), Instant.now(), "test"));
        when(kisOrderClient.placePaperLimitOrder(Market.KR, order.symbol(), OrderSide.BUY, 1,
                order.limitPrice(), Currency.KRW, null))
                .thenReturn(receipt("paper", brokerNumber));

        try {
            var submitted = kisOrders.submitPaperOrder(key, order, null);
            entityManager.flush();
            entityManager.clear();

            var persisted = kisOrderRepository.findById(key).orElseThrow();
            var retried = kisOrders.submitPaperOrder(key, order, null);

            assertEquals(KisOrderStatus.ACCEPTED, submitted.status());
            assertEquals(KisOrderStatus.ACCEPTED, persisted.getStatus());
            assertEquals(brokerNumber, persisted.getBrokerOrderNumber());
            assertEquals(key, retried.idempotencyKey());
            verify(kisOrderClient, times(1)).placePaperLimitOrder(Market.KR, order.symbol(), OrderSide.BUY, 1,
                    order.limitPrice(), Currency.KRW, null);
        } finally {
            kisOrderRepository.deleteById(key);
        }
    }

    @Test
    @Transactional
    void mockBrokerTimeoutPersistsUnknownAndRetryDoesNotResend() {
        UUID key = UUID.randomUUID();
        OrderRequest order = request();
        when(kisAccountClient.getBalance(KisEnvironment.PAPER, Market.KR, order.symbol(), order.limitPrice(), ""))
                .thenReturn(new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW",
                        new BigDecimal("100000"), new BigDecimal("100000"), List.of(), Instant.now(), "test"));
        when(kisOrderClient.placePaperLimitOrder(Market.KR, order.symbol(), OrderSide.BUY, 1,
                order.limitPrice(), Currency.KRW, null))
                .thenThrow(new KisOrderOutcomeUnknownException("simulated timeout", new RuntimeException("mock")));

        try {
            var submitted = kisOrders.submitPaperOrder(key, order, null);
            entityManager.flush();
            entityManager.clear();

            var persisted = kisOrderRepository.findById(key).orElseThrow();
            var retried = kisOrders.submitPaperOrder(key, order, null);

            assertEquals(KisOrderStatus.UNKNOWN, submitted.status());
            assertEquals(KisOrderStatus.UNKNOWN, persisted.getStatus());
            assertEquals(KisOrderStatus.UNKNOWN, retried.status());
            verify(kisOrderClient, times(1)).placePaperLimitOrder(Market.KR, order.symbol(), OrderSide.BUY, 1,
                    order.limitPrice(), Currency.KRW, null);
        } finally {
            kisOrderRepository.deleteById(key);
        }
    }

    @Test
    void concurrentDistinctRealOrdersCannotBothReserveTheSameMarket() throws Exception {
        UUID firstKey = UUID.randomUUID();
        UUID secondKey = UUID.randomUUID();
        OrderRequest firstOrder = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        OrderRequest secondOrder = new OrderRequest(Market.KR, "000660", OrderSide.BUY, 1,
                new BigDecimal("100000"), Currency.KRW);
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);

        try (var pool = Executors.newFixedThreadPool(2)) {
            var first = pool.submit(() -> reserveRealOrder(firstKey, hash("real-race-1"), firstOrder, ready, start));
            var second = pool.submit(() -> reserveRealOrder(secondKey, hash("real-race-2"), secondOrder, ready, start));
            if (!ready.await(5, TimeUnit.SECONDS)) {
                throw new AssertionError("Concurrent REAL reservation workers did not become ready.");
            }
            start.countDown();

            var firstResult = first.get(10, TimeUnit.SECONDS);
            var secondResult = second.get(10, TimeUnit.SECONDS);
            assertEquals(1, (firstResult ? 1 : 0) + (secondResult ? 1 : 0));
            assertEquals(1L, kisOrderRepository.findAllById(List.of(firstKey, secondKey)).stream()
                    .filter(intent -> intent.getEnvironment() == KisEnvironment.REAL
                            && intent.getMarket() == Market.KR
                            && List.of(KisOrderStatus.SUBMITTING, KisOrderStatus.ACCEPTED,
                                    KisOrderStatus.PARTIALLY_FILLED, KisOrderStatus.CANCEL_PENDING,
                                    KisOrderStatus.UNKNOWN).contains(intent.getStatus()))
                    .count());
        } finally {
            kisOrderRepository.deleteAllById(List.of(firstKey, secondKey));
        }
    }

    private boolean reserveRealOrder(UUID key, String requestHash, OrderRequest request,
                                     CountDownLatch ready, CountDownLatch start) throws Exception {
        ready.countDown();
        if (!start.await(5, TimeUnit.SECONDS)) {
            throw new AssertionError("Concurrent REAL reservation start timed out.");
        }
        try {
            return store.prepare(key, requestHash, KisEnvironment.REAL, request, null).createdNow();
        } catch (DataIntegrityViolationException expectedReservationConflict) {
            return false;
        }
    }

    @Test
    @Transactional
    void realIndeterminateOutcomePersistsUnknownAcrossReloadAndRejectsDuplicateIntent() {
        UUID key = UUID.randomUUID();
        OrderRequest order = request();
        String hash = "a".repeat(64);

        try {
            var prepared = store.prepare(key, hash, KisEnvironment.REAL, order, null);
            var unresolved = store.markUnknown(key);
            entityManager.flush();
            entityManager.clear();

            var persisted = kisOrderRepository.findById(key).orElseThrow();
            var duplicate = store.prepare(key, hash, KisEnvironment.REAL, order, null);

            assertTrue(prepared.createdNow());
            assertEquals(KisOrderStatus.UNKNOWN, unresolved.getStatus());
            assertEquals(KisOrderStatus.UNKNOWN, persisted.getStatus());
            assertEquals(KisOrderStatus.UNKNOWN, duplicate.intent().getStatus());
            assertEquals(false, duplicate.createdNow());
            assertEquals(true, store.hasUnresolvedRealOrder(order.market(), UUID.randomUUID()));
            assertEquals(false, store.hasUnresolvedRealOrder(order.market(), key));
        } finally {
            kisOrderRepository.deleteById(key);
        }
    }

    @Test
    @Transactional
    void mockBrokerCancelIsConfirmedByReconciliationAndPersistsCancelledState() {
        UUID key = UUID.randomUUID();
        OrderRequest order = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 3,
                new BigDecimal("70000"), Currency.KRW);
        String brokerNumber = "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        var openSnapshot = new KisBrokerOrderSnapshot(1, 2, new BigDecimal("70000"), false, false);
        var cancelledSnapshot = new KisBrokerOrderSnapshot(1, 0, new BigDecimal("70000"), true, false);
        when(kisAccountClient.getBalance(KisEnvironment.PAPER, Market.KR, order.symbol(), order.limitPrice(), ""))
                .thenReturn(new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW",
                        new BigDecimal("500000"), new BigDecimal("500000"), List.of(), Instant.now(), "test"));
        when(kisOrderClient.placePaperLimitOrder(Market.KR, order.symbol(), OrderSide.BUY, 3,
                order.limitPrice(), Currency.KRW, null))
                .thenReturn(receipt("paper", brokerNumber));
        when(kisOrderClient.reconcilePaperOrder(any(KisOrderIntent.class)))
                .thenReturn(Optional.of(openSnapshot), Optional.of(cancelledSnapshot));

        try {
            assertEquals(KisOrderStatus.ACCEPTED, kisOrders.submitPaperOrder(key, order, null).status());
            assertEquals(KisOrderStatus.CANCEL_PENDING, kisOrders.cancelPaperOrder(key, null).status());
            entityManager.flush();
            entityManager.clear();

            var cancelled = kisOrders.reconcilePaperOrder(key);
            entityManager.flush();
            entityManager.clear();
            var persisted = kisOrderRepository.findById(key).orElseThrow();

            assertEquals(KisOrderStatus.CANCELLED, cancelled.status());
            assertEquals(KisOrderStatus.CANCELLED, persisted.getStatus());
            assertEquals(1, persisted.getFilledQuantity());
            assertEquals(0, persisted.getRemainingQuantity());
            assertEquals(0, persisted.getAverageFillPrice().compareTo(new BigDecimal("70000")));
            verify(kisOrderClient, times(1)).cancelPaperOrder(Market.KR, order.symbol(), brokerNumber,
                    "ITORG01", 2, "");
            verify(kisOrderClient, times(2)).reconcilePaperOrder(any(KisOrderIntent.class));
        } finally {
            kisOrderRepository.deleteById(key);
        }
    }

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
    void paperReconciliationQueryPagesDomesticOrdersInStableUpdatedOrder() {
        UUID earlierKey = UUID.randomUUID();
        UUID laterKey = UUID.randomUUID();
        UUID usPaperKey = UUID.randomUUID();
        UUID unknownWithoutBrokerKey = UUID.randomUUID();
        UUID realKey = UUID.randomUUID();
        try {
            store.prepare(earlierKey, hash("paper-page-earlier"), KisEnvironment.PAPER, request(), null);
            store.accept(earlierKey, receipt("paper", "PG" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));
            store.prepare(laterKey, hash("paper-page-later"), KisEnvironment.PAPER, request(), null);
            store.accept(laterKey, receipt("paper", "PG" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));

            OrderRequest usRequest = new OrderRequest(Market.US, "AAPL", OrderSide.BUY, 1,
                    new BigDecimal("100"), Currency.USD);
            store.prepare(usPaperKey, hash("paper-page-us"), KisEnvironment.PAPER, usRequest, "NASD");
            store.accept(usPaperKey, new KisOrderReceipt("paper", "US", "AAPL", "BUY",
                    "PG" + UUID.randomUUID().toString().replace("-", "").substring(0, 16),
                    "ITORG01", "0", Instant.now()));

            KisOrderIntent unknownWithoutBroker = new KisOrderIntent(unknownWithoutBrokerKey,
                    hash("paper-page-unknown"), KisEnvironment.PAPER, request(), null);
            unknownWithoutBroker.markUnknown();
            repository.saveAndFlush(unknownWithoutBroker);

            store.prepare(realKey, hash("real-page-domestic"), KisEnvironment.REAL, request(), null);
            store.accept(realKey, receipt("real", "RG" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));

            jdbcTemplate.update("UPDATE kis_order_intent SET updated_at = ? WHERE idempotency_key = ?",
                    Timestamp.from(Instant.parse("2026-11-13T01:00:00Z")), earlierKey);
            jdbcTemplate.update("UPDATE kis_order_intent SET updated_at = ? WHERE idempotency_key = ?",
                    Timestamp.from(Instant.parse("2026-11-13T02:00:00Z")), laterKey);
            entityManager.clear();

            var firstPage = repository.findReconciliationCandidates(
                    KisEnvironment.PAPER, List.of(Market.KR),
                    List.of(KisOrderStatus.ACCEPTED, KisOrderStatus.UNKNOWN), PageRequest.of(0, 1));
            var secondPage = repository.findReconciliationCandidates(
                    KisEnvironment.PAPER, List.of(Market.KR),
                    List.of(KisOrderStatus.ACCEPTED, KisOrderStatus.UNKNOWN), PageRequest.of(1, 1));

            assertEquals(2, firstPage.getTotalElements());
            assertEquals(2, firstPage.getTotalPages());
            assertEquals(earlierKey, firstPage.getContent().getFirst().getIdempotencyKey());
            assertEquals(laterKey, secondPage.getContent().getFirst().getIdempotencyKey());
        } finally {
            repository.deleteAllById(List.of(earlierKey, laterKey, usPaperKey, unknownWithoutBrokerKey, realKey));
        }
    }

    @Test
    @Transactional
    void paperReconciliationJobPollsKisDailyFillIntoPostgres() {
        UUID key = UUID.randomUUID();
        String brokerNumber = "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        RestClient.Builder http = RestClient.builder();
        MockRestServiceServer server = MockRestServiceServer.bindTo(http).build();
        server.expect(requestTo("https://openapivts.koreainvestment.com:29443/oauth2/tokenP"))
                .andRespond(withSuccess(testToken(), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(header("tr_id", "VTTC0081R"))
                .andExpect(queryParam("ODNO", brokerNumber))
                .andRespond(withSuccess(dailyFillResponse(brokerNumber, 1, 1), org.springframework.http.MediaType.APPLICATION_JSON));
        server.expect(requestTo(org.hamcrest.Matchers.containsString("/uapi/domestic-stock/v1/trading/inquire-daily-ccld?")))
                .andExpect(header("tr_id", "VTTC0081R"))
                .andExpect(queryParam("ODNO", brokerNumber))
                .andRespond(withSuccess(dailyFillResponse(brokerNumber, 2, 0), org.springframework.http.MediaType.APPLICATION_JSON));

        var order = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW);
        try {
            store.prepare(key, hash("paper-daily-fill-e2e"), KisEnvironment.PAPER, order, null);
            store.accept(key, receipt("paper", brokerNumber));
            KisProperties.Account paper = new KisProperties.Account("paper-key", "paper-secret", "1234567801");
            KisProperties kisProperties = new KisProperties(true, true, false, false, paper,
                    new KisProperties.Account("", "", ""), Duration.ofSeconds(3), Duration.ofSeconds(8), Duration.ZERO);
            KisOrderProperties orderProperties = new KisOrderProperties(false, true);
            KisOrderClient mockedHttpBroker = new KisOrderClient(kisProperties, orderProperties,
                    tradingSafetyProperties, http);
            KisOrderOrchestrator orchestrator = new KisOrderOrchestrator(mockedHttpBroker, kisAccountClient,
                    orderProperties, tradingSafetyProperties, store);
            KisPaperOrderReconciliationJob reconciliationJob = new KisPaperOrderReconciliationJob(
                    orderProperties, kisProperties, tradingSafetyProperties, MarketOperationalScope.domesticOnly(),
                    repository, orchestrator);

            reconciliationJob.reconcileKnownBrokerOrders();
            entityManager.flush();
            entityManager.clear();
            KisOrderIntent afterPartial = repository.findById(key).orElseThrow();
            assertEquals(KisOrderStatus.PARTIALLY_FILLED, afterPartial.getStatus());
            assertEquals(1, afterPartial.getFilledQuantity());
            assertEquals(1, afterPartial.getRemainingQuantity());
            assertEquals(0, afterPartial.getAverageFillPrice().compareTo(new BigDecimal("70000")));

            reconciliationJob.reconcileKnownBrokerOrders();
            entityManager.flush();
            entityManager.clear();
            KisOrderIntent afterComplete = repository.findById(key).orElseThrow();
            assertEquals(KisOrderStatus.FILLED, afterComplete.getStatus());
            assertEquals(2, afterComplete.getFilledQuantity());
            assertEquals(0, afterComplete.getRemainingQuantity());
            server.verify();
        } finally {
            repository.deleteById(key);
        }
    }

    @Test
    void realExecutionTimestampIsMonotonicAndCannotBeWrittenToPaperIntent() {
        UUID realKey = UUID.randomUUID();
        UUID paperKey = UUID.randomUUID();
        String brokerNumber = "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        Instant earlier = Instant.parse("2026-11-06T14:00:01Z");
        Instant later = Instant.parse("2026-11-06T14:00:02Z");
        try {
            store.prepare(realKey, hash("real-fill-time"), KisEnvironment.REAL, request(), null);
            store.accept(realKey, receipt("real", brokerNumber));
            store.prepare(paperKey, hash("paper-fill-time"), KisEnvironment.PAPER, request(), null);
            store.accept(paperKey, receipt("paper", brokerNumber));

            assertEquals(1, repository.recordRealFillTime(realKey, later));
            assertEquals(1, repository.recordRealFillTime(realKey, earlier));
            assertEquals(0, repository.recordRealFillTime(paperKey, later));
            entityManager.clear();

            assertEquals(later, repository.findById(realKey).orElseThrow().getLastFillAt());
            assertNull(repository.findById(paperKey).orElseThrow().getLastFillAt());
        } finally {
            repository.deleteAllById(List.of(realKey, paperKey));
        }
    }

    @Test
    @Transactional
    void ambiguousCancelPersistsPendingAndRequiresReadOnlyReconciliationBeforeAnyRetry() {
        UUID key = UUID.randomUUID();
        OrderRequest order = new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
        String brokerNumber = "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        var openSnapshot = new KisBrokerOrderSnapshot(0, 1, null, false, false);
        var cancelledSnapshot = new KisBrokerOrderSnapshot(0, 0, null, true, false);
        when(kisAccountClient.getBalance(KisEnvironment.PAPER, Market.KR, order.symbol(), order.limitPrice(), ""))
                .thenReturn(new KisAccountSnapshot(KisEnvironment.PAPER, Market.KR, "KRW",
                        new BigDecimal("500000"), new BigDecimal("500000"), List.of(), Instant.now(), "test"));
        when(kisOrderClient.placePaperLimitOrder(Market.KR, order.symbol(), OrderSide.BUY, 1,
                order.limitPrice(), Currency.KRW, null)).thenReturn(receipt("paper", brokerNumber));
        when(kisOrderClient.reconcilePaperOrder(any(KisOrderIntent.class)))
                .thenReturn(Optional.of(openSnapshot), Optional.of(cancelledSnapshot));
        doThrow(new KisOrderOutcomeUnknownException("simulated cancel timeout", new RuntimeException("mock")))
                .when(kisOrderClient).cancelPaperOrder(Market.KR, order.symbol(), brokerNumber,
                        "ITORG01", 1, "");

        try {
            assertEquals(KisOrderStatus.ACCEPTED, kisOrders.submitPaperOrder(key, order, null).status());
            assertEquals(KisOrderStatus.CANCEL_PENDING, kisOrders.cancelPaperOrder(key, null).status());
            entityManager.flush();
            entityManager.clear();

            KisOrderIntent persistedPending = kisOrderRepository.findById(key).orElseThrow();
            assertEquals(KisOrderStatus.CANCEL_PENDING, persistedPending.getStatus());
            assertEquals(KisOrderStatus.CANCEL_PENDING, kisOrders.cancelPaperOrder(key, null).status());
            verify(kisOrderClient, times(1)).cancelPaperOrder(Market.KR, order.symbol(), brokerNumber,
                    "ITORG01", 1, "");

            assertEquals(KisOrderStatus.CANCELLED, kisOrders.reconcilePaperOrder(key).status());
            entityManager.flush();
            entityManager.clear();
            assertEquals(KisOrderStatus.CANCELLED, kisOrderRepository.findById(key).orElseThrow().getStatus());
            verify(kisOrderClient, times(2)).reconcilePaperOrder(any(KisOrderIntent.class));
        } finally {
            kisOrderRepository.deleteById(key);
        }
    }

    @Test
    void onlyOneConcurrentCallerCanClaimCancellationForAnOrder() throws Exception {
        UUID key = UUID.randomUUID();
        try {
            store.prepare(key, hash("concurrent-cancel"), KisEnvironment.PAPER, request(), null);
            store.accept(key, receipt("paper", "IT" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));

            var ready = new CountDownLatch(2);
            var start = new CountDownLatch(1);
            try (var pool = Executors.newFixedThreadPool(2)) {
                var first = pool.submit(() -> claimAfterBarrier(key, ready, start, KisEnvironment.PAPER));
                var second = pool.submit(() -> claimAfterBarrier(key, ready, start, KisEnvironment.PAPER));
                if (!ready.await(5, TimeUnit.SECONDS)) throw new AssertionError("Concurrent cancellation workers did not become ready.");
                start.countDown();

                int winners = (first.get(10, TimeUnit.SECONDS) ? 1 : 0) + (second.get(10, TimeUnit.SECONDS) ? 1 : 0);
                assertEquals(1, winners);
            }

            entityManager.clear();
            assertEquals(KisOrderStatus.CANCEL_PENDING, repository.findById(key).orElseThrow().getStatus());
        } finally {
            repository.deleteById(key);
        }
    }

    @Test
    void onlyOneConcurrentRealCallerCanClaimCancellationForAnOrder() throws Exception {
        UUID key = UUID.randomUUID();
        try {
            store.prepare(key, hash("concurrent-real-cancel"), KisEnvironment.REAL, request(), null);
            store.accept(key, receipt("real", "IR" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));

            var ready = new CountDownLatch(2);
            var start = new CountDownLatch(1);
            try (var pool = Executors.newFixedThreadPool(2)) {
                var first = pool.submit(() -> claimAfterBarrier(key, ready, start, KisEnvironment.REAL));
                var second = pool.submit(() -> claimAfterBarrier(key, ready, start, KisEnvironment.REAL));
                if (!ready.await(5, TimeUnit.SECONDS)) throw new AssertionError("Concurrent REAL cancellation workers did not become ready.");
                start.countDown();

                int winners = (first.get(10, TimeUnit.SECONDS) ? 1 : 0) + (second.get(10, TimeUnit.SECONDS) ? 1 : 0);
                assertEquals(1, winners);
            }

            entityManager.clear();
            KisOrderIntent persisted = repository.findById(key).orElseThrow();
            assertEquals(KisEnvironment.REAL, persisted.getEnvironment());
            assertEquals(KisOrderStatus.CANCEL_PENDING, persisted.getStatus());
        } finally {
            repository.deleteById(key);
        }
    }

    private boolean claimAfterBarrier(UUID key, CountDownLatch ready, CountDownLatch start,
                                      KisEnvironment environment) throws InterruptedException {
        ready.countDown();
        if (!start.await(5, TimeUnit.SECONDS)) throw new AssertionError("Concurrent cancellation start timed out.");
        return store.claimCancelPending(key, environment);
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
    @Transactional
    void persistsSubmissionRejectionCodeWithoutBrokerOrderIdentifiers() {
        UUID key = UUID.randomUUID();
        try {
            store.prepare(key, hash("submit-rejected"), KisEnvironment.PAPER, request(), null);
            store.reject(key, "EGW00201");
            entityManager.flush();
            entityManager.clear();

            KisOrderIntent rejected = repository.findById(key).orElseThrow();
            assertEquals(KisEnvironment.PAPER, rejected.getEnvironment());
            assertEquals(KisOrderStatus.REJECTED, rejected.getStatus());
            assertEquals("EGW00201", rejected.getBrokerResponseCode());
            assertEquals(0, rejected.getFilledQuantity());
            assertEquals(1, rejected.getRemainingQuantity());
            assertNull(rejected.getBrokerOrderNumber());
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
    void paperBuyAndSellPersistCostAdjustedCashPositionAndNetResult() {
        String symbol = String.format(java.util.Locale.ROOT, "%06d",
                java.util.concurrent.ThreadLocalRandom.current().nextInt(100000, 1_000_000));
        BigDecimal initialCash = new BigDecimal("1000000");
        paperCashRepository.save(new PaperCashBalance(Currency.KRW, initialCash));
        var costedOrders = new OrderService(paperOrderRepository, tradingSafetyProperties, paperFillRepository,
                paperPositionRepository, paperCashRepository, paperStrategyRiskRepository,
                new com.aistocktrader.order.PaperTransactionCostCalculator(
                        new com.aistocktrader.order.PaperCostProperties(true, new BigDecimal("10"),
                                new BigDecimal("20"), BigDecimal.ZERO, BigDecimal.ZERO)));
        var buy = costedOrders.submit(new OrderRequest(Market.KR, symbol, OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW), UUID.randomUUID());
        var buyFillKey = UUID.randomUUID();
        costedOrders.recordPaperFill(buy.getId(), new com.aistocktrader.order.PaperFillRequest(
                buyFillKey, 1, new BigDecimal("70000")));
        entityManager.flush();
        entityManager.clear();

        var positionAfterBuy = paperPositionRepository.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0)
                .stream().filter(item -> item.getSymbol().equals(symbol)).findFirst().orElseThrow();
        assertEquals(1, positionAfterBuy.getQuantity());
        assertEquals(0, new BigDecimal("70070.000000").compareTo(positionAfterBuy.getAverageCost()));
        assertEquals(0, new BigDecimal("70").compareTo(paperFillRepository.findByFillKey(buyFillKey)
                .orElseThrow().getTransactionCost()));

        var sell = costedOrders.submit(new OrderRequest(Market.KR, symbol, OrderSide.SELL, 1,
                new BigDecimal("71000"), Currency.KRW), UUID.randomUUID());
        var sellFillKey = UUID.randomUUID();
        costedOrders.recordPaperFill(sell.getId(), new com.aistocktrader.order.PaperFillRequest(
                sellFillKey, 1, new BigDecimal("71000")));
        entityManager.flush();
        entityManager.clear();

        var sellFill = paperFillRepository.findByFillKey(sellFillKey).orElseThrow();
        var settledCash = paperCashRepository.findById(Currency.KRW).orElseThrow().getBalance();
        var closedPosition = paperPositionRepository.findAll().stream()
                .filter(item -> item.getMarket() == Market.KR && item.getCurrency() == Currency.KRW
                        && item.getSymbol().equals(symbol)).findFirst().orElseThrow();
        BigDecimal netResult = new BigDecimal("71000").subtract(new BigDecimal("70000"))
                .subtract(new BigDecimal("70")).subtract(sellFill.getTransactionCost());

        assertEquals(0, new BigDecimal("213").compareTo(sellFill.getTransactionCost()));
        assertEquals(0, new BigDecimal("1000717").compareTo(settledCash));
        assertEquals(0, new BigDecimal("717").compareTo(netResult));
        assertEquals(0, closedPosition.getQuantity());
        assertEquals(0, BigDecimal.ZERO.compareTo(closedPosition.getAverageCost()));
        assertEquals(1L, paperFillRepository.findByFillKey(buyFillKey).stream().count());
        assertEquals(1L, paperFillRepository.findByFillKey(sellFillKey).stream().count());
    }

    @Test
    void concurrentDuplicatePaperFillSettlesOnceAndBothCallsConverge() throws Exception {
        UUID orderKey = UUID.randomUUID();
        UUID fillKey = UUID.randomUUID();
        String symbol = String.format(java.util.Locale.ROOT, "%06d",
                java.util.concurrent.ThreadLocalRandom.current().nextInt(100000, 1_000_000));
        BigDecimal initialBalance = new BigDecimal("1000000");
        var priorCash = paperCashRepository.findById(Currency.KRW);
        paperCashRepository.save(new PaperCashBalance(Currency.KRW, initialBalance));
        var order = paperOrders.submit(new OrderRequest(Market.KR, symbol, OrderSide.BUY, 2,
                new BigDecimal("70000"), Currency.KRW), orderKey);
        var request = new PaperFillRequest(fillKey, 1, new BigDecimal("70000"));
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);

        try {
            var first = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.recordPaperFill(order.getId(), request);
            });
            var second = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.recordPaperFill(order.getId(), request);
            });

            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            var firstResult = first.get(10, TimeUnit.SECONDS);
            var secondResult = second.get(10, TimeUnit.SECONDS);
            assertEquals(com.aistocktrader.order.OrderStatus.PARTIALLY_FILLED, firstResult.getStatus());
            assertEquals(com.aistocktrader.order.OrderStatus.PARTIALLY_FILLED, secondResult.getStatus());
            assertEquals(1, firstResult.getFilledQuantity());
            assertEquals(1, secondResult.getFilledQuantity());

            var fill = paperFillRepository.findByFillKey(fillKey).orElseThrow();
            var persistedOrder = paperOrderRepository.findById(order.getId()).orElseThrow();
            var position = paperPositionRepository.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0).stream()
                    .filter(item -> item.getSymbol().equals(symbol)).findFirst().orElseThrow();
            BigDecimal remainingCash = paperCashRepository.findById(Currency.KRW).orElseThrow().getBalance();
            assertEquals(order.getId(), fill.getOrderId());
            assertEquals(1, persistedOrder.getFilledQuantity());
            assertEquals(1, position.getQuantity());
            assertEquals(0, initialBalance.subtract(new BigDecimal("70000")).subtract(fill.getTransactionCost())
                    .compareTo(remainingCash));
        } finally {
            start.countDown();
            pool.shutdownNow();
            pool.awaitTermination(10, TimeUnit.SECONDS);
            paperFillRepository.findByFillKey(fillKey).ifPresent(paperFillRepository::delete);
            paperPositionRepository.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0).stream()
                    .filter(item -> item.getSymbol().equals(symbol)).forEach(paperPositionRepository::delete);
            paperOrderRepository.findByIdempotencyKey(orderKey)
                    .ifPresent(item -> paperOrderRepository.deleteById(item.getId()));
            if (priorCash.isPresent()) {
                PaperCashBalance original = priorCash.orElseThrow();
                jdbcTemplate.update("UPDATE paper_cash_balance SET balance = ?, updated_at = ? WHERE currency = ?",
                        original.getBalance(), Timestamp.from(original.getUpdatedAt()), Currency.KRW.name());
            } else {
                paperCashRepository.deleteById(Currency.KRW);
            }
        }
    }

    @Test
    void fillKeyReusedConcurrentlyAcrossDifferentOrdersIsRejectedWithoutSecondSettlement() throws Exception {
        UUID firstOrderKey = UUID.randomUUID();
        UUID secondOrderKey = UUID.randomUUID();
        UUID fillKey = UUID.randomUUID();
        String firstSymbol = String.format(java.util.Locale.ROOT, "%06d",
                java.util.concurrent.ThreadLocalRandom.current().nextInt(100000, 1_000_000));
        String secondSymbolCandidate;
        do {
            secondSymbolCandidate = String.format(java.util.Locale.ROOT, "%06d",
                    java.util.concurrent.ThreadLocalRandom.current().nextInt(100000, 1_000_000));
        } while (secondSymbolCandidate.equals(firstSymbol));
        final String secondSymbol = secondSymbolCandidate;
        BigDecimal initialBalance = new BigDecimal("1000000");
        var priorCash = paperCashRepository.findById(Currency.KRW);
        paperCashRepository.save(new PaperCashBalance(Currency.KRW, initialBalance));
        var firstOrder = paperOrders.submit(new OrderRequest(Market.KR, firstSymbol, OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW), firstOrderKey);
        var secondOrder = paperOrders.submit(new OrderRequest(Market.KR, secondSymbol, OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW), secondOrderKey);
        var request = new PaperFillRequest(fillKey, 1, new BigDecimal("70000"));
        var ready = new CountDownLatch(2);
        var start = new CountDownLatch(1);
        var pool = Executors.newFixedThreadPool(2);

        try {
            var first = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.recordPaperFill(firstOrder.getId(), request);
            });
            var second = pool.submit(() -> {
                ready.countDown();
                if (!start.await(5, TimeUnit.SECONDS)) throw new IllegalStateException("Start gate timed out.");
                return paperOrders.recordPaperFill(secondOrder.getId(), request);
            });

            assertTrue(ready.await(5, TimeUnit.SECONDS));
            start.countDown();
            var outcomes = List.of(first, second).stream().map(future -> {
                try {
                    return future.get(10, TimeUnit.SECONDS);
                } catch (Exception exception) {
                    return exception;
                }
            }).toList();

            assertEquals(1, outcomes.stream().filter(OrderRecord.class::isInstance).count());
            assertEquals(1, outcomes.stream().filter(java.util.concurrent.ExecutionException.class::isInstance).count());
            assertInstanceOf(com.aistocktrader.order.IdempotencyConflictException.class,
                    ((java.util.concurrent.ExecutionException) outcomes.stream()
                            .filter(java.util.concurrent.ExecutionException.class::isInstance).findFirst().orElseThrow())
                            .getCause());

            var fill = paperFillRepository.findByFillKey(fillKey).orElseThrow();
            assertEquals(1, paperOrderRepository.findById(firstOrder.getId()).orElseThrow().getFilledQuantity()
                    + paperOrderRepository.findById(secondOrder.getId()).orElseThrow().getFilledQuantity());
            assertTrue(fill.getOrderId().equals(firstOrder.getId()) || fill.getOrderId().equals(secondOrder.getId()));
            assertEquals(1, paperPositionRepository.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0).stream()
                    .filter(item -> item.getSymbol().equals(firstSymbol) || item.getSymbol().equals(secondSymbol))
                    .mapToInt(com.aistocktrader.order.PaperPosition::getQuantity).sum());
            assertEquals(0, initialBalance.subtract(new BigDecimal("70000")).subtract(fill.getTransactionCost())
                    .compareTo(paperCashRepository.findById(Currency.KRW).orElseThrow().getBalance()));
        } finally {
            start.countDown();
            pool.shutdownNow();
            pool.awaitTermination(10, TimeUnit.SECONDS);
            paperFillRepository.findByFillKey(fillKey).ifPresent(paperFillRepository::delete);
            paperPositionRepository.findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(0).stream()
                    .filter(item -> item.getSymbol().equals(firstSymbol) || item.getSymbol().equals(secondSymbol))
                    .forEach(paperPositionRepository::delete);
            paperOrderRepository.findByIdempotencyKey(firstOrderKey)
                    .ifPresent(item -> paperOrderRepository.deleteById(item.getId()));
            paperOrderRepository.findByIdempotencyKey(secondOrderKey)
                    .ifPresent(item -> paperOrderRepository.deleteById(item.getId()));
            if (priorCash.isPresent()) {
                PaperCashBalance original = priorCash.orElseThrow();
                jdbcTemplate.update("UPDATE paper_cash_balance SET balance = ?, updated_at = ? WHERE currency = ?",
                        original.getBalance(), Timestamp.from(original.getUpdatedAt()), Currency.KRW.name());
            } else {
                paperCashRepository.deleteById(Currency.KRW);
            }
        }
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

    @Test
    void acceptedKisPaperIntentSurvivesSpringApplicationContextRestartWithoutResubmission() {
        UUID key = UUID.randomUUID();
        String requestHash = hash("restart-paper-intent");
        String brokerNumber = "P" + UUID.randomUUID().toString().replace("-", "").substring(0, 15);
        try {
            store.prepare(key, requestHash, KisEnvironment.PAPER, request(), null);
            store.accept(key, receipt("paper", brokerNumber));

            try (ConfigurableApplicationContext restarted = new SpringApplicationBuilder(TradingApiApplication.class)
                    .web(WebApplicationType.NONE)
                    .properties("spring.main.banner-mode=off", "spring.task.scheduling.enabled=false",
                            "trading.mode=paper", "trading.live-trading-enabled=false",
                            "kis.orders.paper-enabled=false", "kis.orders.paper-reconciliation-enabled=false",
                            "kis.account-read-enabled=false", "kis.paper-account-read-enabled=false",
                            "kis.real-account-read-enabled=false", "kis.real-reconciliation-enabled=false",
                            "kis.paper.app-key=", "kis.paper.app-secret=", "kis.paper.account-number=",
                            "kis.real.app-key=", "kis.real.app-secret=", "kis.real.account-number=")
                    .run()) {
                KisOrderIntentRepository restartedRepository = restarted.getBean(KisOrderIntentRepository.class);
                KisOrderIntent recovered = restartedRepository.findById(key).orElseThrow();
                var duplicate = restarted.getBean(KisOrderIntentStore.class)
                        .prepare(key, requestHash, KisEnvironment.PAPER, request(), null);

                assertEquals(key, recovered.getIdempotencyKey());
                assertEquals(KisEnvironment.PAPER, recovered.getEnvironment());
                assertEquals(KisOrderStatus.ACCEPTED, recovered.getStatus());
                assertEquals(brokerNumber, recovered.getBrokerOrderNumber());
                assertEquals("ITORG01", recovered.getBrokerOrganizationNumber());
                assertEquals(false, duplicate.createdNow());
                assertEquals(key, duplicate.intent().getIdempotencyKey());
                assertEquals(brokerNumber, duplicate.intent().getBrokerOrderNumber());
            }
        } finally {
            kisOrderRepository.deleteById(key);
        }
    }

    @Test
    @Transactional
    void contradictoryBrokerSnapshotPersistsUnknownLockWithoutCreatingFillHistory() {
        UUID key = UUID.randomUUID();
        store.prepare(key, hash("bad-snapshot"), KisEnvironment.REAL, request(), null);
        store.accept(key, receipt("real", "IR" + UUID.randomUUID().toString().replace("-", "").substring(0, 16)));
        KisBrokerOrderSnapshot contradictory = new KisBrokerOrderSnapshot(1, 1,
                new BigDecimal("70000"), false, false);

        KisOrderIntent reconciled = store.reconcile(key, contradictory);
        entityManager.flush();
        entityManager.clear();

        KisOrderIntent persisted = repository.findById(key).orElseThrow();
        assertEquals(KisOrderStatus.UNKNOWN, reconciled.getStatus());
        assertEquals(KisOrderStatus.UNKNOWN, persisted.getStatus());
        assertEquals(0, persisted.getFilledQuantity());
        assertEquals(1, persisted.getRemainingQuantity());
        assertNull(persisted.getAverageFillPrice());
        assertTrue(jdbcTemplate.queryForList(
                "SELECT idempotency_key FROM kis_real_fill_snapshot WHERE idempotency_key = ?", UUID.class, key).isEmpty());
    }

    private static OrderRequest request() {
        return new OrderRequest(Market.KR, "005930", OrderSide.BUY, 1,
                new BigDecimal("70000"), Currency.KRW);
    }

    private static KisOrderReceipt receipt(String environment, String brokerNumber) {
        return new KisOrderReceipt(environment, "KR", "005930", "BUY", brokerNumber,
                "ITORG01", "0", Instant.now());
    }

    private static String testToken() {
        String expiry = LocalDateTime.now().plusHours(12).format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        return "{\"access_token\":\"test-token\",\"access_token_token_expired\":\"" + expiry + "\"}";
    }

    private static String dailyFillResponse(String brokerNumber, int filled, int remaining) {
        return """
                {"rt_cd":"0","ctx_area_fk100":"","ctx_area_nk100":"","output1":[
                  {"odno":"%s","ord_gno_brno":"ITORG01","pdno":"005930","sll_buy_dvsn_cd":"02",
                   "tot_ccld_qty":"%d","rmn_qty":"%d","avg_prvs":"70000","cncl_yn":"N","rjct_qty":"0"}
                ]}
                """.formatted(brokerNumber, filled, remaining);
    }

    private static String hash(String label) {
        return label + "-" + UUID.randomUUID();
    }
}
