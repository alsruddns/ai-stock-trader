package com.aistocktrader.kis;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.Duration;

/** Recovers abandoned submissions as locked UNKNOWN intents without contacting KIS. */
@Component
public class KisOrderIntentRecoveryJob {
    private static final Logger log = LoggerFactory.getLogger(KisOrderIntentRecoveryJob.class);

    private final KisOrderIntentRepository intents;
    private final Duration submissionTimeout;

    public KisOrderIntentRecoveryJob(KisOrderIntentRepository intents,
                                     @Value("${kis.orders.submitting-recovery-timeout-ms:60000}") long timeoutMillis) {
        if (timeoutMillis < 1000) throw new IllegalArgumentException("Submission recovery timeout must be at least 1000 ms.");
        this.intents = intents;
        this.submissionTimeout = Duration.ofMillis(timeoutMillis);
    }

    @Scheduled(initialDelayString = "${kis.orders.submitting-recovery-initial-delay-ms:30000}",
            fixedDelayString = "${kis.orders.submitting-recovery-fixed-delay-ms:30000}")
    public void recoverStaleSubmissions() {
        int recovered = recoverStaleSubmissionsBefore(Instant.now().minus(submissionTimeout));
        if (recovered > 0) {
            log.warn("Recovered {} stale KIS order submissions as UNKNOWN; no broker requests were sent.", recovered);
        }
    }

    int recoverStaleSubmissionsBefore(Instant cutoff) {
        return intents.recoverStaleSubmittingIntents(cutoff);
    }
}
