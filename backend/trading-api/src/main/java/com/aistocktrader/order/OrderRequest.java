package com.aistocktrader.order;

import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;

public record OrderRequest(@NotNull Market market,
                           @NotBlank @Size(max = 24) @Pattern(regexp = "^[A-Za-z0-9.-]+$") String symbol,
                           @NotNull OrderSide side,
                           @Positive int quantity, @NotNull @DecimalMin("0.01") BigDecimal limitPrice,
                           @NotNull Currency currency) {}
