package com.aistocktrader.kis;

/** Durable lifecycle for a REAL buy's protective-exit workflow. */
public enum KisRealExitStatus {
    NOT_APPLICABLE,
    WAITING_FOR_ENTRY,
    ARMED,
    EXIT_PENDING,
    CLOSED,
    MANUAL_REVIEW
}
