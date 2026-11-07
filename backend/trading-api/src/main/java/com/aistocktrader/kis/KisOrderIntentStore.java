package com.aistocktrader.kis;

import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.Market;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.NoSuchElementException;
import java.util.UUID;

@Service
public class KisOrderIntentStore {
    private static final java.util.List<KisOrderStatus> UNRESOLVED_STATUSES = java.util.List.of(
            KisOrderStatus.SUBMITTING, KisOrderStatus.ACCEPTED, KisOrderStatus.PARTIALLY_FILLED,
            KisOrderStatus.CANCEL_PENDING, KisOrderStatus.UNKNOWN);
    private final KisOrderIntentRepository repository;
    private final KisRealFillSnapshotStore realFillSnapshots;

    @Autowired
    public KisOrderIntentStore(KisOrderIntentRepository repository, KisRealFillSnapshotStore realFillSnapshots) {
        this.repository = repository;
        this.realFillSnapshots = realFillSnapshots;
    }

    public KisOrderIntentStore(KisOrderIntentRepository repository) {
        this(repository, null);
    }

    @Transactional
    public PreparedIntent prepare(UUID key, String hash, OrderRequest request, String exchange) {
        return prepare(key, hash, KisEnvironment.PAPER, request, exchange);
    }

    @Transactional
    public PreparedIntent prepare(UUID key, String hash, KisEnvironment environment, OrderRequest request, String exchange) {
        return prepare(key, hash, environment, request, exchange, null, null);
    }

    @Transactional
    public PreparedIntent prepare(UUID key, String hash, KisEnvironment environment, OrderRequest request,
                                  String exchange, java.math.BigDecimal exitStopDistancePerShare,
                                  java.math.BigDecimal exitRewardRiskMultiple) {
        var existing = repository.findById(key);
        if (existing.isPresent()) {
            KisOrderIntent intent = existing.get();
            if (intent.getEnvironment() != environment) {
                throw new com.aistocktrader.order.IdempotencyConflictException(
                        "Idempotency-Key was already used in a different KIS environment.");
            }
            if (!intent.getRequestHash().equals(hash)) throw new com.aistocktrader.order.IdempotencyConflictException("Idempotency-Key was already used for a different KIS order.");
            return new PreparedIntent(intent, false);
        }
        return new PreparedIntent(repository.saveAndFlush(new KisOrderIntent(key, hash, environment, request,
                exchange, exitStopDistancePerShare, exitRewardRiskMultiple)), true);
    }

    @Transactional
    public KisOrderIntent accept(UUID key, KisOrderReceipt receipt) {
        KisOrderIntent intent = repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
        intent.accept(receipt);
        return intent;
    }

    @Transactional
    public KisOrderIntent markUnknown(UUID key) {
        KisOrderIntent intent = repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
        intent.markUnknown();
        return intent;
    }

    @Transactional
    public KisOrderIntent reject(UUID key, String code) {
        KisOrderIntent intent = repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
        intent.reject(code);
        return intent;
    }

    @Transactional
    public KisOrderIntent reconcile(UUID key, KisBrokerOrderSnapshot snapshot) {
        KisOrderIntent intent = repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
        if (realFillSnapshots != null) realFillSnapshots.record(intent, snapshot);
        intent.reconcile(snapshot.filledQuantity(), snapshot.remainingQuantity(), snapshot.averageFillPrice(),
                snapshot.cancelled(), snapshot.rejected());
        return intent;
    }

    @Transactional
    public KisOrderIntent recordCancelRejection(UUID key, String code) {
        KisOrderIntent intent = repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
        intent.recordCancelRejection(code);
        return intent;
    }

    @Transactional
    public KisOrderIntent recordCancelPreflightBlocked(UUID key, String code) {
        KisOrderIntent intent = repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
        intent.recordCancelPreflightBlocked(code);
        return intent;
    }

    @Transactional
    public KisOrderIntent markCancelPending(UUID key) {
        KisOrderIntent intent = repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
        if (intent.getStatus() == KisOrderStatus.CANCEL_PENDING || intent.getStatus() == KisOrderStatus.CANCELLED) return intent;
        if (intent.getStatus() != KisOrderStatus.ACCEPTED && intent.getStatus() != KisOrderStatus.PARTIALLY_FILLED) {
            throw new IllegalStateException("Only accepted or partially filled KIS orders can be cancelled.");
        }
        intent.markCancelPending();
        return intent;
    }

    /** Atomically grants one caller the right to submit a broker cancellation. */
    @Transactional
    public boolean claimCancelPending(UUID key, KisEnvironment environment) {
        return repository.claimCancelPending(key, environment.name()) == 1;
    }

    @Transactional(readOnly = true)
    public boolean hasUnresolvedRealOrder(Market market, UUID exceptIdempotencyKey) {
        if (market == null || exceptIdempotencyKey == null) return true;
        return repository.existsByEnvironmentAndMarketAndStatusInAndIdempotencyKeyNot(
                KisEnvironment.REAL, market, UNRESOLVED_STATUSES, exceptIdempotencyKey);
    }

    @Transactional(readOnly = true)
    public KisOrderIntent get(UUID key) {
        return repository.findById(key).orElseThrow(() -> new NoSuchElementException("KIS order intent not found."));
    }

    public record PreparedIntent(KisOrderIntent intent, boolean createdNow) { }
}
