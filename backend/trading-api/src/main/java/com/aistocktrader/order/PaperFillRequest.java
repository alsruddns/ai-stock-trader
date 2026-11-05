package com.aistocktrader.order;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;
import java.util.UUID;

public record PaperFillRequest(@NotNull UUID fillKey,
                               @Positive int quantity,
                               @NotNull @DecimalMin("0.01") BigDecimal fillPrice) {}
