"""Fail closed if the domestic quant test selection starts covering US cases."""

from __future__ import annotations

import ast
import re
from pathlib import Path


TEST_FILE = Path(__file__).resolve().parents[1] / "tests" / "test_quant.py"
EXCLUDED_TESTS = {
    "test_us_backtest_uses_new_york_session_date_across_korea_midnight",
    "test_us_backtest_rejects_premarket_bars",
}
FOREIGN_MARKERS = re.compile(
    r"(?<![A-Za-z0-9_])US(?![A-Za-z0-9_])|\bAAPL\b|\bNASDAQ\b|\bNYSE\b|"
    r"\bNASD\b|America/New_York|HDFSCNT0|overseas",
    re.IGNORECASE,
)


def main() -> int:
    source = TEST_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(TEST_FILE))
    tests = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name.startswith("test_")]
    names = {node.name for node in tests}
    if not EXCLUDED_TESTS.issubset(names):
        raise SystemExit("The domestic quant test exclusions no longer match the test file.")

    unexpected = []
    for test in tests:
        snippet = ast.get_source_segment(source, test) or ""
        if FOREIGN_MARKERS.search(snippet) and test.name not in EXCLUDED_TESTS:
            unexpected.append(test.name)
    if unexpected:
        raise SystemExit("Domestic quant selection would include cross-market cases: " + ", ".join(unexpected))

    selected_count = 0
    for test in tests:
        if test.name in EXCLUDED_TESTS:
            continue
        case_count = 1
        for decorator in test.decorator_list:
            if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "parametrize"):
                if len(decorator.args) < 2 or not isinstance(decorator.args[1], (ast.List, ast.Tuple)):
                    raise SystemExit(f"Cannot count parameterized domestic quant test cases: {test.name}.")
                case_count *= len(decorator.args[1].elts)
        selected_count += case_count
    if selected_count != 32:
        raise SystemExit(f"Expected 32 domestic quant tests, found {selected_count}.")
    print(f"Domestic quant selection verified: {selected_count} tests; 2 US-specific tests excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
