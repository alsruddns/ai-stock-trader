package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;
import java.util.HashSet;
import java.util.Locale;
import java.util.OptionalInt;
import java.util.Set;

/** Counts distinct held instruments across the KIS domestic and US account snapshots. */
@Component
public class KisRealAccountPortfolioPositionCounter {
    private static final Duration MAX_SNAPSHOT_AGE = Duration.ofSeconds(5);
    private final KisAccountClient accounts;

    public KisRealAccountPortfolioPositionCounter(KisAccountClient accounts) {
        this.accounts = accounts;
    }

    /**
     * The caller supplies the fresh account snapshot for the order market. This method reads the
     * other market from KIS and returns empty unless both snapshots are complete, current, and
     * unambiguous. NASD is KIS's documented REAL query code for all US exchanges.
     */
    public OptionalInt countOpenPositionsAcrossMarkets(KisAccountSnapshot orderMarketAccount) {
        if (!valid(orderMarketAccount, orderMarketAccount == null ? null : orderMarketAccount.market(), Instant.now())) {
            return OptionalInt.empty();
        }
        Market otherMarket = orderMarketAccount.market() == Market.KR ? Market.US : Market.KR;
        try {
            KisAccountSnapshot other = accounts.getBalance(KisEnvironment.REAL, otherMarket, null, null,
                    otherMarket == Market.US ? "NASD" : null);
            Instant checkedAt = Instant.now();
            if (!valid(orderMarketAccount, orderMarketAccount.market(), checkedAt)
                    || !valid(other, otherMarket, checkedAt)) {
                return OptionalInt.empty();
            }
            return OptionalInt.of(count(orderMarketAccount) + count(other));
        } catch (RuntimeException unavailable) {
            return OptionalInt.empty();
        }
    }

    private static boolean valid(KisAccountSnapshot account, Market expectedMarket, Instant now) {
        if (account == null || expectedMarket == null || account.environment() != KisEnvironment.REAL
                || account.market() != expectedMarket || !"KIS_READ_ONLY_REST".equals(account.source())
                || account.retrievedAt() == null || account.retrievedAt().isAfter(now)
                || Duration.between(account.retrievedAt(), now).compareTo(MAX_SNAPSHOT_AGE) > 0
                || account.positions() == null) {
            return false;
        }
        String currency = expectedMarket == Market.KR ? "KRW" : "USD";
        if (!currency.equals(account.currency())) return false;

        Set<String> symbols = new HashSet<>();
        for (KisAccountSnapshot.Position position : account.positions()) {
            if (position == null || position.symbol() == null || position.symbol().isBlank()
                    || position.quantity() == null || position.quantity().signum() < 0) return false;
            String symbol = position.symbol().trim().toUpperCase(Locale.ROOT);
            if (!symbols.add(symbol)) return false;
        }
        return true;
    }

    private static int count(KisAccountSnapshot account) {
        return (int) account.positions().stream().filter(position -> position.quantity().signum() > 0).count();
    }
}
