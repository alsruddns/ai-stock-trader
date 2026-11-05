package com.aistocktrader.kis;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.UUID;
import java.util.List;

public interface KisOrderIntentRepository extends JpaRepository<KisOrderIntent, UUID> {
    List<KisOrderIntent> findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment environment);

    @Query("select intent.status, count(intent) from KisOrderIntent intent where intent.environment = :environment group by intent.status")
    List<Object[]> countStatusesByEnvironment(@Param("environment") KisEnvironment environment);
}
