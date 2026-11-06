package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Autowired;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Optional;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Captures immutable KIS account-equity baselines at the verified session open and computes drawdown. */
@Service
public class KisRealDailyRiskBaselineService {
    private static final Logger log = LoggerFactory.getLogger(KisRealDailyRiskBaselineService.class);
    private static final Duration CAPTURE_WINDOW = Duration.ofMinutes(3);
    private static final Duration MAX_SESSION_EVIDENCE_AGE = Duration.ofSeconds(15);
    private static final Duration MAX_ACCOUNT_SNAPSHOT_AGE = Duration.ofSeconds(5);
    private final KisProperties properties;
    private final KisAccountClient accounts;
    private final KisMarketSessionClient sessions;
    private final KisRealDailyRiskBaselineStore baselines;
    private final KisRealSessionPositionBaselineStore positionBaselines;

    @Autowired
    public KisRealDailyRiskBaselineService(KisProperties properties, KisAccountClient accounts,
                                           KisMarketSessionClient sessions,
                                           KisRealDailyRiskBaselineStore baselines,
                                           KisRealSessionPositionBaselineStore positionBaselines) {
        this.properties = properties;
        this.accounts = accounts;
        this.sessions = sessions;
        this.baselines = baselines;
        this.positionBaselines = positionBaselines;
    }

    @Scheduled(fixedDelayString = "${kis.real-risk-baseline-poll-ms:30000}")
    public void captureDomesticSessionBaseline() {
        if (!properties.accountReadEnabled() || !properties.realAccountReadEnabled()
                || !properties.credentials(KisEnvironment.REAL).configured()) return;
        try {
            KisMarketSessionClient.MarketSession session = sessions.session(Market.KR);
            Instant now = Instant.now();
            if (!openingWindow(session, Market.KR, now)) return;
            String fingerprint = accountFingerprint();
            boolean needsPositions = !positionBaselines.exists(Market.KR, session.sessionDate(), fingerprint);
            boolean needsEquity = !baselines.exists(Market.KR, session.sessionDate(), fingerprint);
            if (!needsPositions && !needsEquity) return;
            KisAccountSnapshot account = accounts.getBalance(KisEnvironment.REAL, Market.KR, null, null, null);
            if (!validOpeningAccount(account, Market.KR, now, session)) return;
            if (needsPositions) {
                positionBaselines.recordOnce(Market.KR, session.sessionDate(), account.currency(), fingerprint,
                        account.positions(), account.retrievedAt());
            }
            if (needsEquity && account.accountEquity() != null && account.accountEquity().signum() > 0) {
                baselines.recordOnce(Market.KR, session.sessionDate(), account.currency(), fingerprint,
                        account.accountEquity(), account.retrievedAt());
            }
        } catch (RuntimeException exception) {
            log.warn("KIS REAL daily baseline capture unavailable ({})", exception.getClass().getSimpleName());
        }
    }

    /** Captures the KIS-reported opening inventory for US sessions; this is read-only and immutable per session. */
    @Scheduled(fixedDelayString = "${kis.real-risk-baseline-poll-ms:30000}")
    public void captureUsSessionPositionBaseline() {
        if (!properties.accountReadEnabled() || !properties.realAccountReadEnabled()
                || !properties.credentials(KisEnvironment.REAL).configured()) return;
        try {
            KisMarketSessionClient.MarketSession session = sessions.session(Market.US);
            Instant now = Instant.now();
            if (!openingWindow(session, Market.US, now)) return;
            String fingerprint = accountFingerprint();
            if (positionBaselines.exists(Market.US, session.sessionDate(), fingerprint)) return;
            KisAccountSnapshot account = accounts.getBalance(KisEnvironment.REAL, Market.US, null, null, null);
            if (!validOpeningAccount(account, Market.US, now, session)) return;
            positionBaselines.recordOnce(Market.US, session.sessionDate(), account.currency(), fingerprint,
                    account.positions(), account.retrievedAt());
        } catch (RuntimeException exception) {
            log.warn("KIS REAL US opening position baseline unavailable ({})", exception.getClass().getSimpleName());
        }
    }

