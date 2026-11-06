package com.aistocktrader.kis;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Modifier;

import static org.junit.jupiter.api.Assertions.assertFalse;

class KisLiveRiskContextVisibilityTest {
    @Test
    void trustedLiveRiskFactsAreNotPubliclyConstructible() {
        assertFalse(Modifier.isPublic(KisLiveRiskContext.class.getModifiers()));
        assertFalse(Modifier.isPublic(KisLiveRiskContext.class.getDeclaredConstructors()[0].getModifiers()));
    }
}
