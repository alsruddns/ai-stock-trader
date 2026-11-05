package com.aistocktrader.kis;

import org.springframework.http.HttpStatus;

public class KisApiException extends RuntimeException {
    private final HttpStatus status;
    public KisApiException(HttpStatus status, String message) {
        super(message);
        this.status = status;
    }
    public HttpStatus status() { return status; }
}
