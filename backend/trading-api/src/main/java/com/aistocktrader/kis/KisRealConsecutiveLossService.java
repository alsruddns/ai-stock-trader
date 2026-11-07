package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/** Reconstructs consecutive net losing trades across both REAL markets; incomplete evidence is unknown. */
@Service
public class KisRealConsecutiveLossService {
    private static final Logger log = LoggerFactory.getLogger(KisRealConsecutiveLossService.class);
    private static final Duration BASELINE_WINDOW = Duration.ofMinutes(3);
    private static final Duration MAX_ACCOUNT_AGE = Duration.ofSeconds(5);
    private final KisProperties properties;
    private final KisAccountClient accounts;
    private final KisMarketSessionClient sessions;
    private final KisRealSessionPositionBaselineStore positionBaselines;
    private final KisRealDailyRiskBaselineService dailyRiskBaselines;
    private final KisOrderIntentRepository intents;
    private final KisRealTradingCostProperties costs;
    private final KisRealizedPnlCalculator calculator = new KisRealizedPnlCalculator();

    public KisRealConsecutiveLossService(KisProperties properties, KisAccountClient accounts,
                                         KisMarketSessionClient sessions,
                                         KisRealSessionPositionBaselineStore positionBaselines,
                                         KisRealDailyRiskBaselineService dailyRiskBaselines,
                                         KisOrderIntentRepository intents,
                                         KisRealTradingCostProperties costs) {
        this.properties = properties;
        this.accounts = accounts;
        this.sessions = sessions;
        this.positionBaselines = positionBaselines;
        this.dailyRiskBaselines = dailyRiskBaselines;
        this.intents = intents;
        this.costs = costs;
    }

    @Transactional(readOnly = true)
    public Optional<RiskHistory> evaluate(Market requestMarket, KisAccountSnapshot requestAccount) {
        if (!properties.accountReadEnabled() || !properties.realAccountReadEnabled()
                || !properties.credentials(KisEnvironment.REAL).configured() || requestMarket == null) {
            return Optional.empty();
        }
        try {
            String fingerprint = dailyRiskBaselines.accountFingerprint();
            List<MarketLedgerInput> ledgers = new ArrayList<>();
            Map<Market, KisAccountSnapshot> snapshots = new HashMap<>();
            for (Market market : Market.values()) {
                var session = sessions.session(market);
                Instant now = Instant.now();
                if (!KisRealDailyRiskBaselineService.freshSession(session, market, now)
                        || session.openAt() == null || session.sessionDate() == null) return Optional.empty();
                var baseline = positionBaselines.get(market, session.sessionDate(), fingerprint);
                if (baseline.isEmpty() || !"KIS_READ_ONLY_REST".equals(baseline.get().source())
                        || baseline.get().capturedAt().isBefore(session.openAt())
                        || baseline.get().capturedAt().isAfter(session.openAt().plus(BASELINE_WINDOW))) {
                    return Optional.empty();
                }
                var marketCosts = costs.forMarket(market);
                if (marketCosts == null || !marketCosts.valid()) return Optional.empty();
                List<KisOrderIntent> orders = intents.findByEnvironmentAndMarketAndCreatedAtGreaterThanEqualOrderByCreatedAtAsc(
                        KisEnvironment.REAL, market, baseline.get().capturedAt());
                List<KisRealizedPnlCalculator.TradeFill> fills = new ArrayList<>();
                for (KisOrderIntent order : orders) {
                    if (order.getStatus() != KisOrderStatus.FILLED && order.getStatus() != KisOrderStatus.CANCELLED
                            && order.getStatus() != KisOrderStatus.REJECTED) return Optional.empty();
                    if (order.getFilledQuantity() == null || order.getFilledQuantity() <= 0) continue;
                    if (order.getAverageFillPrice() == null || order.getAverageFillPrice().signum() <= 0) {
                        return Optional.empty();
                    }
                    if (order.getLastFillAt() == null || order.getLastFillAt().isBefore(session.openAt())
                            || order.getLastFillAt().isAfter(session.closeAt())) return Optional.empty();
                    fills.add(new KisRealizedPnlCalculator.TradeFill(order.getIdempotencyKey().toString(),
                            order.getSymbol(), order.getSide(), order.getFilledQuantity(),
                            order.getAverageFillPrice(), order.getLastFillAt()));
                }
                List<KisRealizedPnlCalculator.OpeningPosition> opening = baseline.get().positions().stream()
                        .map(position -> new KisRealizedPnlCalculator.OpeningPosition(position.symbol(),
                                position.quantity(), position.averagePurchasePrice())).toList();
                ledgers.add(new MarketLedgerInput(market, opening, fills, marketCosts));

                KisAccountSnapshot snapshot = market == requestMarket ? requestAccount
                        : accounts.getBalance(KisEnvironment.REAL, market, null, null, null);
                if (!validSnapshot(snapshot, market, Instant.now())) return Optional.empty();
                snapshots.put(market, snapshot);
            }

            List<KisRealizedPnlCalculator.RealizedTrade> realized = new ArrayList<>();
            int openPositions = 0;
            for (MarketLedgerInput ledger : ledgers) {
                var result = calculator.calculate(ledger.opening(), ledger.fills(), ledger.costs());
                if (!result.valid() || !matchesAccountInventory(result.openPositions(), snapshots.get(ledger.market()))) {
                    return Optional.empty();
                }
                realized.addAll(result.trades());
                openPositions += result.openPositions().size();
            }
            realized.sort(java.util.Comparator.comparing(KisRealizedPnlCalculator.RealizedTrade::executedAt)
                    .thenComparing(KisRealizedPnlCalculator.RealizedTrade::id));
            for (int index = 1; index < realized.size(); index++) {
                if (realized.get(index - 1).executedAt().equals(realized.get(index).executedAt())) return Optional.empty();
            }
            int losses = 0;
            for (int index = realized.size() - 1; index >= 0; index--) {
                if (realized.get(index).netPnl().signum() >= 0) break;
                losses++;
            }
            return Optional.of(new RiskHistory(losses, openPositions));
        } catch (RuntimeException exception) {
            log.warn("KIS REAL consecutive loss evidence unavailable ({})", exception.getClass().getSimpleName());
            return Optional.empty();
        }
    }

