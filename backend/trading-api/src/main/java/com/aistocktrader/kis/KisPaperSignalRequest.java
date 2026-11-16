package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/** A domestic PAPER buy-signal reference; callers cannot choose price, side, quantity, or model. */
public record KisPaperSignalRequest(@NotNull Market market,
                                    @NotBlank @Size(max = 24)
                                    @Pattern(regexp = "^[A-Za-z0-9.-]+$") String symbol) {
    public KisPaperSignalRequest {
        if (symbol != null) symbol = symbol.trim().toUpperCase(java.util.Locale.ROOT);
    }
}
