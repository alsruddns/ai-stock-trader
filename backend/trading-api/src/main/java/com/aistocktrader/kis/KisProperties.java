package com.aistocktrader.kis;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.ConstructorBinding;
import java.time.Duration;

@ConfigurationProperties(prefix = "kis")
public record KisProperties(boolean accountReadEnabled, boolean paperAccountReadEnabled,
                            boolean realAccountReadEnabled, boolean snapshotPersistenceEnabled,
                            Account paper, Account real, Duration connectTimeout,
                            Duration readTimeout, Duration minRequestInterval) {
    public KisProperties(boolean accountReadEnabled, boolean snapshotPersistenceEnabled,
                         Account paper, Account real, Duration connectTimeout,
                         Duration readTimeout, Duration minRequestInterval) {
        this(accountReadEnabled, accountReadEnabled, false, snapshotPersistenceEnabled,
                paper, real, connectTimeout, readTimeout, minRequestInterval);
    }

    @ConstructorBinding
    public KisProperties {
        paper = paper == null ? new Account("", "", "") : paper;
        real = real == null ? new Account("", "", "") : real;
        connectTimeout = connectTimeout == null ? Duration.ofSeconds(3) : connectTimeout;
        readTimeout = readTimeout == null ? Duration.ofSeconds(8) : readTimeout;
        minRequestInterval = minRequestInterval == null ? Duration.ofMillis(100) : minRequestInterval;
    }

    public Account credentials(KisEnvironment environment) {
        return environment == KisEnvironment.PAPER ? paper : real;
    }

    public record Account(String appKey, String appSecret, String accountNumber) {
        public Account {
            appKey = appKey == null ? "" : appKey.trim();
            appSecret = appSecret == null ? "" : appSecret.trim();
            accountNumber = accountNumber == null ? "" : accountNumber.replace("-", "").trim();
        }
        public boolean configured() {
            return !appKey.isBlank() && !appSecret.isBlank() && accountNumber.matches("\\d{10}");
        }

        @Override
        public String toString() {
            String maskedAccount = accountNumber.isBlank() ? "" : "*".repeat(Math.max(0, accountNumber.length() - 2))
                    + accountNumber.substring(Math.max(0, accountNumber.length() - 2));
            return "Account[appKey=***, appSecret=***, accountNumber=" + maskedAccount + "]";
        }
    }
}
