package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class KisPropertiesTest {
    @Test
    void accountCredentialsAreMaskedInDefaultStringRepresentation() {
        KisProperties.Account account = new KisProperties.Account(
                "app-key-sensitive", "app-secret-sensitive", "1234567801");

        String representation = account.toString();

        assertFalse(representation.contains("app-key-sensitive"));
        assertFalse(representation.contains("app-secret-sensitive"));
        assertFalse(representation.contains("1234567801"));
        assertTrue(representation.contains("********01"));
    }
}
