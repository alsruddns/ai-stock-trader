package com.aistocktrader.kis;

/** Exact domestic PAPER broker identity returned by a read-only KIS daily execution query. */
record KisManualPaperOrderMatch(String brokerOrganizationNumber, KisBrokerOrderSnapshot snapshot) {
    KisManualPaperOrderMatch {
        if (snapshot == null) {
            throw new IllegalArgumentException("A KIS order snapshot is required.");
        }
    }
}
