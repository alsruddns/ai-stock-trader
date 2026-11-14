package com.aistocktrader.kis;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;
import java.util.List;
import java.util.Collection;
import java.time.Instant;
import jakarta.persistence.LockModeType;

public interface KisOrderIntentRepository extends JpaRepository<KisOrderIntent, UUID> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select intent from KisOrderIntent intent where intent.idempotencyKey = :key")
    java.util.Optional<KisOrderIntent> findByIdForUpdate(@Param("key") UUID key);
    List<KisOrderIntent> findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment environment);
    List<KisOrderIntent> findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(
            KisEnvironment environment, Collection<KisOrderStatus> statuses);
    @Query("""
            select intent from KisOrderIntent intent
             where intent.environment = :environment
               and intent.market in :markets
               and intent.status in :statuses
               and intent.brokerOrderNumber is not null
               and trim(intent.brokerOrderNumber) <> ''
             order by intent.updatedAt asc, intent.idempotencyKey asc
            """)
    Page<KisOrderIntent> findReconciliationCandidates(@Param("environment") KisEnvironment environment,
                                                       @Param("markets") Collection<com.aistocktrader.order.Market> markets,
                                                       @Param("statuses") Collection<KisOrderStatus> statuses,
                                                       Pageable pageable);
    List<KisOrderIntent> findTop50ByEnvironmentAndRealExitStatusInOrderByUpdatedAtAsc(
            KisEnvironment environment, Collection<KisRealExitStatus> statuses);
    @Query("""
            select intent from KisOrderIntent intent
             where intent.environment = :environment
               and intent.market in :markets
               and intent.realExitStatus in :statuses
             order by intent.updatedAt asc, intent.idempotencyKey asc
            """)
    Page<KisOrderIntent> findRealExitCandidates(@Param("environment") KisEnvironment environment,
                                                @Param("markets") Collection<com.aistocktrader.order.Market> markets,
                                                @Param("statuses") Collection<KisRealExitStatus> statuses,
                                                Pageable pageable);
    List<KisOrderIntent> findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
            KisEnvironment environment, com.aistocktrader.order.Market market, Instant createdAt);
    List<KisOrderIntent> findByEnvironmentAndMarketAndLastFillAtGreaterThanEqualAndLastFillAtLessThanOrderByLastFillAtAsc(
            KisEnvironment environment, com.aistocktrader.order.Market market, Instant fromInclusive, Instant toExclusive);
    java.util.Optional<KisOrderIntent> findByEnvironmentAndMarketAndBrokerOrderNumber(
            KisEnvironment environment, com.aistocktrader.order.Market market, String brokerOrderNumber);
    boolean existsByEnvironmentAndMarketAndStatusInAndIdempotencyKeyNot(
            KisEnvironment environment, com.aistocktrader.order.Market market,
            Collection<KisOrderStatus> statuses, UUID idempotencyKey);
    boolean existsByEnvironmentAndMarketAndStatusIn(
            KisEnvironment environment, com.aistocktrader.order.Market market,
            Collection<KisOrderStatus> statuses);

    /** Restart recovery lookup for REAL BUY plans whose broker confirmed the entry is no longer open. */
    @Query("""
            select intent from KisOrderIntent intent
             where intent.environment = :environment
               and intent.market = :market
               and upper(intent.symbol) = upper(:symbol)
               and intent.side = com.aistocktrader.order.OrderSide.BUY
               and intent.exitStopDistancePerShare is not null
               and intent.exitRewardRiskMultiple is not null
               and intent.filledQuantity > 0
               and intent.realExitStatus in (com.aistocktrader.kis.KisRealExitStatus.ARMED,
                                               com.aistocktrader.kis.KisRealExitStatus.EXIT_PENDING)
             order by intent.updatedAt asc
            """)
    List<KisOrderIntent> findExitManagedRealBuyCandidates(@Param("environment") KisEnvironment environment,
                                                           @Param("market") com.aistocktrader.order.Market market,
                                                           @Param("symbol") String symbol);

    /** Claims exactly one protective sell attempt before any external order request. */
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Transactional
    @Query(value = """
            UPDATE kis_order_intent
               SET real_exit_status = 'EXIT_PENDING',
                   real_exit_reason = :reason,
                   real_exit_order_key = :exitOrderKey,
                   real_exit_quantity = :quantity,
                   real_exit_limit_price = :limitPrice,
                   updated_at = CURRENT_TIMESTAMP
             WHERE idempotency_key = :entryOrderKey
               AND environment = 'REAL' AND side = 'BUY'
               AND real_exit_status = 'ARMED'
               AND filled_quantity > 0
               AND :quantity > 0 AND :quantity <= filled_quantity
               AND :limitPrice > 0
               AND :reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'SESSION_END')
            """, nativeQuery = true)
    int claimRealExit(@Param("entryOrderKey") UUID entryOrderKey,
                      @Param("exitOrderKey") UUID exitOrderKey,
                      @Param("reason") String reason,
                      @Param("quantity") int quantity,
                      @Param("limitPrice") java.math.BigDecimal limitPrice);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Transactional
    @Query(value = """
            UPDATE kis_order_intent
               SET real_exit_status = 'MANUAL_REVIEW', updated_at = CURRENT_TIMESTAMP
             WHERE idempotency_key = :entryOrderKey
               AND environment = 'REAL' AND side = 'BUY'
               AND real_exit_status IN ('ARMED', 'EXIT_PENDING')
            """, nativeQuery = true)
    int markRealExitManualReview(@Param("entryOrderKey") UUID entryOrderKey);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Transactional
    @Query(value = """
            UPDATE kis_order_intent
               SET real_exit_status = 'CLOSED', updated_at = CURRENT_TIMESTAMP
             WHERE idempotency_key = :entryOrderKey
               AND environment = 'REAL' AND side = 'BUY'
               AND real_exit_status IN ('ARMED', 'EXIT_PENDING')
            """, nativeQuery = true)
    int closeRealExitPlan(@Param("entryOrderKey") UUID entryOrderKey);

    @Modifying
    @Transactional
    @Query(value = """
            UPDATE kis_order_intent
               SET last_fill_at = CASE WHEN last_fill_at IS NULL OR last_fill_at < :fillAt THEN :fillAt ELSE last_fill_at END
             WHERE idempotency_key = :key AND environment = 'REAL' AND broker_order_number IS NOT NULL
            """, nativeQuery = true)
    int recordRealFillTime(@Param("key") UUID key, @Param("fillAt") Instant fillAt);

    @Query("select intent.status, count(intent) from KisOrderIntent intent where intent.environment = :environment group by intent.status")
    List<Object[]> countStatusesByEnvironment(@Param("environment") KisEnvironment environment);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query(value = """
            UPDATE kis_order_intent
               SET status = 'CANCEL_PENDING', broker_response_code = '0', updated_at = CURRENT_TIMESTAMP
             WHERE idempotency_key = :key
               AND environment = :environment
               AND status IN ('ACCEPTED', 'PARTIALLY_FILLED')
               AND broker_order_number IS NOT NULL
               AND remaining_quantity > 0
            """, nativeQuery = true)
    int claimCancelPending(@Param("key") UUID key, @Param("environment") String environment);

    @Transactional
    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Query(value = """
            UPDATE kis_order_intent
               SET status = 'UNKNOWN', updated_at = CURRENT_TIMESTAMP
             WHERE status = 'SUBMITTING'
               AND updated_at < :cutoff
            """, nativeQuery = true)
    int recoverStaleSubmittingIntents(@Param("cutoff") Instant cutoff);
}
