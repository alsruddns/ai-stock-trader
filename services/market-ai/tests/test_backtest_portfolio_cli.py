import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
CLI = REPOSITORY / "scripts" / "backtest-portfolio.py"


def _dataset(path: Path, session_date: str = "2026-11-05", symbol: str | None = None) -> Path:
    symbol = symbol or path.stem
    start = datetime.fromisoformat(f"{session_date}T09:00:00+09:00")
    lines = ["timestamp,open,high,low,close,volume,spread_bps,spread_observed_at,symbol"]
    for index in range(380):
        stamp = start + timedelta(minutes=index)
        observed = stamp + timedelta(seconds=30)
        lines.append(f"{stamp.isoformat()},100,101,99,100,1000,5,{observed.isoformat()},{symbol}")
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


def _command(*datasets: Path | tuple[str, Path]) -> list[str]:
    command = [sys.executable, str(CLI), "--starting-equity", "1000000", "--max-order-notional", "1000000",
               "--buy-commission-bps", "5", "--sell-commission-bps", "5", "--sell-tax-bps", "0",
               "--slippage-bps", "5", "--minimum-turnover", "0"]
    for path in datasets:
        symbol, csv_path = path if isinstance(path, tuple) else (path.stem, path)
        command.extend(["--dataset", f"{symbol}={csv_path}"])
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
    assert payload["summary"]["max_bar_marked_drawdown_amount"] is None
    assert payload["summary"]["max_bar_marked_drawdown_percent"] is None
    assert payload["summary"]["portfolio_return_percent"] is None
    assert payload["summary"]["portfolio_return_basis"] is None
    assert payload["summary"]["portfolio_return_note"] == (
        "Independent sessions do not produce one portfolio return percentage."
    )
    assert payload["summary"]["executable"] is False
    assert payload["executable"] is False


