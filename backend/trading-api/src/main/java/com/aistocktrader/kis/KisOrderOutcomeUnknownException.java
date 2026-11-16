package com.aistocktrader.kis;

/** The request may have reached KIS. Reconcile broker orders before considering any further action. */
public class KisOrderOutcomeUnknownException extends RuntimeException {
    private final String diagnosticCode;

    public KisOrderOutcomeUnknownException(String message, Throwable cause) {
        this(message, cause, "ORDER_TRANSPORT_UNKNOWN");
    }

    public KisOrderOutcomeUnknownException(String message, Throwable cause, String diagnosticCode) {
        super(message, cause);
        this.diagnosticCode = diagnosticCode != null && diagnosticCode.matches("[A-Za-z0-9_-]{1,32}")
                ? diagnosticCode : "ORDER_OUTCOME_UNKNOWN";
    }

    /** A bounded code safe to persist; never includes exception text or broker response bodies. */
    public String diagnosticCode() { return diagnosticCode; }
}