    private static boolean validSnapshot(KisAccountSnapshot account, Market market, Instant now) {
        if (account == null || account.environment() != KisEnvironment.REAL || account.market() != market
                || !"KIS_READ_ONLY_REST".equals(account.source()) || account.positions() == null
                || account.retrievedAt() == null || account.retrievedAt().isAfter(now)
                || Duration.between(account.retrievedAt(), now).compareTo(MAX_ACCOUNT_AGE) > 0) return false;
        String currency = market == Market.KR ? "KRW" : "USD";
        if (!currency.equalsIgnoreCase(account.currency())) return false;
        java.util.HashSet<String> symbols = new java.util.HashSet<>();
        for (KisAccountSnapshot.Position position : account.positions()) {
            if (position == null || position.symbol() == null || position.symbol().isBlank()
                    || position.quantity() == null || position.quantity().signum() < 0
                    || !symbols.add(position.symbol().trim().toUpperCase(Locale.ROOT))) return false;
        }
        return true;
    }

    private static boolean matchesAccountInventory(Map<String, BigDecimal> ledger, KisAccountSnapshot account) {
        Map<String, BigDecimal> observed = new HashMap<>();
        account.positions().stream().filter(position -> position.quantity().signum() > 0)
                .forEach(position -> observed.put(position.symbol().trim().toUpperCase(Locale.ROOT), position.quantity()));
        return ledger.equals(observed);
    }

    private record MarketLedgerInput(Market market, List<KisRealizedPnlCalculator.OpeningPosition> opening,
                                     List<KisRealizedPnlCalculator.TradeFill> fills,
                                     KisRealizedPnlCalculator.Costs costs) { }
    public record RiskHistory(int consecutiveLosses, int totalOpenPositionsAcrossMarkets) { }
}
