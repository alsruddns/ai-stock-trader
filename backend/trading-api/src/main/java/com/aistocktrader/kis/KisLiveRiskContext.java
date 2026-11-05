package com.aistocktrader.kis;

import java.math.BigDecimal;

/**
 * Non-broker safety facts required before a live buy may be considered.
 * Construct this only inside the trusted signal/risk pipeline; never bind it from public HTTP input.
 */
public record KisLiveRiskContext(boolean approvedModelCandidate, boolean quoteFresh,
                                 boolean accountSnapshotFresh, boolean marketOpen,
                                 boolean unresolvedOrderExists, int totalOpenPositions,
                                 BigDecimal dailyLossPercent, int consecutiveLosingTrades,
                                 BigDecimal accountEquity, BigDecimal stopDistancePerShare) { }
