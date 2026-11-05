package com.aistocktrader.order;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;
import java.util.UUID;
public interface OrderRepository extends JpaRepository<OrderRecord, Long> {
    Optional<OrderRecord> findByIdempotencyKey(UUID idempotencyKey);
}
