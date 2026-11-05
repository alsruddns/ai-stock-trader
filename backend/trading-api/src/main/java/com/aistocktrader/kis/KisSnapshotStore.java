package com.aistocktrader.kis;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.Map;

@Repository
public class KisSnapshotStore {
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public KisSnapshotStore(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    public void save(KisAccountSnapshot snapshot) {
        try {
            String summary = mapper.writeValueAsString(Map.of(
                    "cashBalance", snapshot.cashBalance() == null ? "" : snapshot.cashBalance(),
                    "orderableCash", snapshot.orderableCash() == null ? "" : snapshot.orderableCash()));
            String positions = mapper.writeValueAsString(snapshot.positions());
            jdbc.update("""
                    INSERT INTO account_balance_snapshot
                      (environment, market, currency, summary, positions, retrieved_at, source)
                    VALUES (?, ?, ?, ?::jsonb, ?::jsonb, ?, ?)
                    """, snapshot.environment().label(), snapshot.market().name(), snapshot.currency(),
                    summary, positions, snapshot.retrievedAt(), snapshot.source());
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("Account snapshot could not be serialized.");
        }
    }
}
