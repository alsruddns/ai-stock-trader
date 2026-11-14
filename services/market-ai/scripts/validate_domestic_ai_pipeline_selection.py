"""Fail closed if the domestic AI pipeline suite starts covering US cases."""

from __future__ import annotations

import ast
import re
from pathlib import Path


TEST_FILE = Path(__file__).resolve().parents[1] / "tests" / "test_ai_pipeline.py"
API_TEST_FILE = Path(__file__).resolve().parents[1] / "tests" / "test_api.py"
EXCLUDED_TESTS = {
    "test_us_training_and_inference_accept_one_new_york_session_crossing_korea_midnight",
    "test_us_session_progress_uses_new_york_exchange_session",
    "test_model_prediction_rejects_market_scope_mismatch",
    "test_us_model_governance_is_scoped_to_us_symbol",
    "test_us_active_model_registry_selection_is_independent",
}
FOREIGN_MARKERS = re.compile(
    r"(?<![A-Za-z0-9_])US(?![A-Za-z0-9_])|\bAAPL\b|\bNASDAQ\b|\bNYSE\b|"
    r"\bNASD\b|America/New_York|HDFSCNT0|overseas",
    re.IGNORECASE,
)
DOMESTIC_API_TESTS = {
    "test_training_readiness_counts_only_complete_approved_websocket_sessions",
    "test_training_readiness_includes_exchange_sessions_with_no_stored_bars",
    "test_kr_training_readiness_reports_incomplete_session_and_spread_reasons",
}


def _case_count(test: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    count = 1
    for decorator in test.decorator_list:
        if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "parametrize"):
            if len(decorator.args) < 2 or not isinstance(decorator.args[1], (ast.List, ast.Tuple)):
                raise SystemExit(f"Cannot count parameterized domestic AI test cases: {test.name}.")
            count *= len(decorator.args[1].elts)
    return count


def main() -> int:
    source = TEST_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TEST_FILE))
    tests = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name.startswith("test_")]
    names = {test.name for test in tests}
    if not EXCLUDED_TESTS.issubset(names):
        raise SystemExit("The domestic AI test exclusions no longer match the test file.")

    marked_foreign = set()
    for test in tests:
        snippet = ast.get_source_segment(source, test) or ""
        if FOREIGN_MARKERS.search(snippet):
            marked_foreign.add(test.name)
    if marked_foreign != EXCLUDED_TESTS:
        raise SystemExit(
            "Domestic AI test exclusions need review; foreign-marked tests are: "
            + ", ".join(sorted(marked_foreign))
        )

    selected_count = sum(_case_count(test) for test in tests if test.name not in EXCLUDED_TESTS)
    if selected_count != 82:
        raise SystemExit(f"Expected 82 domestic AI pipeline tests, found {selected_count}.")
    api_source = API_TEST_FILE.read_text(encoding="utf-8")
    api_tree = ast.parse(api_source, filename=str(API_TEST_FILE))
    api_tests = [node for node in api_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name in DOMESTIC_API_TESTS]
    if {test.name for test in api_tests} != DOMESTIC_API_TESTS:
        raise SystemExit("The domestic training-readiness API test selection no longer matches the test file.")
    api_foreign = [test.name for test in api_tests
                   if FOREIGN_MARKERS.search(ast.get_source_segment(api_source, test) or "")]
    if api_foreign:
        raise SystemExit("Domestic training-readiness API tests contain foreign-market markers: "
                         + ", ".join(sorted(api_foreign)))
    print(f"Domestic AI selection verified: {selected_count} tests; 5 US-specific tests excluded.")
    print(f"Domestic training-readiness API selection verified: {len(api_tests)} tests; no foreign-market markers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
