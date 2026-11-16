package com.aistocktrader.kis;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

/** Advances durable PAPER exit plans using only already-reconciled order state. */
@Service
public class KisPaperExitPlanLifecycleService {
    private final KisPaperExitPlanRepository plans;
    private final KisOrderIntentRepository intents;

    public KisPaperExitPlanLifecycleService(KisPaperExitPlanRepository plans, KisOrderIntentRepository intents) {
        this.plans = plans;
        this.intents = intents;
    }

    @Transactional
    public KisPaperExitStatus synchronize(UUID entryKey) {
        KisPaperExitPlan plan = plans.findById(entryKey).orElse(null);
        if (plan == null || isTerminal(plan.getStatus())) return plan == null ? null : plan.getStatus();

        KisOrderIntent entry = intents.findById(entryKey).orElse(null);
        if (entry == null || entry.getEnvironment() != KisEnvironment.PAPER
                || entry.getSide() != com.aistocktrader.order.OrderSide.BUY
                || entry.getExitStopDistancePerShare() == null
                || entry.getExitRewardRiskMultiple() == null) {
            plans.requireManualReview(entryKey);
            return KisPaperExitStatus.MANUAL_REVIEW;
        }

        if (plan.getStatus() == KisPaperExitStatus.WAITING_FOR_ENTRY) {
            return synchronizeEntry(plan, entry);
        }
        if (plan.getStatus() == KisPaperExitStatus.EXIT_PENDING) {
            return synchronizeExit(plan, entry);
        }
        return plan.getStatus();
    }

    private KisPaperExitStatus synchronizeEntry(KisPaperExitPlan plan, KisOrderIntent entry) {
        int filled = entry.getFilledQuantity() == null ? 0 : entry.getFilledQuantity();
        int remaining = entry.getRemainingQuantity() == null ? entry.getQuantity() : entry.getRemainingQuantity();
        if (filled > 0 && remaining == 0
                && (entry.getStatus() == KisOrderStatus.FILLED || entry.getStatus() == KisOrderStatus.CANCELLED)) {
            plans.arm(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.ARMED;
        }
        if (filled == 0 && (entry.getStatus() == KisOrderStatus.REJECTED
                || entry.getStatus() == KisOrderStatus.CANCELLED
                || entry.getStatus() == KisOrderStatus.NO_ORDER_CONFIRMED)) {
            plans.closePlan(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.CLOSED;
        }
        if (filled > 0 && remaining > 0) {
            // A live remainder and an exposed position need coordinated cancel/exit handling.
            // Until that path exists, stop automation and require explicit reconciliation.
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.MANUAL_REVIEW;
        }
        if ((entry.getStatus() == KisOrderStatus.FILLED && filled == 0)
                || (entry.getStatus() == KisOrderStatus.REJECTED && filled > 0)
                || (entry.getStatus() == KisOrderStatus.CANCELLED && filled > 0 && remaining > 0)) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.MANUAL_REVIEW;
        }
        return KisPaperExitStatus.WAITING_FOR_ENTRY;
    }

    private KisPaperExitStatus synchronizeExit(KisPaperExitPlan plan, KisOrderIntent entry) {
        UUID exitKey = plan.getExitOrderKey();
        if (exitKey == null) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.MANUAL_REVIEW;
        }
        KisOrderIntent exit = intents.findById(exitKey).orElse(null);
        if (exit == null) return KisPaperExitStatus.EXIT_PENDING;
        if (exit.getEnvironment() != KisEnvironment.PAPER
                || exit.getSide() != com.aistocktrader.order.OrderSide.SELL
                || exit.getMarket() != entry.getMarket()
                || exit.getSymbol() == null || !exit.getSymbol().equalsIgnoreCase(entry.getSymbol())
                || plan.getExitQuantity() == null || exit.getQuantity() != plan.getExitQuantity()
                || plan.getExitLimitPrice() == null || exit.getLimitPrice() == null
                || exit.getLimitPrice().compareTo(plan.getExitLimitPrice()) != 0) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.MANUAL_REVIEW;
        }
        if (exit.getStatus() == KisOrderStatus.FILLED
                && exit.getFilledQuantity() != null && exit.getFilledQuantity() == exit.getQuantity()
                && exit.getRemainingQuantity() != null && exit.getRemainingQuantity() == 0) {
            plans.closePlan(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.CLOSED;
        }
        if (exit.getStatus() == KisOrderStatus.REJECTED || exit.getStatus() == KisOrderStatus.CANCELLED
                || exit.getStatus() == KisOrderStatus.PARTIALLY_FILLED
                || exit.getStatus() == KisOrderStatus.NO_ORDER_CONFIRMED
                || exit.getStatus() == KisOrderStatus.UNKNOWN || exit.getStatus() == KisOrderStatus.SUBMITTING) {
            plans.requireManualReview(plan.getEntryIdempotencyKey());
            return KisPaperExitStatus.MANUAL_REVIEW;
        }
        return KisPaperExitStatus.EXIT_PENDING;
    }

    private static boolean isTerminal(KisPaperExitStatus status) {
        return status == KisPaperExitStatus.CLOSED || status == KisPaperExitStatus.MANUAL_REVIEW;
    }
}
