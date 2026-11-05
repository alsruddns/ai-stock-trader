package com.aistocktrader;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

@SpringBootApplication
@ConfigurationPropertiesScan
public class TradingApiApplication {
    public static void main(String[] args) {
        SpringApplication.run(TradingApiApplication.class, args);
    }
}
