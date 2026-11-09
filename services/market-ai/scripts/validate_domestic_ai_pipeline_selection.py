"""Fail closed if the domestic AI pipeline suite starts covering US cases."""

from __future__ import annotations

import ast
import re
from pathlib import Path


TEST_FILE = Path(__file__).resolve().parents[1] / "tests" / "test_ai_pipeline.py"
EXCLUDED_TESTS = {
    "test_us_training_and_inference_accept_one_new_york_session_crossing_korea_midnight",
    "test_session_progress_uses_market_open_and_exchange_timezone",
    "test_model_training_compares_both_models_and_keeps_them_unapproved",
    "test_model_governance_requires_review_and_records_rollback",
    "test_active_model_registry_keeps_market_and_symbol_selections_independent",
}
FOREIGN_MARKERS = re.compile(
    r"(?<![A-Za-z0-9_])US(?![A-Za-z0-9_])|\bAAPL\b|\bNASDAQ\b|\bNYSE\b|"
    r"\bNASD\b|America/New_York|HDFSCNT0|overseas",
    re.IGNORECASE,
)


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
    if selected_count != 54:
        raise SystemExit(f"Expected 54 domestic AI pipeline tests, found {selected_count}.")
    print(f"Domestic AI pipeline selection verified: {selected_count} tests; 5 cross-market tests excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
