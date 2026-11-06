package com.aistocktrader.order;

import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PaperOrderAmendmentRepository extends JpaRepository<PaperOrderAmendment, Long> {
    Optional<PaperOrderAmendment> findByOrderIdAndIdempotencyKey(Long orderId, UUID idempotencyKey);
}
