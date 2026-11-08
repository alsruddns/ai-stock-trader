# KIS API compatibility check

Last checked: 2026-11-08 against the current examples in the official KIS repository.

## Read-only account balance

| Market | Endpoint | PAPER transaction ID | REAL transaction ID | Current implementation |
| --- | --- | --- | --- | --- |
| Korea | `/uapi/domestic-stock/v1/trading/inquire-balance` | `VTTC8434R` | `TTTC8434R` | Matches the official endpoint, transaction IDs, request fields, and `output1`/`output2` response mapping. Pagination follows `tr_cont` and the `CTX_AREA_FK100`/`CTX_AREA_NK100` cursors. |
| United States | `/uapi/overseas-stock/v1/trading/inquire-balance` | `VTTS3012R` | `TTTS3012R` | Matches the official endpoint, transaction IDs, `CANO`/`ACNT_PRDT_CD`/exchange/currency fields, `output1`/`output2` mapping, and `CTX_AREA_FK200`/`CTX_AREA_NK200` pagination. |

The official sample lists `NASD`, `NYSE`, and `AMEX` for PAPER balance lookups; REAL also lists `NAS` as a Nasdaq-only code. For REAL position counting across the US market, the sample identifies `NASD` as the all-US-exchange lookup code. A new order-risk check combines current-market and other-market snapshots and refuses buys if either is stale, malformed, or ambiguous. The application accepts the shared `NASD`, `NYSE`, and `AMEX` set for direct environment-specific lookups. The sample also notes that overseas service enrollment is required. Validate account eligibility and live response fields with the user's own read-only account before treating the integration as operationally verified.

## REAL order request shape

| Market | Operation | Endpoint | REAL transaction ID | Compatibility check |
| --- | --- | --- | --- | --- |
| Korea | Cash buy/sell | `/uapi/domestic-stock/v1/trading/order-cash` | `TTTC0012U` buy, `TTTC0011U` sell | Matches official sample's uppercase body fields and limit-order type. |
| Korea | Cancel | `/uapi/domestic-stock/v1/trading/order-rvsecncl` | `TTTC0013U` | Runs `TTTC0084R` cancelable-order lookup first, matches symbol and broker identifiers, and only cancels the observed remaining quantity. |
| United States | Buy/sell | `/uapi/overseas-stock/v1/trading/order` | `TTTT1002U` buy, `TTTT1006U` sell | Matches official sample for US exchanges `NASD`, `NYSE`, and `AMEX`, uppercase request fields, and limit-order type. |
| United States | Cancel | `/uapi/overseas-stock/v1/trading/order-rvsecncl` | `TTTT1004U` | Sends `ORD_QTY=0` with `QTY_ALL_ORD_YN=Y` for full remaining-quantity cancellation; intent is reconciled before cancellation. |

### PAPER cancel support by market

The current official support matrix marks US overseas order, revise/cancel, unfilled-order inquiry, and order/fill inquiry as PAPER-supported. The official US cancel sample maps REAL `TTTT1004U` to PAPER `VTTT1004U` for `/uapi/overseas-stock/v1/trading/order-rvsecncl`; it requires the original broker order number and the matching symbol/exchange. Full cancellation sends `ORD_QTY=0` and `QTY_ALL_ORD_YN=Y`, as required by the sample's quantity contract. The existing US PAPER order path may therefore be exercised through the guarded one-share lifecycle script after the quote, open-session, orderable-funds, and immediate-cancel checks pass. This support matrix is not proof that this account has sufficient USD or that a lifecycle request succeeds; no US PAPER order has yet been sent. [KIS official PAPER support matrix](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/README.md) · [KIS official overseas order/cancel sample](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py)

### Domestic PAPER cancellation gate

The current official support matrix marks domestic cash orders, revise/cancel orders, and daily order/fill inquiry as PAPER-supported, but does not mark the domestic cancelable-order inquiry as PAPER-supported. The PAPER daily inquiry uses `VTTC0081R` and is used here for post-order reconciliation. The official domestic cancel sample separately requires calling `inquire-psbl-rvsecncl` and checking `output.psbl_qty` before submitting a cancel. Its `rmn_qty` from daily inquiry is not treated as an equivalent substitute for that required preflight. On 2026-11-07 16:33 KST, the read-only probe called this endpoint with PAPER credentials and official REAL TR ID `TTTC0084R`; KIS returned HTTP 500, code `EGW02006`, result code `1`, zero rows, and no `psbl_qty`. The result was `unsupported_or_rejected`; it does not identify a supported PAPER preflight TR. The probe now allows only the official sample's `TTTC0084R` and rejects guessed PAPER transaction IDs before loading credentials. Domestic PAPER cancellation remains locked until KIS confirms a supported PAPER preflight and a read-only or mock contract verifies its response. [KIS official PAPER support matrix](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/README.md) · [official daily order/fill inquiry sample](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_ccld/inquire_daily_ccld.py) · [official domestic cancel sample](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py)

