package com.aistocktrader.order;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.repository.query.Param;
import java.util.Optional;
import java.util.UUID;
import java.time.Instant;

public interface PaperFillRepository extends JpaRepository<PaperFillRecord, Long> {
    Optional<PaperFillRecord> findByFillKey(UUID fillKey);
    long countByFilledAtAfter(Instant cutoff);

    @Modifying(flushAutomatically = true)
    @Query(value = """
            INSERT INTO paper_fill (order_id, fill_key, quantity, fill_price, transaction_cost,
                                    strategy_stop_price, strategy_target_price, filled_at)
            VALUES (:orderId, :fillKey, :quantity, :fillPrice, :transactionCost,
                    :stopPrice, :targetPrice, :filledAt)
            ON CONFLICT (fill_key) DO NOTHING
            """, nativeQuery = true)
    int insertIfFillKeyAbsent(@Param("orderId") Long orderId, @Param("fillKey") UUID fillKey,
                              @Param("quantity") int quantity, @Param("fillPrice") java.math.BigDecimal fillPrice,
                              @Param("transactionCost") java.math.BigDecimal transactionCost,
                              @Param("stopPrice") java.math.BigDecimal stopPrice,
                              @Param("targetPrice") java.math.BigDecimal targetPrice,
                              @Param("filledAt") Instant filledAt);

    @Query(value = """
            SELECT f.order_id AS \"orderId\", f.filled_at AS \"filledAt\", f.quantity AS quantity,
                   f.fill_price AS \"fillPrice\", f.transaction_cost AS \"transactionCost\",
                   o.market AS market, o.symbol AS symbol, o.currency AS currency, o.side AS side,
                   o.strategy_id AS "strategyId", o.model_id AS "modelId"
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
        String getStrategyId();
        String getModelId();
    }
}
