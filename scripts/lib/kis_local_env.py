from __future__ import annotations

import os
from pathlib import Path


def load_local_environment(repository: Path) -> Path:
    """Load the ignored local env file as the source of truth for this process."""
    for name in (".env_local", ".env"):
        path = repository / name
        if not path.is_file():
            continue
        for key in tuple(os.environ):
            if (key.startswith(("KIS_", "POSTGRES_")) or key in {
                "TRADING_MODE", "LIVE_TRADING_ENABLED", "PAPER_AUTOTRADING_ENABLED",
                "REAL_SIGNAL_MONITOR_ENABLED", "REAL_SIGNAL_SUBMISSION_ENABLED",
                "REAL_PROTECTIVE_EXIT_MANAGER_ENABLED",
            }):
                os.environ.pop(key, None)
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            trimmed = line.strip()
            if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
                continue
            key, value = trimmed.split("=", 1)
            key, value = key.strip(), value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ[key] = value
        return path
    raise FileNotFoundError("ignored .env_local or .env file is missing")
