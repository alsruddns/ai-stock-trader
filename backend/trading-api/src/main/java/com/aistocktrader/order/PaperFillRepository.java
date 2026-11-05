package com.aistocktrader.order;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import java.util.Optional;
import java.util.UUID;
import java.time.Instant;

public interface PaperFillRepository extends JpaRepository<PaperFillRecord, Long> {
    Optional<PaperFillRecord> findByFillKey(UUID fillKey);
    long countByFilledAtAfter(Instant cutoff);

    @Query(value = """
            SELECT f.order_id AS \"orderId\", f.filled_at AS \"filledAt\", f.quantity AS quantity,
                   f.fill_price AS \"fillPrice\", f.transaction_cost AS \"transactionCost\",
                   o.market AS market, o.symbol AS symbol, o.currency AS currency, o.side AS side
              FROM paper_fill f JOIN trade_order o ON o.id = f.order_id
             ORDER BY f.filled_at ASC, f.id ASC
            """, nativeQuery = true)
    java.util.List<FillTradeView> findAllWithTradeDetails();

    interface FillTradeView {
        Long getOrderId();
        Instant getFilledAt();
        int getQuantity();
        java.math.BigDecimal getFillPrice();
        java.math.BigDecimal getTransactionCost();
        String getMarket();
        String getSymbol();
        String getCurrency();
        String getSide();
    }
}
