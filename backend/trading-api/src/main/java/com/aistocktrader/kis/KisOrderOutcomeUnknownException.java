package com.aistocktrader.kis;

/** The request may have reached KIS. Reconcile broker orders before considering any further action. */
public class KisOrderOutcomeUnknownException extends RuntimeException {
    public KisOrderOutcomeUnknownException(String message, Throwable cause) { super(message, cause); }
}
