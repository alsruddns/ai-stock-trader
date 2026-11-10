ALTER TABLE market_minute_bar
    ADD COLUMN spread_observed_at TIMESTAMPTZ;

ALTER TABLE market_minute_bar
    ADD CONSTRAINT ck_market_minute_bar_spread_observation_time CHECK (
        spread_observed_at IS NULL OR (
            spread_bps IS NOT NULL
            AND spread_observed_at >= bar_at
            AND spread_observed_at < bar_at + INTERVAL '1 minute'
        )
    );
