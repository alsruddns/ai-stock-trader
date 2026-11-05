package com.aistocktrader.order;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import java.util.Map;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(IdempotencyConflictException.class)
    @ResponseStatus(HttpStatus.CONFLICT)
    public Map<String, String> idempotencyConflict(IdempotencyConflictException exception) {
        return Map.of("error", exception.getMessage());
    }

    @ExceptionHandler(IllegalArgumentException.class)
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public Map<String, String> badRequest(IllegalArgumentException exception) {
        return Map.of("error", exception.getMessage());
    }
}
