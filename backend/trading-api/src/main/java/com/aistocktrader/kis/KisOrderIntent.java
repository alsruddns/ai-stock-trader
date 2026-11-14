package com.aistocktrader.kis;

import com.aistocktrader.order.Currency;
import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderRequest;
import com.aistocktrader.order.OrderSide;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Locale;
import java.util.UUID;

@Entity
@Table(name = "kis_order_intent")
public class KisOrderIntent {
    @Id
    @Column(name = "idempotency_key", nullable = false)
    private UUID idempotencyKey;
    @Column(name = "request_hash", nullable = false, length = 64)
    private String requestHash;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 8)
    private KisEnvironment environment;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 2)
    private Market market;
    @Column(nullable = false, length = 24)
    private String symbol;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 4)
    private OrderSide side;
    @Column(nullable = false)
    private int quantity;
    @Column(name = "limit_price", nullable = false, precision = 20, scale = 6)
    private BigDecimal limitPrice;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 3)
    private Currency currency;
    @Enumerated(EnumType.STRING) @Column(nullable = false, length = 24)
    private KisOrderStatus status;
    @Column(name = "broker_order_number", length = 32)
    private String brokerOrderNumber;
    @Column(name = "broker_organization_number", length = 32)
    private String brokerOrganizationNumber;
    @Column(name = "broker_response_code", length = 32)
    private String brokerResponseCode;
    @Column(name = "exchange_code", length = 8)
    private String exchangeCode;
    @Column(name = "filled_quantity")
    private Integer filledQuantity;
    @Column(name = "remaining_quantity")
    private Integer remainingQuantity;
    @Column(name = "average_fill_price", precision = 20, scale = 6)
    private BigDecimal averageFillPrice;
    @Column(name = "exit_stop_distance_per_share", precision = 20, scale = 6)
    private BigDecimal exitStopDistancePerShare;
    @Column(name = "exit_reward_risk_multiple", precision = 10, scale = 4)
    private BigDecimal exitRewardRiskMultiple;
    @Enumerated(EnumType.STRING) @Column(name = "real_exit_status", nullable = false, length = 24)
    private KisRealExitStatus realExitStatus = KisRealExitStatus.NOT_APPLICABLE;
    @Column(name = "real_exit_reason", length = 24)
    private String realExitReason;
    @Column(name = "real_exit_order_key")
    private UUID realExitOrderKey;
    @Column(name = "real_exit_quantity")
    private Integer realExitQuantity;
    @Column(name = "real_exit_limit_price", precision = 20, scale = 6)
    private BigDecimal realExitLimitPrice;
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;
    @Column(name = "last_fill_at")
    private Instant lastFillAt;

    protected KisOrderIntent() { }

    public KisOrderIntent(UUID key, String hash, OrderRequest request, String exchange) {
        this(key, hash, KisEnvironment.PAPER, request, exchange);
    }

    public KisOrderIntent(UUID key, String hash, KisEnvironment environment, OrderRequest request, String exchange) {
        this(key, hash, environment, request, exchange, null, null);
    }

    public KisOrderIntent(UUID key, String hash, KisEnvironment environment, OrderRequest request, String exchange,
                          BigDecimal exitStopDistancePerShare, BigDecimal exitRewardRiskMultiple) {
        idempotencyKey = key; requestHash = hash; market = request.market();
        this.environment = java.util.Objects.requireNonNull(environment, "environment");
        symbol = request.symbol().trim().toUpperCase(Locale.ROOT); side = request.side();
        quantity = request.quantity(); limitPrice = request.limitPrice(); currency = request.currency();
        exchangeCode = exchange == null ? null : exchange.trim().toUpperCase(Locale.ROOT);
        if (exitStopDistancePerShare != null || exitRewardRiskMultiple != null) {
            if (environment != KisEnvironment.REAL || request.side() != OrderSide.BUY
                    || exitStopDistancePerShare == null || exitStopDistancePerShare.signum() <= 0
                    || exitRewardRiskMultiple == null || exitRewardRiskMultiple.signum() <= 0
                    || request.limitPrice().compareTo(exitStopDistancePerShare) <= 0) {
                throw new IllegalArgumentException("A REAL buy exit plan requires valid stop distance and reward/risk inputs.");
            }
            this.exitStopDistancePerShare = exitStopDistancePerShare;
            this.exitRewardRiskMultiple = exitRewardRiskMultiple;
            this.realExitStatus = KisRealExitStatus.WAITING_FOR_ENTRY;
        }
        filledQuantity = 0; remainingQuantity = request.quantity();
        status = KisOrderStatus.SUBMITTING; createdAt = Instant.now(); updatedAt = createdAt;
    }

    public void accept(KisOrderReceipt receipt) {
        if (!matches(receipt)) {
            markUnknown();
            return;
        }
        status = KisOrderStatus.ACCEPTED; brokerOrderNumber = receipt.brokerOrderNumber();
        brokerOrganizationNumber = receipt.brokerOrganizationNumber(); brokerResponseCode = receipt.responseCode();
        updatedAt = Instant.now();
    }

    private boolean matches(KisOrderReceipt receipt) {
        return receipt != null
                && environment.label().equalsIgnoreCase(receipt.environment())
                && market.name().equalsIgnoreCase(receipt.market())
                && symbol.equalsIgnoreCase(receipt.symbol())
                && side.name().equalsIgnoreCase(receipt.side())
                && receipt.brokerOrderNumber() != null && !receipt.brokerOrderNumber().isBlank()
                && (market != Market.KR || (receipt.brokerOrganizationNumber() != null
                        && !receipt.brokerOrganizationNumber().isBlank()))
                && "0".equals(receipt.responseCode());
    }

    public void markUnknown() { status = KisOrderStatus.UNKNOWN; updatedAt = Instant.now(); }
    boolean conflictsWithTerminalStatus(boolean cancelled, boolean rejected) {
        return (status == KisOrderStatus.CANCELLED && !cancelled)
                || (status == KisOrderStatus.REJECTED && !rejected);
    }
    public void markCancelPending() {
        status = KisOrderStatus.CANCEL_PENDING;
        brokerResponseCode = "0"; // Clear a prior cancel rejection before starting a new attempt.
        updatedAt = Instant.now();
    }
    public void reject(String code) {
        status = KisOrderStatus.REJECTED;
        brokerResponseCode = code;
        if (realExitStatus == KisRealExitStatus.WAITING_FOR_ENTRY
                && (filledQuantity == null || filledQuantity == 0)) realExitStatus = KisRealExitStatus.CLOSED;
        updatedAt = Instant.now();
    }
    public void reconcile(int filled, int remaining, BigDecimal averagePrice, boolean cancelled, boolean rejected) {
        long accountedQuantity = (long) filled + remaining;
        if (filled < 0 || remaining < 0 || filled > quantity || accountedQuantity > quantity
                || (cancelled && rejected)) return;
        if (cancelled) {
            if (remaining != 0 || filled >= quantity) return;
        } else if (rejected) {
            if (filled != 0 || remaining != 0) return;
        } else if (accountedQuantity != quantity) {
            return;
        }
        if ((averagePrice != null && averagePrice.signum() < 0)
                || (filled == 0 && averagePrice != null && averagePrice.signum() > 0)
                || (filled > 0 && (averagePrice == null || averagePrice.signum() <= 0))) return;
        if (filledQuantity != null && filled < filledQuantity) return;
        boolean terminalSnapshotConflict = conflictsWithTerminalStatus(cancelled, rejected);
        boolean cancellationRequested = status == KisOrderStatus.CANCEL_PENDING;
        boolean cancellationRejected = cancellationRequested && brokerResponseCode != null
                && !"0".equals(brokerResponseCode);
        filledQuantity = filled;
        remainingQuantity = remaining;
        averageFillPrice = averagePrice;
        if (exitStopDistancePerShare != null && realExitStatus == KisRealExitStatus.WAITING_FOR_ENTRY) {
            if (filled == 0 && (rejected || cancelled)) realExitStatus = KisRealExitStatus.CLOSED;
            else if (filled > 0 && remaining == 0) realExitStatus = KisRealExitStatus.ARMED;
        }
        if (terminalSnapshotConflict) {
            // Keep observed fills, but do not reopen an order after a terminal broker result.
            // A conflicting snapshot needs another reconciliation before any retry is safe.
            status = KisOrderStatus.UNKNOWN;
            updatedAt = Instant.now();
            return;
        }
        if (rejected && filled == 0) status = KisOrderStatus.REJECTED;
        else if (filled == quantity) status = KisOrderStatus.FILLED;
        else if (cancelled && remaining == 0) status = KisOrderStatus.CANCELLED;
        else if (cancellationRequested && !cancellationRejected && remaining > 0) status = KisOrderStatus.CANCEL_PENDING;
        else if (filled > 0) status = KisOrderStatus.PARTIALLY_FILLED;
        else status = KisOrderStatus.ACCEPTED;
        updatedAt = Instant.now();
    }
    public void recordCancelRejection(String code) {
        if (code != null && code.matches("[A-Za-z0-9_-]{1,32}")) brokerResponseCode = code;
        updatedAt = Instant.now();
    }
    public void recordCancelPreflightBlocked(String code) {
        if (code != null && code.matches("[A-Za-z0-9_-]{1,32}")) brokerResponseCode = code;
        if (remainingQuantity > 0) status = filledQuantity > 0 ? KisOrderStatus.PARTIALLY_FILLED : KisOrderStatus.ACCEPTED;
        updatedAt = Instant.now();
    }
    public UUID getIdempotencyKey() { return idempotencyKey; }
    public String getRequestHash() { return requestHash; }
    public KisEnvironment getEnvironment() { return environment; }
    public Market getMarket() { return market; }
    public String getSymbol() { return symbol; }
    public OrderSide getSide() { return side; }
    public int getQuantity() { return quantity; }
    public BigDecimal getLimitPrice() { return limitPrice; }
    public Currency getCurrency() { return currency; }
    public KisOrderStatus getStatus() { return status; }
    public String getBrokerOrderNumber() { return brokerOrderNumber; }
    public String getBrokerOrganizationNumber() { return brokerOrganizationNumber; }
    public String getBrokerResponseCode() { return brokerResponseCode; }
    public String getExchangeCode() { return exchangeCode; }
    public Integer getFilledQuantity() { return filledQuantity; }
    public Integer getRemainingQuantity() { return remainingQuantity; }
    public BigDecimal getAverageFillPrice() { return averageFillPrice; }
    public BigDecimal getExitStopDistancePerShare() { return exitStopDistancePerShare; }
    public BigDecimal getExitRewardRiskMultiple() { return exitRewardRiskMultiple; }
    public KisRealExitStatus getRealExitStatus() { return realExitStatus; }
    public String getRealExitReason() { return realExitReason; }
    public UUID getRealExitOrderKey() { return realExitOrderKey; }
    public Integer getRealExitQuantity() { return realExitQuantity; }
    public BigDecimal getRealExitLimitPrice() { return realExitLimitPrice; }
    public BigDecimal getExitStopPrice() {
        return averageFillPrice == null || exitStopDistancePerShare == null
                ? null : averageFillPrice.subtract(exitStopDistancePerShare);
    }
    public BigDecimal getExitTargetPrice() {
        return averageFillPrice == null || exitStopDistancePerShare == null || exitRewardRiskMultiple == null
                ? null : averageFillPrice.add(exitStopDistancePerShare.multiply(exitRewardRiskMultiple));
    }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getLastFillAt() { return lastFillAt; }
    void recordFillTime(Instant fillAt) {
        if (fillAt != null && (lastFillAt == null || fillAt.isAfter(lastFillAt))) lastFillAt = fillAt;
    }
}
