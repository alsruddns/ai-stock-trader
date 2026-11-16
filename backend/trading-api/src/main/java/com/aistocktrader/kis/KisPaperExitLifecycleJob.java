package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/** Reconciles only local PAPER exit-plan state; it never submits KIS orders. */
@Component
public class KisPaperExitLifecycleJob {
    private static final Logger log = LoggerFactory.getLogger(KisPaperExitLifecycleJob.class);
    private static final List<KisPaperExitStatus> MONITORED =
            List.of(KisPaperExitStatus.WAITING_FOR_ENTRY, KisPaperExitStatus.EXIT_PENDING);
    private static final int PAGE_SIZE = 100;

    private final KisOrderProperties orderProperties;
    private final KisProperties kis;
    private final MarketOperationalScope marketScope;
    private final KisPaperExitPlanRepository plans;
    private final KisPaperExitPlanLifecycleService lifecycle;
    private final AtomicInteger nextPage = new AtomicInteger();

    public KisPaperExitLifecycleJob(KisOrderProperties orderProperties, KisProperties kis,
                                    MarketOperationalScope marketScope, KisPaperExitPlanRepository plans,
                                    KisPaperExitPlanLifecycleService lifecycle) {
        this.orderProperties = orderProperties;
        this.kis = kis;
        this.marketScope = marketScope;
        this.plans = plans;
        this.lifecycle = lifecycle;
    }

    @Scheduled(fixedDelayString = "${kis.orders.paper-exit-lifecycle-delay-ms:5000}")
    public void refreshPaperExitLifecycle() {
        if (!orderProperties.paperReconciliationEnabled() || !kis.accountReadEnabled()
                || !kis.paperAccountReadEnabled() || !kis.credentials(KisEnvironment.PAPER).configured()
                || !marketScope.allowedMarkets().contains(Market.KR)) return;
        try {
            int pageNumber = nextPage.get();
            Page<KisPaperExitPlan> page = plans.findByStatusInOrderByUpdatedAtAsc(
                    MONITORED, PageRequest.of(pageNumber, PAGE_SIZE));
            nextPage.set(page.getTotalPages() == 0 || pageNumber + 1 >= page.getTotalPages() ? 0 : pageNumber + 1);
            page.getContent().forEach(plan -> lifecycle.synchronize(plan.getEntryIdempotencyKey()));
        } catch (RuntimeException exception) {
            log.warn("KIS PAPER exit-plan lifecycle refresh failed ({})", exception.getClass().getSimpleName());
        }
    }
}
