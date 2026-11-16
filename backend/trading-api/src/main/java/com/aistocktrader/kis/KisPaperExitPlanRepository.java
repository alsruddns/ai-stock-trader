package com.aistocktrader.kis;

import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface KisPaperExitPlanRepository extends JpaRepository<KisPaperExitPlan, UUID> {
    Page<KisPaperExitPlan> findByStatusInOrderByUpdatedAtAsc(List<KisPaperExitStatus> statuses, Pageable pageable);
    Optional<KisPaperExitPlan> findByExitOrderKey(UUID exitOrderKey);

    @Query(value = """
            SELECT COUNT(*)
              FROM kis_paper_exit_plan plan
              JOIN kis_order_intent entry ON entry.idempotency_key = plan.entry_idempotency_key
             WHERE entry.environment = :environment
               AND entry.market = :market
               AND UPPER(entry.symbol) = UPPER(:symbol)
               AND plan.status IN ('ARMED', 'EXIT_PENDING')
            """, nativeQuery = true)
    long countActiveForSymbol(@Param("environment") String environment, @Param("market") String market,
                              @Param("symbol") String symbol);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Transactional
    @Query(value = """
            UPDATE kis_paper_exit_plan
               SET status = 'ARMED', updated_at = CURRENT_TIMESTAMP
             WHERE entry_idempotency_key = :entryKey AND status = 'WAITING_FOR_ENTRY'
            """, nativeQuery = true)
    int arm(@Param("entryKey") UUID entryKey);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Transactional
    @Query(value = """
            UPDATE kis_paper_exit_plan
               SET status = 'EXIT_PENDING', exit_reason = :reason, exit_order_key = :exitOrderKey,
                   exit_quantity = :quantity, exit_limit_price = :limitPrice, updated_at = CURRENT_TIMESTAMP
             WHERE entry_idempotency_key = :entryKey AND status = 'ARMED'
               AND :reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'SESSION_END')
               AND :quantity > 0 AND :limitPrice > 0
            """, nativeQuery = true)
    int claimExit(@Param("entryKey") UUID entryKey, @Param("exitOrderKey") UUID exitOrderKey,
                 @Param("reason") String reason, @Param("quantity") int quantity,
                 @Param("limitPrice") BigDecimal limitPrice);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Transactional
    @Query(value = """
            UPDATE kis_paper_exit_plan
               SET status = 'CLOSED', updated_at = CURRENT_TIMESTAMP
             WHERE entry_idempotency_key = :entryKey AND status IN ('WAITING_FOR_ENTRY', 'ARMED', 'EXIT_PENDING')
            """, nativeQuery = true)
    int closePlan(@Param("entryKey") UUID entryKey);

    @Modifying(flushAutomatically = true, clearAutomatically = true)
    @Transactional
    @Query(value = """
            UPDATE kis_paper_exit_plan
               SET status = 'MANUAL_REVIEW', updated_at = CURRENT_TIMESTAMP
             WHERE entry_idempotency_key = :entryKey AND status IN ('WAITING_FOR_ENTRY', 'ARMED', 'EXIT_PENDING')
            """, nativeQuery = true)
    int requireManualReview(@Param("entryKey") UUID entryKey);
}
