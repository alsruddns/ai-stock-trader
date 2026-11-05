from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json

import pytest

from app.data_export import build_minute_bar_export, write_minute_bar_export
from app.data_provenance import load_data_provenance


def _row(timestamp: datetime) -> tuple:
    return (timestamp, Decimal("100.1"), Decimal("101.0"), Decimal("99.5"),
            Decimal("100.5"), 1200, Decimal("4.2"), "KIS_REST_MINUTE_CHART")


def test_export_writes_csv_and_unreviewed_hash_bound_sidecar(tmp_path) -> None:
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
    assert sidecar["rights_reviewed"] is False
    assert sidecar["permissions"] == {"local_storage": False, "backtesting": False, "model_training": False}
    with pytest.raises(ValueError, match="explicitly reviewed"):
        load_data_provenance(csv_path, purpose="backtesting")


def test_export_sidecar_can_be_used_after_operator_review_and_hash_match(tmp_path) -> None:
    csv_path = tmp_path / "bars.csv"
    rows = [_row(datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc))]
    write_minute_bar_export(
        csv_path, rows, market="KR", symbol="005930", from_date=date(2026, 11, 5),
        to_date=date(2026, 11, 5), sources=["KIS_REST_MINUTE_CHART"],
    )
    sidecar_path = csv_path.with_name(csv_path.name + ".provenance.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
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
