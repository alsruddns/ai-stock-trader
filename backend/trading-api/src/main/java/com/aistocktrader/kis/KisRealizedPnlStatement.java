package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.fasterxml.jackson.databind.JsonNode;

import java.time.Instant;
import java.time.LocalDate;

/** Read-only broker statement payload; raw KIS fields are not treated as reconciled accounting values. */
public record KisRealizedPnlStatement(Market market, LocalDate from, LocalDate to, String exchange,
                                      Instant retrievedAt, JsonNode details, JsonNode summary, String source) { }
