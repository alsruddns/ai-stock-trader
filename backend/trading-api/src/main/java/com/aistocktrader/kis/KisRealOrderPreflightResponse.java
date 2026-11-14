package com.aistocktrader.kis;

import com.aistocktrader.order.OrderRequest;

import java.time.Instant;
import java.util.List;

/** Order-free assessment result; it is informational and is never an authorization token. */
public record KisRealOrderPreflightResponse(boolean approved, List<String> reasons,
                                            OrderRequest effectiveOrder, Instant assessedAt) {
    public KisRealOrderPreflightResponse {
        reasons = List.copyOf(reasons);
    }
}
