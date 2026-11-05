package com.aistocktrader.order;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;
import java.util.Optional;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
public interface OrderRepository extends JpaRepository<OrderRecord, Long> {
    Optional<OrderRecord> findByIdempotencyKey(UUID idempotencyKey);
    @Modifying
    @Transactional
    @Query(value = """
            INSERT INTO trade_order (market, symbol, side, quantity, filled_quantity, limit_price, currency,
                                     status, idempotency_key, created_at, updated_at)
            VALUES (:market, :symbol, :side, :quantity, 0, :limitPrice, :currency,
                    'PENDING', :idempotencyKey, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (idempotency_key) DO NOTHING
            """, nativeQuery = true)
    int insertIfIdempotencyKeyAbsent(@Param("market") String market, @Param("symbol") String symbol,
                                     @Param("side") String side, @Param("quantity") int quantity,
                                     @Param("limitPrice") java.math.BigDecimal limitPrice,
                                     @Param("currency") String currency,
                                     @Param("idempotencyKey") UUID idempotencyKey);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select o from OrderRecord o where o.status in :statuses and o.updatedAt < :cutoff")
    List<OrderRecord> findOpenOrdersCreatedBefore(@Param("statuses") List<OrderStatus> statuses,
                                                  @Param("cutoff") Instant cutoff);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select o from OrderRecord o where o.id = :id")
    Optional<OrderRecord> findByIdForUpdate(@Param("id") Long id);
}
