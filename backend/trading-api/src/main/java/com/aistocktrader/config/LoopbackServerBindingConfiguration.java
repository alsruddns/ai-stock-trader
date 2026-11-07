package com.aistocktrader.config;

import org.springframework.boot.web.server.ConfigurableWebServerFactory;
import org.springframework.boot.web.server.WebServerFactoryCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;

import java.net.InetAddress;

@Configuration
public class LoopbackServerBindingConfiguration {
    @Bean
    WebServerFactoryCustomizer<ConfigurableWebServerFactory> loopbackOnlyServerBinding() {
        return new LoopbackOnlyServerBindingCustomizer();
    }

    private static final class LoopbackOnlyServerBindingCustomizer
            implements WebServerFactoryCustomizer<ConfigurableWebServerFactory>, Ordered {
        @Override
        public void customize(ConfigurableWebServerFactory factory) {
            factory.setAddress(InetAddress.getLoopbackAddress());
        }

        @Override
        public int getOrder() {
            return Ordered.LOWEST_PRECEDENCE;
        }
    }
}
