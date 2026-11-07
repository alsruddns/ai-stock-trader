package com.aistocktrader.order;

import org.junit.jupiter.api.Test;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.CannotCreateTransactionException;
import org.springframework.transaction.TransactionSystemException;
import com.aistocktrader.kis.KisApiException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

class ApiExceptionHandlerTest {
    @Test
    void kisErrorsExposeSanitizedBrokerCodeWithoutRawResponse() {
        ApiExceptionHandler handler = new ApiExceptionHandler();
        KisApiException exception = new KisApiException(HttpStatus.BAD_GATEWAY,
                "KIS authentication request was rejected (HTTP 403, code EGW00002).");

        var response = handler.kisError(exception);

        assertEquals(HttpStatus.BAD_GATEWAY, response.getStatusCode());
        assertEquals("KIS authentication request was rejected (HTTP 403, code EGW00002).",
                response.getBody().get("error"));
        assertFalse(response.getBody().toString().contains("appsecret"));
        assertFalse(response.getBody().toString().contains("error_description"));
    }

    @Test
    void databaseFailuresReturnServiceUnavailableWithoutLeakingDriverDetails() {
        ApiExceptionHandler handler = new ApiExceptionHandler();

        var response = handler.databaseUnavailable(
                new DataAccessResourceFailureException("password=private; host=internal"));

        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, response.getStatusCode());
        assertEquals("Local database is unavailable; no order result was confirmed.",
                response.getBody().get("error"));
        assertFalse(response.getBody().get("error").contains("private"));
        assertFalse(response.getBody().get("error").contains("internal"));

        var commitFailure = handler.databaseUnavailable(new TransactionSystemException("commit failed"));
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, commitFailure.getStatusCode());

        var connectionFailure = handler.databaseUnavailable(
                new CannotCreateTransactionException("database connection failed"));
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, connectionFailure.getStatusCode());
    }
}
