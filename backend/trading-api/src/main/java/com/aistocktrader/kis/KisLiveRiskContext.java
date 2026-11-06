package com.aistocktrader.kis;

import java.math.BigDecimal;

/**
 * Non-broker safety facts required before a live buy may be considered.
 * Package visibility keeps request DTOs in other packages from supplying trusted risk facts.
 */
record KisLiveRiskContext(boolean approvedModelCandidate, boolean quoteFresh,
                          boolean accountSnapshotFresh, boolean marketOpen,
                          boolean unresolvedOrderExists, int totalOpenPositions,
                          BigDecimal dailyLossPercent, int consecutiveLosingTrades,
                          BigDecimal accountEquity, BigDecimal stopDistancePerShare) { }