def test_portfolio_cli_reports_optional_slippage_scenarios_without_changing_base_result(tmp_path: Path) -> None:
    first = _dataset(tmp_path / "005930.csv")
    second = _dataset(tmp_path / "000660.csv")
    base = subprocess.run(_command(first, second), cwd=REPOSITORY, capture_output=True, text=True, check=False)
    scenario_command = _command(first, second)
    for value in ("0", "5", "10", "20"):
        scenario_command.extend(["--slippage-scenario-bps", value])
    scenarios = subprocess.run(scenario_command, cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert base.returncode == 0, base.stderr
    assert scenarios.returncode == 0, scenarios.stderr
    base_payload = json.loads(base.stdout)
    scenario_payload = json.loads(scenarios.stdout)
    assert scenario_payload["summary"] == base_payload["summary"]
    assert scenario_payload["cost_assumptions_bps_per_fill"] == base_payload["cost_assumptions_bps_per_fill"]
    sensitivity = scenario_payload["slippage_sensitivity"]
    assert [item["slippage_bps_per_fill"] for item in sensitivity] == ["0", "5", "10", "20"]
    assert all(item["session_count"] == 1 and item["accepted_trade_count"] == 0 for item in sensitivity)
    assert all(item["net_pnl"] == "0" and item["executable"] is False for item in sensitivity)
    assert all(item["drawdown_basis"] == "maximum_independent_session" for item in sensitivity)
    assert scenario_payload["executable"] is False


def test_portfolio_cli_reports_capital_scenarios_without_changing_base_result(tmp_path: Path) -> None:
    first = _dataset(tmp_path / "005930.csv")
    second = _dataset(tmp_path / "000660.csv")
    base = subprocess.run(_command(first, second), cwd=REPOSITORY, capture_output=True, text=True, check=False)
    scenario_command = _command(first, second)
    for value in ("5000000", "10000000", "20000000"):
        scenario_command.extend(["--capital-scenario", value])
    scenarios = subprocess.run(scenario_command, cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert base.returncode == 0, base.stderr
    assert scenarios.returncode == 0, scenarios.stderr
    base_payload = json.loads(base.stdout)
    scenario_payload = json.loads(scenarios.stdout)
    assert scenario_payload["summary"] == base_payload["summary"]
    assert scenario_payload["sessions"] == base_payload["sessions"]
    sensitivity = scenario_payload["capital_sensitivity"]
    assert [item["starting_equity"] for item in sensitivity] == ["5000000", "10000000", "20000000"]
    assert [item["max_order_notional"] for item in sensitivity] == ["5000000", "10000000", "20000000"]
    assert all(item["session_count"] == 1 and item["accepted_trade_count"] == 0 for item in sensitivity)
    assert all(item["net_pnl"] == "0" and item["return_percent"] is None for item in sensitivity)
    assert all(item["return_basis"] is None for item in sensitivity)
    assert all(item["rejected_entries_by_symbol_and_reason"] == {} for item in sensitivity)
    assert all(item["executable"] is False for item in sensitivity)
    assert scenario_payload["executable"] is False


def test_portfolio_cli_rejects_duplicate_capital_scenarios(tmp_path: Path) -> None:
    first = _dataset(tmp_path / "005930.csv")
    second = _dataset(tmp_path / "000660.csv")
    command = _command(first, second) + ["--capital-scenario", "1000000", "--capital-scenario", "1000000"]

    completed = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert "capital scenarios must be unique positive finite KRW values" in completed.stderr


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
    command = _command(first, second) + ["--carry-equity-between-sessions",
                                         "--capital-scenario", "1000000", "--capital-scenario", "2000000"]

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
    expected_return = (Decimal(payload["summary"]["ending_equity"])
                       - Decimal(payload["summary"]["starting_equity"])) * Decimal(100) / Decimal(
                           payload["summary"]["starting_equity"]
                       )
    assert Decimal(payload["summary"]["portfolio_return_percent"]) == expected_return
    assert "compounded sequential ending equity" in payload["summary"]["portfolio_return_basis"]
    assert payload["summary"]["portfolio_return_note"] is None
    assert Decimal(payload["summary"]["starting_equity"]) + Decimal(payload["summary"]["net_pnl"]) == Decimal(
        payload["summary"]["ending_equity"]
    )
    assert payload["summary"]["max_bar_marked_drawdown_amount"] == "0"
    assert payload["summary"]["max_bar_marked_drawdown_percent"] == "0"
    assert payload["summary"]["bar_marked_equity_observations"] == 2280
    assert payload["summary"]["trade_metrics"]["portfolio"]["profit_factor_status"] == "no_trades"
    assert payload["summary"]["trade_metrics"]["portfolio"]["mean_net_return_bps"] is None
    assert payload["summary"]["trade_metrics"]["by_symbol"]["005930"]["trade_count"] == 0
    assert all(session["trade_metrics"]["profit_factor_status"] == "no_trades"
               for session in payload["sessions"])
    sensitivity = payload["capital_sensitivity"]
    assert [item["starting_equity"] for item in sensitivity] == ["1000000", "2000000"]
    assert [item["max_order_notional"] for item in sensitivity] == ["1000000", "2000000"]
    assert all(item["ending_equity"] == item["starting_equity"] for item in sensitivity)
    assert all(item["return_percent"] == "0" for item in sensitivity)
    assert all(item["return_basis"] == "compounded_sequential_sessions" for item in sensitivity)
    assert payload["executable"] is False


def test_portfolio_cli_preserves_provenance_for_multiple_source_files_per_symbol(tmp_path: Path) -> None:
    first_005930 = _dataset(tmp_path / "005930-20261105.csv", "2026-11-05", "005930")
    next_005930 = _dataset(tmp_path / "005930-20261106.csv", "2026-11-06", "005930")
    first_000660 = _dataset(tmp_path / "000660-20261105.csv", "2026-11-05", "000660")
    next_000660 = _dataset(tmp_path / "000660-20261106.csv", "2026-11-06", "000660")
    command = _command(("005930", first_005930), ("005930", next_005930),
                       ("000660", first_000660), ("000660", next_000660))
    command.append("--carry-equity-between-sessions")

    completed = subprocess.run(command, cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert len(payload["sessions"]) == 2
    assert {item["date"] for item in payload["sessions"]} == {"2026-11-05", "2026-11-06"}
    for symbol in ("005930", "000660"):
        dataset = payload["datasets"][symbol]
        assert dataset["dataset_sha256"] is None
        assert dataset["data_provenance"] is None
        assert dataset["data_rights_verified"] is True
        assert dataset["session_count"] == 2
        assert dataset["bar_count"] == 760
        assert len(dataset["source_files"]) == 2
        assert all(source["data_rights_verified"] is True for source in dataset["source_files"])
        assert all(len(source["dataset_sha256"]) == 64 for source in dataset["source_files"])


def test_portfolio_cli_rejects_duplicate_session_across_symbol_source_files(tmp_path: Path) -> None:
    first_005930 = _dataset(tmp_path / "005930-a.csv", "2026-11-05", "005930")
    duplicate_005930 = _dataset(tmp_path / "005930-b.csv", "2026-11-05", "005930")
    first_000660 = _dataset(tmp_path / "000660.csv", "2026-11-05", "000660")

    completed = subprocess.run(_command(("005930", first_005930), ("005930", duplicate_005930),
                                        ("000660", first_000660)), cwd=REPOSITORY,
                               capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert "duplicate KR session dates" in completed.stderr


def test_portfolio_cli_rejects_symbol_session_mismatch(tmp_path: Path) -> None:
    first = _dataset(tmp_path / "005930.csv", "2026-11-05")
    second = _dataset(tmp_path / "000660.csv", "2026-11-06")

    completed = subprocess.run(_command(first, second), cwd=REPOSITORY, capture_output=True, text=True, check=False)

    assert completed.returncode == 2
    assert "same non-empty set of KR session dates" in completed.stderr
