from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from app.data_export import (
    build_minute_bar_export,
    summarize_session_coverage,
    validate_websocket_session_rows,
    write_minute_bar_export,
)
from app.data_provenance import load_data_provenance


def _row(timestamp: datetime, source: str = "KIS_REST_MINUTE_CHART") -> tuple:
    return (timestamp, Decimal("100.1"), Decimal("101.0"), Decimal("99.5"),
            Decimal("100.5"), 1200, Decimal("4.2"), source)


@pytest.mark.parametrize(
    ("market", "opened", "expected_bars"),
    [
        ("KR", datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc), 380),
        ("US", datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc), 390),
    ],
)
def test_session_coverage_reports_complete_bars_and_spreads(market, opened, expected_bars) -> None:
    rows = [_row(opened + timedelta(minutes=index)) for index in range(expected_bars)]

    summary = summarize_session_coverage(rows, market=market)

    assert summary == [{
        "session_date": "2026-11-05",
        "bars": expected_bars,
        "expected_bars": expected_bars,
        "complete": True,
        "bars_with_spread": expected_bars,
        "spread_complete": True,
        "sources": ["KIS_REST_MINUTE_CHART"],
        "source_counts": {"KIS_REST_MINUTE_CHART": expected_bars},
    }]


def test_session_coverage_marks_gaps_duplicates_and_missing_spreads() -> None:
    opened = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)
    rows = [_row(opened + timedelta(minutes=index)) for index in range(390)]
    rows[42] = (*rows[42][:6], None, rows[42][7])
    rows[43] = _row(opened + timedelta(minutes=42))

    summary = summarize_session_coverage(rows, market="US")[0]

    assert summary["complete"] is False
    assert summary["bars"] == 390
    assert summary["bars_with_spread"] == 389
    assert summary["spread_complete"] is False


def test_kr_session_coverage_reports_domestic_websocket_and_rest_source_counts() -> None:
    opened = datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(380):
        source = "KIS_PAPER_WEBSOCKET" if index < 52 else "KIS_REST_MINUTE_SESSION"
        row = _row(opened + timedelta(minutes=index), source)
        rows.append(row if index < 52 else (*row[:6], None, row[7]))

    summary = summarize_session_coverage(rows, market="KR")[0]

    assert summary["source_counts"] == {
        "KIS_PAPER_WEBSOCKET": 52,
        "KIS_REST_MINUTE_SESSION": 328,
    }
    assert summary["bars_with_spread"] == 52
    assert summary["spread_complete"] is False


def test_session_coverage_rejects_non_session_rows() -> None:
    with pytest.raises(ValueError, match="inside a verified continuous session"):
        summarize_session_coverage(
            [_row(datetime(2026, 11, 5, 13, 30, tzinfo=timezone.utc))], market="US"
        )


def test_kis_export_applies_confirmed_personal_rights_to_hash_bound_sidecar(tmp_path) -> None:
    csv_path = tmp_path / "bars.csv"
    rows = [_row(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)),
            _row(datetime(2026, 11, 5, 9, 1, tzinfo=timezone.utc))]

    sidecar = write_minute_bar_export(
        csv_path, rows, market="KR", symbol="005930", from_date=date(2026, 11, 5),
        to_date=date(2026, 11, 5), sources=["KIS_REST_MINUTE_CHART"],
    )

    csv_bytes = csv_path.read_bytes()
    assert b"005930,2026-11-05T09:00:00+00:00" in csv_bytes
    assert sidecar["dataset_sha256"] == hashlib.sha256(csv_bytes).hexdigest()
    assert sidecar["provider"] == "KIS Open API"
    assert sidecar["coverage"]["sessions"][0]["source_counts"] == {"KIS_REST_MINUTE_CHART": 2}
    assert sidecar["rights_reviewed"] is True
    assert sidecar["permissions"] == {"local_storage": True, "backtesting": True, "model_training": True}
    assert "no public redistribution" in sidecar["license_or_terms"]
    assert load_data_provenance(csv_path, purpose="backtesting")["dataset_sha256"] == sidecar["dataset_sha256"]
    assert load_data_provenance(csv_path, purpose="model_training")["dataset_sha256"] == sidecar["dataset_sha256"]


def test_export_manifest_reports_observed_sessions_separately_from_requested_date_range(tmp_path) -> None:
    csv_path = tmp_path / "bars.csv"
    opened = datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc)
    rows = [_row(opened + timedelta(minutes=index), "KIS_REAL_US_WEBSOCKET") for index in range(390)]

    sidecar = write_minute_bar_export(
        csv_path, rows, market="US", symbol="AAPL", from_date=date(2026, 11, 2),
        to_date=date(2026, 11, 6), sources=["KIS_REAL_US_WEBSOCKET"],
    )

    assert sidecar["coverage"] == {
        "requested_from_date": "2026-11-02",
        "requested_to_date": "2026-11-06",
        "observed_session_count": 1,
        "observed_first_session": "2026-11-06",
        "observed_last_session": "2026-11-06",
        "observed_bar_count": 390,
        "expected_bar_count": 390,
        "complete_session_count": 1,
        "spread_complete_session_count": 1,
        "sessions": [{
            "session_date": "2026-11-06",
            "bars": 390,
            "expected_bars": 390,
            "complete": True,
            "bars_with_spread": 390,
            "spread_complete": True,
            "sources": ["KIS_REAL_US_WEBSOCKET"],
            "source_counts": {"KIS_REAL_US_WEBSOCKET": 390},
        }],
    }