    /** Returns null unless session, account snapshot, and immutable opening baseline are all trustworthy. */
    public BigDecimal dailyLossPercent(Market market, KisAccountSnapshot current) {
        return riskInputs(market, current).dailyLossPercent();
    }

    public RiskInputs riskInputs(Market market, KisAccountSnapshot current) {
        try {
            KisMarketSessionClient.MarketSession session = sessions.session(market);
            Instant now = Instant.now();
            if (!freshSession(session, market, now) || !session.session() || !validAccount(current, market, now)) {
                return new RiskInputs(false, null);
            }
            boolean marketOpen = session.open() && session.openAt() != null && session.closeAt() != null
                    && !now.isBefore(session.openAt()) && now.isBefore(session.closeAt());
            Optional<BigDecimal> baseline = baselines.equity(market, session.sessionDate(), accountFingerprint());
            if (baseline.isEmpty() || baseline.get().signum() <= 0 || current.accountEquity() == null
                    || current.accountEquity().signum() <= 0) return new RiskInputs(marketOpen, null);
            BigDecimal loss = baseline.get().subtract(current.accountEquity())
                    .multiply(new BigDecimal("100")).divide(baseline.get(), 8, java.math.RoundingMode.HALF_UP);
            return new RiskInputs(marketOpen, loss.max(BigDecimal.ZERO));
        } catch (RuntimeException exception) {
            log.warn("KIS REAL daily risk baseline unavailable for {} ({})",
                    market, exception.getClass().getSimpleName());
            return new RiskInputs(false, null);
        }
    }

    public record RiskInputs(boolean marketOpen, BigDecimal dailyLossPercent) { }

    static boolean freshSession(KisMarketSessionClient.MarketSession session, Market market, Instant now) {
        if (session == null || session.market() != market || session.sessionDate() == null
                || session.evaluatedAt() == null || session.evaluatedAt().isAfter(now)) return false;
        return Duration.between(session.evaluatedAt(), now).compareTo(MAX_SESSION_EVIDENCE_AGE) <= 0;
    }

    private static boolean openingWindow(KisMarketSessionClient.MarketSession session, Market market, Instant now) {
        return freshSession(session, market, now) && session.session() && session.open()
                && session.openAt() != null && !now.isBefore(session.openAt())
                && !now.isAfter(session.openAt().plus(CAPTURE_WINDOW));
    }

    private static boolean validOpeningAccount(KisAccountSnapshot account, Market market, Instant now,
                                               KisMarketSessionClient.MarketSession session) {
        return validAccount(account, market, now) && openingWindow(session, market, now)
                && !account.retrievedAt().isBefore(session.openAt())
                && !account.retrievedAt().isAfter(session.openAt().plus(CAPTURE_WINDOW));
    }

    private static boolean validAccount(KisAccountSnapshot account, Market market, Instant now) {
        if (account == null || account.environment() != KisEnvironment.REAL || account.market() != market
                || !"KIS_READ_ONLY_REST".equals(account.source()) || account.retrievedAt() == null
                || account.retrievedAt().isAfter(now)
                || Duration.between(account.retrievedAt(), now).compareTo(MAX_ACCOUNT_SNAPSHOT_AGE) > 0) return false;
        return market == Market.KR ? "KRW".equals(account.currency()) : "USD".equals(account.currency());
    }

    private String accountFingerprint() {
        KisProperties.Account account = properties.credentials(KisEnvironment.REAL);
        if (!account.configured()) throw new IllegalStateException("KIS REAL credentials are not configured.");
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(account.appSecret().getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            byte[] value = (account.appKey() + "|" + account.accountNumber()).getBytes(StandardCharsets.UTF_8);
            return HexFormat.of().formatHex(mac.doFinal(value));
        } catch (GeneralSecurityException exception) {
            throw new IllegalStateException("KIS account identity fingerprint is unavailable.");
        }
    }
}
