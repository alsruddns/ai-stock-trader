package com.aistocktrader.kis;

import java.util.List;

public record KisLiveRiskDecision(boolean approved, List<String> reasons) {
    public KisLiveRiskDecision {
        reasons = List.copyOf(reasons);
    }
}
