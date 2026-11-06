package com.aistocktrader.kis;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.UUID;
import java.util.List;
import java.util.Collection;

public interface KisOrderIntentRepository extends JpaRepository<KisOrderIntent, UUID> {
    List<KisOrderIntent> findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment environment);
    List<KisOrderIntent> findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(
            KisEnvironment environment, Collection<KisOrderStatus> statuses);
    java.util.Optional<KisOrderIntent> findByEnvironmentAndMarketAndBrokerOrderNumber(
            KisEnvironment environment, com.aistocktrader.order.Market market, String brokerOrderNumber);
    boolean existsByEnvironmentAndMarketAndStatusInAndIdempotencyKeyNot(
            KisEnvironment environment, com.aistocktrader.order.Market market,
            Collection<KisOrderStatus> statuses, UUID idempotencyKey);

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
}
