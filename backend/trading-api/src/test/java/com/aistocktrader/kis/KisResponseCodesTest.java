package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class KisResponseCodesTest {
    @Test
    void extractsOnlyAWholeDelimitedSafeKisCode() {
        assertEquals("EGW00201", KisResponseCodes.fromErrorMessage(
                "KIS rejected the paper order (code EGW00201).", "rejected"));
    }

    @Test
    void rejectsPartialMatchesAndFallsBackWithoutPersistingErrorDetails() {
        assertEquals("rejected", KisResponseCodes.fromErrorMessage(
                "KIS rejected the paper order (code private.secret-value).", "rejected"));
        assertEquals("rejected", KisResponseCodes.fromErrorMessage(
                "account=12345678; secret=hidden", "rejected"));
        assertEquals("rejected", KisResponseCodes.fromErrorMessage(null, "rejected"));
    }
}
