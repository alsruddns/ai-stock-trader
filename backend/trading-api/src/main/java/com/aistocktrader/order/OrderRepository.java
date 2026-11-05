package com.aistocktrader.order;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.Optional;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
public interface OrderRepository extends JpaRepository<OrderRecord, Long> {
    Optional<OrderRecord> findByIdempotencyKey(UUID idempotencyKey);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select o from OrderRecord o where o.status in :statuses and o.updatedAt < :cutoff")
    List<OrderRecord> findOpenOrdersCreatedBefore(@Param("statuses") List<OrderStatus> statuses,
                                                  @Param("cutoff") Instant cutoff);
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select o from OrderRecord o where o.id = :id")
    Optional<OrderRecord> findByIdForUpdate(@Param("id") Long id);
}
