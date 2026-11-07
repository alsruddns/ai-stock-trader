package com.aistocktrader.config;

import org.junit.jupiter.api.Test;
import org.springframework.boot.web.server.ConfigurableWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.core.Ordered;

import java.net.InetAddress;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoMoreInteractions;

class LoopbackServerBindingConfigurationTest {
    @Test
    void appliesLoopbackBindingAfterOtherServerCustomizers() {
        ConfigurableWebServerFactory factory = mock(ConfigurableWebServerFactory.class);
        WebServerFactoryCustomizer<ConfigurableWebServerFactory> customizer =
                new LoopbackServerBindingConfiguration().loopbackOnlyServerBinding();

        customizer.customize(factory);

        var address = org.mockito.ArgumentCaptor.forClass(InetAddress.class);
        verify(factory).setAddress(address.capture());
        assertTrue(address.getValue().isLoopbackAddress());
        assertEquals(Ordered.LOWEST_PRECEDENCE, ((Ordered) customizer).getOrder());
        verifyNoMoreInteractions(factory);
    }
}
