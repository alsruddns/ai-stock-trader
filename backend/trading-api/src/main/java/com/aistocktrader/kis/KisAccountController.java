package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.springframework.dao.DataAccessException;
import org.springframework.web.bind.annotation.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.math.BigDecimal;
import java.sql.SQLException;
import java.time.LocalDate;
import java.util.Locale;

@RestController
@RequestMapping("/api/v1/kis/accounts")
public class KisAccountController {
    private static final Logger log = LoggerFactory.getLogger(KisAccountController.class);
    private final KisAccountClient client;
    private final KisProperties properties;
    private final KisSnapshotStore snapshots;
    public KisAccountController(KisAccountClient client, KisProperties properties, KisSnapshotStore snapshots) {
        this.client = client;
        this.properties = properties;
        this.snapshots = snapshots;
    }

    @GetMapping("/{environment}/{market}/balance")
    public KisAccountSnapshot balance(@PathVariable String environment,
                                      @PathVariable Market market,
                                      @RequestParam(required = false) String symbol,
                                      @RequestParam(required = false) BigDecimal unitPrice,
                                      @RequestParam(defaultValue = "NASD") String exchange) {
        KisEnvironment mode;
        try {
            mode = KisEnvironment.valueOf(environment.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException ex) {
            throw new KisApiException(org.springframework.http.HttpStatus.BAD_REQUEST,
                    "Environment must be paper or real.");
        }
        if ((symbol == null) != (unitPrice == null)) {
            throw new KisApiException(org.springframework.http.HttpStatus.BAD_REQUEST,
                    "symbol and unitPrice must be supplied together.");
        }
        if (symbol != null && (symbol.isBlank() || symbol.length() > 16 || unitPrice.signum() <= 0)) {
            throw new KisApiException(org.springframework.http.HttpStatus.BAD_REQUEST,
                    "A valid symbol and positive unitPrice are required.");
        }
        if (market == Market.KR && symbol != null && !symbol.matches("\\d{6}")) {
            throw new KisApiException(org.springframework.http.HttpStatus.BAD_REQUEST,
                    "KR symbol must contain six digits.");
        }
        if (market == Market.US && symbol != null && !symbol.matches("(?i)[A-Z0-9.-]{1,16}")) {
            throw new KisApiException(org.springframework.http.HttpStatus.BAD_REQUEST,
                    "US symbol format is invalid.");
        }
        KisAccountSnapshot snapshot = client.getBalance(mode, market, symbol, unitPrice,
                exchange == null ? "NASD" : exchange.toUpperCase(Locale.ROOT));
        if (properties.snapshotPersistenceEnabled()) {
            try {
                snapshots.save(snapshot);
            } catch (DataAccessException ex) {
                Throwable cause = org.springframework.core.NestedExceptionUtils.getMostSpecificCause(ex);
                String sqlState = cause instanceof SQLException sqlException ? sqlException.getSQLState() : "unknown";
                log.error("KIS account snapshot persistence failed for {} {} (exception={}, sqlState={})",
                        mode, market, ex.getClass().getSimpleName(), sqlState);
                throw new KisApiException(org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE,
                        "KIS account snapshot storage is unavailable.");
            }
        }
        return snapshot;
    }

    @GetMapping("/realized-pnl/real/{market}")
    public KisRealizedPnlStatement realizedPnl(@PathVariable Market market,
                                               @RequestParam LocalDate from,
                                               @RequestParam LocalDate to,
                                               @RequestParam(defaultValue = "NASD") String exchange) {
        LocalDate koreaToday = LocalDate.now(java.time.ZoneId.of("Asia/Seoul"));
        if (from.isAfter(koreaToday) || to.isAfter(koreaToday)) {
            throw new KisApiException(org.springframework.http.HttpStatus.BAD_REQUEST,
                    "Statement dates cannot be in the future.");
        }
        return client.getRealizedPnlStatement(market, from, to,
                exchange == null ? "NASD" : exchange.toUpperCase(Locale.ROOT));
    }
}
