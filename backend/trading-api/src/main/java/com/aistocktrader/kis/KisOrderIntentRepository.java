package com.aistocktrader.kis;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.UUID;
import java.util.List;
import java.util.Collection;

public interface KisOrderIntentRepository extends JpaRepository<KisOrderIntent, UUID> {
    List<KisOrderIntent> findTop10ByEnvironmentOrderByCreatedAtDesc(KisEnvironment environment);
    List<KisOrderIntent> findTop50ByEnvironmentAndStatusInOrderByUpdatedAtAsc(
            KisEnvironment environment, Collection<KisOrderStatus> statuses);

    @Query("select intent.status, count(intent) from KisOrderIntent intent where intent.environment = :environment group by intent.status")
    List<Object[]> countStatusesByEnvironment(@Param("environment") KisEnvironment environment);
}
