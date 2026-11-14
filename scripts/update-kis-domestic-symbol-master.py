from __future__ import annotations

import argparse
import csv
import io
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path


SOURCES = {
    "KOSPI": ("https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip", "kospi_code.mst", 228),
    "KOSDAQ": ("https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip", "kosdaq_code.mst", 222),
}


def download_rows(market: str, timeout: int) -> list[tuple[str, str, str]]:
    url, member_name, tail_bytes = SOURCES[market]
    request = urllib.request.Request(url, headers={"User-Agent": "ai-stock-trader-local/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        archive_bytes = response.read()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        candidates = [name for name in archive.namelist() if Path(name).name.lower() == member_name]
        if len(candidates) != 1:
            raise ValueError(f"Expected one {member_name} file in official archive")
        content_bytes = archive.read(candidates[0])
    content = content_bytes.decode("cp949")

    rows: list[tuple[str, str, str]] = []
    for line in content.splitlines():
        if not line:
            continue
        if len(line) <= tail_bytes:
            raise ValueError(f"Malformed {market} master row")
        security_code = line[:-tail_bytes][:9].strip()
        symbol = security_code[:6]
        group_code = line[-tail_bytes:][:2].strip()
        # KIS master files also contain non-stock instruments with non-numeric codes.
        if len(symbol) != 6 or not symbol.isdigit():
            continue
        if not 1 <= len(group_code) <= 2 or not group_code.isascii() or not group_code.isalnum() and group_code != ")E":
            raise ValueError(f"Invalid {market} security group for symbol {symbol}")
        rows.append((symbol, market, group_code))
    if not rows:
        raise ValueError(f"Official {market} master was empty")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh local KIS domestic symbol master (no credentials required).")
    parser.add_argument("--output", type=Path, default=Path(".local/reference-data/kis-domestic-symbols.csv"))
    parser.add_argument("--timeout-seconds", type=int, default=20)
    args = parser.parse_args()
    if args.timeout_seconds < 1:
        parser.error("--timeout-seconds must be positive")

    rows = download_rows("KOSPI", args.timeout_seconds) + download_rows("KOSDAQ", args.timeout_seconds)
    symbols = [row[0] for row in rows]
    if len(set(symbols)) != len(symbols):
        raise ValueError("Duplicate symbol found across official KIS masters")

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as output:
        output.write(f"#generated_at={generated_at}\n")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("symbol", "market", "group_code"))
        writer.writerows(sorted(rows))
    temporary.replace(args.output)
    print(f"Updated {len(rows)} domestic symbols from official KIS masters: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