Read-only account integration was successfully rechecked on 2026-11-07: Spring PAPER KR/US balance and orderable-cash GETs (4 tests), Python PAPER KR/US quote and balance GETs (4 requests), and Spring REAL KR/US balance GETs (2 tests) passed. Earlier same-day `EGW00002` responses did not recur. These checks sent no order or cancel requests. The persistent application remains PAPER; REAL account reads, REAL/PAPER orders, and REAL reconciliation are disabled in its running configuration.

## Read-only quote snapshots

| Market | Endpoint | PAPER transaction ID | REAL transaction ID | Current implementation |
| --- | --- | --- | --- | --- |
| Korea | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` | `FHKST01010100` | Uses `FID_COND_MRKT_DIV_CODE=J` and `FID_INPUT_ISCD`; matches the current official sample for KRX-listed stocks. |
| United States | `/uapi/overseas-price/v1/quotations/price` | `HHDFS00000300` | `HHDFS00000300` | Uses `AUTH` (empty as in the official sample), `EXCD`, and `SYMB`; supports configured `NAS`, `NYS`, and `AMS` exchanges. |

These REST endpoints provide quote snapshots. The official domestic example directs clients to WebSocket for real-time quotes; the project's separate WebSocket stream is therefore the real-time path. Authenticated PAPER smoke on 2026-11-06 returned valid KIS REST quote contracts for KR symbol 005930 and US symbol AAPL. This verifies these two quote requests only; it does not validate account access, WebSocket continuity, or trading entitlement.

The official overseas `price.py` sample explicitly maps both `real` and `demo` to `HHDFS00000300`, uses `/uapi/overseas-price/v1/quotations/price`, and normalizes `output` to a list before consuming it. The client accepts either the usual object or exactly one object in an array; empty arrays, multiple rows, and malformed arrays fail closed. The official overseas MCP configuration also declares `env_dv=demo` for this quote operation. These sources establish supported sample parameters, not the reason for a specific PAPER HTTP 502; the next market-session smoke must inspect only sanitized diagnostics. [KIS official overseas quote sample](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/price/price.py) · [KIS official overseas API configuration](https://github.com/koreainvestment/open-trading-api/blob/main/MCP/Kis%20Trading%20MCP/configs/overseas_stock.json)

Exchange codes must match the instrument: quote requests use `NAS`, `NYS`, or `AMS`, while overseas order/account requests use `NASD`, `NYSE`, or `AMEX`. Ford's own shareholder site identifies ticker `F` as `NYSE: F`; the scheduled Ford PAPER lifecycle smoke now uses `NYS` for its quote and `NYSE` for its account/order requests. A prior manual `F` quote probe used `NAS`, so its 502 does not establish that the PAPER endpoint itself failed for the correct venue. The next scheduled run is the first venue-correct runtime check; it must still stop before order submission if quote/account/session preflights fail. [Ford official stock information](https://www.shareholder.ford.com/Information/stock/default.aspx)

## Read-only realized-profit statements

| Market | Endpoint | REAL transaction ID | Current implementation |
| --- | --- | --- | --- |
| Korea | `/uapi/domestic-stock/v1/trading/inquire-period-profit` | `TTTC8708R` | Reads `output1` details and `output2` summary; follows `tr_cont` and `CTX_AREA_FK100`/`CTX_AREA_NK100` up to 20 pages. |
| United States | `/uapi/overseas-stock/v1/trading/inquire-period-profit` | `TTTS3039R` | Uses NASD/NYSE/AMEX, USD and KRW-conversion mode `02`; follows `tr_cont` and `CTX_AREA_FK200`/`CTX_AREA_NK200` up to 20 pages. Official sample identifies this endpoint as REAL-only. |

The API returns the unmodified `output1`/`output2` JSON payload for local operator review; the application does not persist it and does not use it as a risk input. Official examples establish endpoint, transaction ID, input parameters and output containers, but this check has not verified a stable field-level accounting mapping. The KIS overseas sample says period profit is available from the next trading day, is informational rather than tax-basis P&L, uses the sale-date exchange rate for KRW conversion, and includes sell costs (with same-day buy costs in its stated exception). This adapter is contract-tested with mocked responses only; the user's live statement has not been queried. See the [official domestic sample](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_domstk.py) and [official overseas sample](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py).

## Domestic order cancellation preflight

The domestic cancel adapter calls `/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl` with REAL transaction ID `TTTC0084R`, `INQR_DVSN_1=1`, and `INQR_DVSN_2=0`, matching the current official sample's demonstrated item-order/whole-side query. It follows the response cursor (up to 10 pages), matches the symbol, broker order number, and organization number, requires a positive `psbl_qty`, and requires that value to equal the most recent open quantity before the cancel POST is allowed. Missing rows, unsupported responses, pagination without a cursor, or quantity mismatches fail closed. The official sample requires this availability lookup before a domestic revise/cancel request and documents up to 50 rows per page. The official cancel-order example maps REAL to `TTTC0013U` and demo to `VTTC0013U`, but its required preflight example documents `TTTC0084R` only and does not define a mock transaction ID. Domestic PAPER cancellation therefore stays disabled until KIS confirms a supported mock preflight or a valid mock preflight row is observed. This adapter is covered by Mock tests only; no REAL cancellation was performed. See the [official preflight example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py) and [official cancel-order example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py).

## Security and verification status

- Account reads require both `KIS_ACCOUNT_READ_ENABLED=true` and the environment-specific switch. Both remain false by default.
- REAL and PAPER credentials are separate. Secrets and account numbers must remain in the ignored local `.env_local` file.
- The user re-registered the PAPER account and replaced its PAPER credentials in `.env_local`. The 2026-11-06 Spring PAPER account smoke then passed both domestic and US balance GET tests (2 passed, 0 failed). The preceding `OPSQ2000` was resolved by the updated PAPER registration/configuration. A separate opt-in REAL account-read smoke initially hit `EGW00201`, then passed domestic and US balance GET tests after the documented rate-limit interval was respected. These were read-only requests; no order request was sent. REAL account reads remain disabled by default and are currently locked in `.env_local`.
- The REAL cross-market position counter is validated with mocked snapshots only. It needs both environment-specific account-read switches at runtime and calls KIS for the other market; the user's current REAL read switch is off, so this counter has not been live-smoked.
- Spring now includes only the upstream HTTP status number in sanitized account-request errors; response bodies and credential-bearing details remain suppressed.
- This project has not enabled live orders. The read-only account path must not be used as evidence that order submission, market data entitlement, or fill reconciliation is verified.

## WebSocket market data

The official KIS WebSocket sample answers a JSON `PINGPONG` message by sending its payload back as a WebSocket Pong frame. Both the local quote relay and the PAPER session monitor now do this and skip the control message in the trade-tick parser. This was checked against the [official KIS WebSocket sample](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/websocket/python/ops_ws_sample.py). Live feed connectivity still requires an authenticated account-level smoke test.

## Upcoming API retirement

The KIS Developers portal announced that seven domestic-stock financial analysis APIs (balance sheet, income statement, financial ratios, profitability, other key ratios, stability, and growth) will be blocked after market close on 2026-11-11 at 16:00 KST because of source-data licensing changes. The current application uses quote, minute-chart, balance, and order endpoints and does not reference these seven analysis endpoints; keep them out of the runtime unless KIS publishes a replacement and its permitted use is confirmed. See the [KIS Developers notice page](https://apiportal.koreainvestment.com/intro).

## Official references

- [KIS official domestic stock balance example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_balance/inquire_balance.py)
- [KIS official overseas stock balance example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/overseas_stock/overseas_stock_functions.py)
- [KIS official domestic quote example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_price/inquire_price.py)
- [KIS official domestic revise/cancel-possible query example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py)
- [KIS official domestic cash order example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_cash/order_cash.py)
- [KIS official domestic revise/cancel order example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py)
- [KIS official overseas order and cancel examples](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py)
- [KIS official overseas quote example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/price/price.py)
- [KIS Developers error code reference](https://apiportal.koreainvestment.com/faq-error-code)
- [KIS Developers portal](https://apiportal.koreainvestment.com/)

Recheck these references and the API portal before changing transaction IDs, fields, pagination behavior, or account environment handling.

## Retired domestic financial-analysis APIs

KIS announced that the seven domestic stock financial-analysis APIs will be blocked after market close on 2026-11-11. The official sample names these calls `finance_balance_sheet`, `finance_income_statement`, `finance_financial_ratio`, `finance_profit_ratio`, `finance_other_major_ratios`, `finance_stability_ratio`, and `finance_growth_ratio`. They are not used by this application. Windows CI scans Spring and FastAPI runtime source for those helper identifiers and the corresponding direct REST path literals under `/uapi/domestic-stock/v1/finance/`; it fails if any is added. Documentation and test fixtures are outside the scan. The guard detects identifiers, complete path literals, and standalone endpoint suffixes that could be joined to a shared prefix. Arbitrary computed endpoint values cannot be proven by this static check. Do not replace these APIs with another source until its license and allowed use are verified.

- [KIS official notice page](https://apiportal.koreainvestment.com/intro)
- [KIS official domestic-stock examples with financial-analysis call identifiers](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_examples.py)
