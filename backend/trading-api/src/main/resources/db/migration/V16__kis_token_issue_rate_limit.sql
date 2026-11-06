CREATE TABLE kis_token_issue_rate_limit_slot (
    environment VARCHAR(8) NOT NULL CHECK (environment IN ('PAPER', 'REAL')),
    app_key_sha256 CHAR(64) NOT NULL CHECK (app_key_sha256 ~ '^[0-9a-f]{64}$'),
    next_request_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_kis_token_issue_rate_limit_slot PRIMARY KEY (environment, app_key_sha256)
);
