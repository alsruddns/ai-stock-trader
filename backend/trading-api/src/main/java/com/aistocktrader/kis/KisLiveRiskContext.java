package com.aistocktrader.kis;

import java.math.BigDecimal;

/**
 * Non-broker safety facts required before a live buy may be considered.
 * Package visibility keeps request DTOs in other packages from supplying trusted risk facts.
 */
record KisLiveRiskContext(boolean approvedModelCandidate, boolean quoteFresh,
                          boolean accountSnapshotFresh, boolean marketOpen,
                          boolean unresolvedOrderExists,
                          BigDecimal dailyLossPercent, int consecutiveLosingTrades,
                          BigDecimal accountEquity, BigDecimal stopDistancePerShare,
                          Integer totalOpenPositionsAcrossMarkets,
                          boolean buyLimitPriceWithinLatestQuote,
                          boolean continuousEntryWindowOpen) {
    KisLiveRiskContext(boolean approvedModelCandidate, boolean quoteFresh,
                       boolean accountSnapshotFresh, boolean marketOpen,
                       boolean unresolvedOrderExists, BigDecimal dailyLossPercent,
                       int consecutiveLosingTrades, BigDecimal accountEquity,
                       BigDecimal stopDistancePerShare, Integer totalOpenPositionsAcrossMarkets) {
        this(approvedModelCandidate, quoteFresh, accountSnapshotFresh, marketOpen, unresolvedOrderExists,
                dailyLossPercent, consecutiveLosingTrades, accountEquity, stopDistancePerShare,
                totalOpenPositionsAcrossMarkets, true, true);
    }

    KisLiveRiskContext(boolean approvedModelCandidate, boolean quoteFresh,
                       boolean accountSnapshotFresh, boolean marketOpen,
                       boolean unresolvedOrderExists, BigDecimal dailyLossPercent,
                       int consecutiveLosingTrades, BigDecimal accountEquity,
                       BigDecimal stopDistancePerShare, Integer totalOpenPositionsAcrossMarkets,
                       boolean buyLimitPriceWithinLatestQuote) {
        this(approvedModelCandidate, quoteFresh, accountSnapshotFresh, marketOpen, unresolvedOrderExists,
                dailyLossPercent, consecutiveLosingTrades, accountEquity, stopDistancePerShare,
                totalOpenPositionsAcrossMarkets, buyLimitPriceWithinLatestQuote, true);
    }
}
