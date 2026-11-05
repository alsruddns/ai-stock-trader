package com.aistocktrader.kis;

import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Process-wide pacing shared by the Spring KIS account and order clients. */
final class KisRestRateLimiter {
    private static final long PAPER_FLOOR_MILLIS = 1_000;
    private static final long REAL_FLOOR_MILLIS = 100;
    private static final Map<KisEnvironment, Object> LOCKS = new ConcurrentHashMap<>();
    private static final Map<KisEnvironment, Long> NEXT_SLOT_NANOS = new ConcurrentHashMap<>();

    static {
        for (KisEnvironment environment : KisEnvironment.values()) LOCKS.put(environment, new Object());
    }

    private KisRestRateLimiter() { }

    static void acquire(KisEnvironment environment, Duration configuredMinimum) throws InterruptedException {
        long floor = environment == KisEnvironment.PAPER ? PAPER_FLOOR_MILLIS : REAL_FLOOR_MILLIS;
        long configured = configuredMinimum == null ? 0 : configuredMinimum.toMillis();
        long intervalNanos = Duration.ofMillis(Math.max(configured, floor)).toNanos();
        synchronized (LOCKS.get(environment)) {
            long now = System.nanoTime();
            long slot = Math.max(now, NEXT_SLOT_NANOS.getOrDefault(environment, 0L));
            long delayNanos = slot - now;
            if (delayNanos > 0) {
                long delayMillis = (delayNanos + 999_999) / 1_000_000;
                Thread.sleep(delayMillis);
            }
            NEXT_SLOT_NANOS.put(environment, slot + intervalNanos);
        }
    }
}
