package com.aistocktrader.kis;

/** Rejects incomplete or contradictory broker quantities before they change a persisted order. */
final class KisBrokerOrderSnapshotValidator {
    private KisBrokerOrderSnapshotValidator() { }

    static boolean matchesOrderQuantity(KisOrderIntent intent, KisBrokerOrderSnapshot snapshot) {
        if (intent == null || snapshot == null || snapshot.filledQuantity() < 0
                || snapshot.remainingQuantity() < 0 || snapshot.filledQuantity() > intent.getQuantity()
                || snapshot.filledQuantity() + snapshot.remainingQuantity() > intent.getQuantity()
                || (snapshot.cancelled() && snapshot.rejected())) {
            return false;
        }

        if (snapshot.cancelled()) return snapshot.remainingQuantity() == 0
                && snapshot.filledQuantity() < intent.getQuantity();
        if (snapshot.rejected()) return snapshot.filledQuantity() == 0 && snapshot.remainingQuantity() == 0;
        return snapshot.filledQuantity() + snapshot.remainingQuantity() == intent.getQuantity();
    }
}
