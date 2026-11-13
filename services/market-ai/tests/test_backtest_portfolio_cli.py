import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
CLI = REPOSITORY / "scripts" / "backtest-portfolio.py"


def _dataset(path: Path, session_date: str = "2026-11-05") -> Path:
    start = datetime.fromisoformat(f"{session_date}T09:00:00+09:00")
    lines = ["timestamp,open,high,low,close,volume,spread_bps,spread_observed_at,symbol"]
    for index in range(380):
        stamp = start + timedelta(minutes=index)
        observed = stamp + timedelta(seconds=30)
        lines.append(f"{stamp.isoformat()},100,101,99,100,1000,5,{observed.isoformat()},{path.stem}")
    content = ("\n".join(lines) + "\n").encode()
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    sidecar = {
        "schema_version": 1,
        "provider": "test-provider",
        "dataset_name": path.stem,
        "source_reference": "test fixture",
        "license_or_terms": "test rights",
        "terms_reference": "test reference",
        "retrieved_at": "2026-11-05T16:00:00+09:00",
        "verified_by": "test",
        "verified_at": "2026-11-05T16:00:00+09:00",
        "rights_reviewed": True,
        "permissions": {"local_storage": True, "backtesting": True, "model_training": True},
        "dataset_sha256": digest,
    }
    path.with_name(path.name + ".provenance.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return path


def _multi_session_dataset(path: Path) -> Path:
    symbol = path.stem
    first = _dataset(path, "2026-11-05")
    first_content = first.read_text(encoding="utf-8").splitlines()
    second = _dataset(path.with_name(path.stem + "-second.csv"), "2026-11-06")
    second_content = [f"{row.rsplit(',', 1)[0]},{symbol}"
                      for row in second.read_text(encoding="utf-8").splitlines()[1:]]
    combined = ("\n".join(first_content + second_content) + "\n").encode()
    first.write_bytes(combined)
    digest = hashlib.sha256(combined).hexdigest()
    sidecar_path = first.with_name(first.name + ".provenance.json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["dataset_sha256"] = digest
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    second.unlink()
    second.with_name(second.name + ".provenance.json").unlink()
    return first


def _command(*datasets: Path) -> list[str]:
    command = [sys.executable, str(CLI), "--starting-equity", "1000000", "--max-order-notional", "1000000",
               "--buy-commission-bps", "5", "--sell-commission-bps", "5", "--sell-tax-bps", "0",
               "--slippage-bps", "5", "--minimum-turnover", "0"]
    for path in datasets:
        command.extend(["--dataset", f"{path.stem}={path}"])
    return command


def test_portfolio_cli_requires_reviewed_multi_symbol_inputs_and_reports_non_executable(tmp_path: Path) -> None:
    first = _dataset(tmp_path / "005930.csv")
    second = _dataset(tmp_path / "000660.csv")

    completed = subprocess.run(_command(first, second), cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["market"] == "KR"
    assert payload["ai"]["enabled"] is False
    assert set(payload["datasets"]) == {"005930", "000660"}
    assert payload["sessions"][0]["input_bars_by_symbol"] == {"000660": 380, "005930": 380}
    session = payload["sessions"][0]
    assert "adverse low" in session["bar_marked_equity_basis"]
    cost_components = session["estimated_costs_by_component"]
    assert set(cost_components) == {
        "entry_slippage", "entry_spread", "exit_slippage", "exit_spread",
        "buy_commission_and_exchange_fee", "sell_commission_and_exchange_fee", "sell_tax",
    }
    assert sum((Decimal(value) for value in cost_components.values()), Decimal(0)) == Decimal(
        session["estimated_costs"]
    )
    assert Decimal(session["gross_pnl"]) - Decimal(session["estimated_costs"]) == Decimal(session["net_pnl"])
    summary_components = payload["summary"]["estimated_costs_by_component_across_independent_sessions"]
    assert set(summary_components) == set(cost_components)
    assert sum((Decimal(value) for value in summary_components.values()), Decimal(0)) == Decimal(
        payload["summary"]["estimated_costs_across_independent_sessions"]
    )
    assert Decimal(payload["summary"]["gross_pnl_across_independent_sessions"]) - Decimal(
        payload["summary"]["estimated_costs_across_independent_sessions"]
    ) == Decimal(payload["summary"]["net_pnl_across_independent_sessions"])
    assert payload["summary"]["executable"] is False
    assert payload["executable"] is False


def test_portfolio_cli_fails_closed_when_active_ai_models_are_required_but_missing(tmp_path: Path) -> None:
    first = _dataset(tmp_path / "005930.csv")
    second = _dataset(tmp_path / "000660.csv")
    command = _command(first, second) + ["--with-active-ai-models", "--registry", str(tmp_path / "registry")]

    completed = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert "no approved active KR AI model is available" in completed.stderr


def test_portfolio_cli_can_carry_completed_equity_forward_between_sessions(tmp_path: Path) -> None:
    first = _multi_session_dataset(tmp_path / "005930.csv")
    second = _multi_session_dataset(tmp_path / "000660.csv")
    command = _command(first, second) + ["--carry-equity-between-sessions"]

    completed = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"]["equity_mode"] == "compounded_sequential_sessions"
    assert "net_pnl_across_independent_sessions" not in payload["summary"]
    assert len(payload["sessions"]) == 2
    assert payload["sessions"][0]["starting_equity"] == "1000000"
    assert payload["sessions"][1]["starting_equity"] == payload["sessions"][0]["ending_equity"]
    assert payload["sessions"][0]["starting_consecutive_losses"] == 0
    assert payload["sessions"][1]["starting_consecutive_losses"] == payload["sessions"][0]["ending_consecutive_losses"]
    assert payload["summary"]["ending_equity"] == payload["sessions"][1]["ending_equity"]
    assert Decimal(payload["summary"]["starting_equity"]) + Decimal(payload["summary"]["net_pnl"]) == Decimal(
        payload["summary"]["ending_equity"]
    )
    assert payload["executable"] is False


def test_portfolio_cli_rejects_symbol_session_mismatch(tmp_path: Path) -> None:
    first = _dataset(tmp_path / "005930.csv", "2026-11-05")
    second = _dataset(tmp_path / "000660.csv", "2026-11-06")

    completed = subprocess.run(_command(first, second), cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert "same non-empty set of KR session dates" in completed.stderr
