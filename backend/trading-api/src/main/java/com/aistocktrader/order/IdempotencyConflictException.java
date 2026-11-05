package com.aistocktrader.order;

public class IdempotencyConflictException extends RuntimeException {
    public IdempotencyConflictException(String message) { super(message); }
}
