package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/** A buy-signal reference only; callers cannot choose a price, side, quantity, model, or strategy. */
public record KisRealSignalRequest(@NotNull Market market,
                                   @NotBlank @Size(max = 24)
                                   @Pattern(regexp = "^[A-Za-z0-9.-]+$") String symbol) {
    public KisRealSignalRequest {
        if (symbol != null) symbol = symbol.trim().toUpperCase(java.util.Locale.ROOT);
    }
}
