package com.aistocktrader.kis;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.UUID;

public interface KisOrderIntentRepository extends JpaRepository<KisOrderIntent, UUID> { }
