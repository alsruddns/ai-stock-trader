package com.aistocktrader.kis;

/** Durable PAPER-only lifecycle for a filled signal entry's protective-exit plan. */
public enum KisPaperExitStatus {
    WAITING_FOR_ENTRY,
    ARMED,
    EXIT_PENDING,
    CLOSED,
    MANUAL_REVIEW
}
