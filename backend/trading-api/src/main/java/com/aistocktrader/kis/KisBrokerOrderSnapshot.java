package com.aistocktrader.kis;

import java.math.BigDecimal;

public record KisBrokerOrderSnapshot(int filledQuantity, int remainingQuantity,
                                     BigDecimal averageFillPrice, boolean cancelled,
                                     boolean rejected) { }
