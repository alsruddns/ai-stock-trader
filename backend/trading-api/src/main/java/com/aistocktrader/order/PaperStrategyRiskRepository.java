package com.aistocktrader.order;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;
import java.util.Optional;

public interface PaperStrategyRiskRepository extends JpaRepository<PaperStrategyRisk, Long> {
    Optional<PaperStrategyRisk> findByPositionId(Long positionId);
    List<PaperStrategyRisk> findAllByOrderByUpdatedAtAsc();
    void deleteByPositionId(Long positionId);
}