def test_non_kis_export_remains_unreviewed_until_operator_review(tmp_path) -> None:
    csv_path = tmp_path / "bars.csv"
    rows = [_row(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc), "OTHER_VENDOR")]
    sidecar = write_minute_bar_export(
        csv_path, rows, market="KR", symbol="005930", from_date=date(2026, 11, 5),
        to_date=date(2026, 11, 5), sources=["OTHER_VENDOR"],
    )
    assert sidecar["coverage"]["observed_session_count"] == 1
    assert sidecar["coverage"]["complete_session_count"] == 0
    assert sidecar["coverage"]["spread_complete_session_count"] == 0
    sidecar_path = csv_path.with_name(csv_path.name + ".provenance.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["rights_reviewed"] is False
    assert sidecar["permissions"] == {"local_storage": False, "backtesting": False, "model_training": False}
    with pytest.raises(ValueError, match="explicitly reviewed"):
        load_data_provenance(csv_path, purpose="backtesting")

    sidecar.update({
        "license_or_terms": "synthetic test fixture terms",
        "terms_reference": "unit test only",
        "verified_by": "test operator",
        "verified_at": "2026-11-06T10:00:00+09:00",
        "rights_reviewed": True,
        "permissions": {"local_storage": True, "backtesting": True, "model_training": True},
    })
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    provenance = load_data_provenance(csv_path, purpose="backtesting")

    assert provenance["rights_reviewed"] is True
    assert provenance["dataset_sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()


def test_mixed_kis_and_other_vendor_export_remains_unreviewed(tmp_path) -> None:
    csv_path = tmp_path / "mixed.csv"
    rows = [
        _row(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc), "KIS_REST_MINUTE_CHART"),
        _row(datetime(2026, 11, 5, 9, 1, tzinfo=timezone.utc), "OTHER_VENDOR"),
    ]

    sidecar = write_minute_bar_export(
        csv_path, rows, market="KR", symbol="005930", from_date=date(2026, 11, 5),
        to_date=date(2026, 11, 5), sources=["KIS_REST_MINUTE_CHART", "OTHER_VENDOR"],
    )

    assert sidecar["rights_reviewed"] is False
    assert sidecar["permissions"] == {"local_storage": False, "backtesting": False, "model_training": False}
    with pytest.raises(ValueError, match="explicitly reviewed"):
        load_data_provenance(csv_path, purpose="model_training")


def test_export_rejects_source_summary_that_does_not_match_exported_rows() -> None:
    with pytest.raises(ValueError, match="must match the sources"):
        build_minute_bar_export(
            [_row(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc))], market="KR", symbol="005930",
            from_date=date(2026, 11, 5), to_date=date(2026, 11, 5), sources=["OTHER_VENDOR"],
        )


def test_export_requires_timezone_and_source_attribution() -> None:
    with pytest.raises(ValueError, match="timestamps must include a timezone"):
        build_minute_bar_export(
            [_row(datetime(2026, 11, 5, 9, 0))], market="KR", symbol="005930",
            from_date=date(2026, 11, 5), to_date=date(2026, 11, 5), sources=["KIS_REST_MINUTE_CHART"],
        )
    with pytest.raises(ValueError, match="source information is required"):
        build_minute_bar_export(
            [_row(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc))], market="KR", symbol="005930",
            from_date=date(2026, 11, 5), to_date=date(2026, 11, 5), sources=[],
        )


def test_training_export_source_gate_requires_a_complete_spread_populated_kis_session() -> None:
    opened = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)
    rows = [_row(opened + timedelta(minutes=index), "KIS_PAPER_WEBSOCKET") for index in range(380)]

    validate_websocket_session_rows(rows, market="KR", expected_source="KIS_PAPER_WEBSOCKET")

    with pytest.raises(ValueError, match="every expected minute exactly once"):
        validate_websocket_session_rows(rows[:-1], market="KR", expected_source="KIS_PAPER_WEBSOCKET")

    mixed = rows.copy()
    mixed[20] = _row(opened + timedelta(minutes=20), "KIS_REST_MINUTE_SESSION")
    with pytest.raises(ValueError, match="unexpected market-data source"):
        validate_websocket_session_rows(mixed, market="KR", expected_source="KIS_PAPER_WEBSOCKET")


def test_training_export_source_gate_rejects_missing_spread_and_market_source_mismatch() -> None:
    opened = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)
    rows = [_row(opened + timedelta(minutes=index), "KIS_PAPER_WEBSOCKET") for index in range(380)]
    missing_spread = list(rows)
    missing_spread[10] = (*missing_spread[10][:6], None, missing_spread[10][7])

    with pytest.raises(ValueError, match="spread data"):
        validate_websocket_session_rows(missing_spread, market="KR", expected_source="KIS_PAPER_WEBSOCKET")
    with pytest.raises(ValueError, match="do not match"):
        validate_websocket_session_rows(rows, market="US", expected_source="KIS_PAPER_WEBSOCKET")


def test_training_export_source_gate_accepts_complete_new_york_session() -> None:
    opened = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)
    rows = [_row(opened + timedelta(minutes=index), "KIS_REAL_US_WEBSOCKET") for index in range(390)]

    validate_websocket_session_rows(rows, market="US", expected_source="KIS_REAL_US_WEBSOCKET")
