package com.aistocktrader.order;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;
import java.util.UUID;

public interface PaperFillRepository extends JpaRepository<PaperFillRecord, Long> {
    Optional<PaperFillRecord> findByFillKey(UUID fillKey);
}
