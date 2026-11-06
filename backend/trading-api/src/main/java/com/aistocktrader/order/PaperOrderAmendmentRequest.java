package com.aistocktrader.order;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;

public record PaperOrderAmendmentRequest(@Positive int quantity,
                                         @NotNull @DecimalMin("0.01") BigDecimal limitPrice) { }
