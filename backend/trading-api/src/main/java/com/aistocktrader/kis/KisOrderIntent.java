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
    @Column(name = "created_at", nullable = false)
    private Instant createdAt;
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected KisOrderIntent() { }

    public KisOrderIntent(UUID key, String hash, OrderRequest request, String exchange) {
        this(key, hash, KisEnvironment.PAPER, request, exchange);
    }

    public KisOrderIntent(UUID key, String hash, KisEnvironment environment, OrderRequest request, String exchange) {
        idempotencyKey = key; requestHash = hash; market = request.market();
        this.environment = java.util.Objects.requireNonNull(environment, "environment");
        symbol = request.symbol().trim().toUpperCase(Locale.ROOT); side = request.side();
        quantity = request.quantity(); limitPrice = request.limitPrice(); currency = request.currency();
        exchangeCode = exchange == null ? null : exchange.trim().toUpperCase(Locale.ROOT);
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
                && "0".equals(receipt.responseCode());
    }

    public void markUnknown() { status = KisOrderStatus.UNKNOWN; updatedAt = Instant.now(); }
    public void markCancelPending() { status = KisOrderStatus.CANCEL_PENDING; updatedAt = Instant.now(); }
    public void reject(String code) { status = KisOrderStatus.REJECTED; brokerResponseCode = code; updatedAt = Instant.now(); }
    public void reconcile(int filled, int remaining, BigDecimal averagePrice, boolean cancelled, boolean rejected) {
        if (filled < 0 || remaining < 0 || filled > quantity || filled + remaining > quantity) return;
        boolean cancellationRequested = status == KisOrderStatus.CANCEL_PENDING;
        filledQuantity = filled;
        remainingQuantity = remaining;
        averageFillPrice = averagePrice;
        if (rejected && filled == 0) status = KisOrderStatus.REJECTED;
        else if (filled == quantity) status = KisOrderStatus.FILLED;
        else if (cancelled && remaining == 0) status = KisOrderStatus.CANCELLED;
        else if (cancellationRequested && remaining > 0) status = KisOrderStatus.CANCEL_PENDING;
        else if (filled > 0) status = KisOrderStatus.PARTIALLY_FILLED;
        else status = KisOrderStatus.ACCEPTED;
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
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
