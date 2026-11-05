package com.aistocktrader.order;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import java.math.BigDecimal;

public record PaperDailyRiskBaselineRequest(@NotNull @DecimalMin("0.01") BigDecimal equity) { }
