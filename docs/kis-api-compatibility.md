# KIS API compatibility check

Last checked: 2026-11-06 against the current examples in the official KIS repository.

## Read-only account balance

| Market | Endpoint | PAPER transaction ID | REAL transaction ID | Current implementation |
| --- | --- | --- | --- | --- |
| Korea | `/uapi/domestic-stock/v1/trading/inquire-balance` | `VTTC8434R` | `TTTC8434R` | Matches the official endpoint, transaction IDs, request fields, and `output1`/`output2` response mapping. Pagination follows `tr_cont` and the `CTX_AREA_FK100`/`CTX_AREA_NK100` cursors. |
| United States | `/uapi/overseas-stock/v1/trading/inquire-balance` | `VTTS3012R` | `TTTS3012R` | Matches the official endpoint, transaction IDs, `CANO`/`ACNT_PRDT_CD`/exchange/currency fields, `output1`/`output2` mapping, and `CTX_AREA_FK200`/`CTX_AREA_NK200` pagination. |

The official sample lists `NASD`, `NYSE`, and `AMEX` for PAPER balance lookups; REAL also lists `NAS` as a Nasdaq-only code. The application accepts the shared `NASD`, `NYSE`, and `AMEX` set for both environments. The sample also notes that overseas service enrollment is required. Validate account eligibility and live response fields with the user's own read-only account before treating the integration as operationally verified.

## Read-only quote snapshots

| Market | Endpoint | PAPER transaction ID | REAL transaction ID | Current implementation |
| --- | --- | --- | --- | --- |
| Korea | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` | `FHKST01010100` | Uses `FID_COND_MRKT_DIV_CODE=J` and `FID_INPUT_ISCD`; matches the current official sample for KRX-listed stocks. |
| United States | `/uapi/overseas-price/v1/quotations/price` | `HHDFS00000300` | `HHDFS00000300` | Uses `AUTH` (empty as in the official sample), `EXCD`, and `SYMB`; supports configured `NAS`, `NYS`, and `AMS` exchanges. |

These REST endpoints provide quote snapshots. The official domestic example directs clients to WebSocket for real-time quotes; the project's separate WebSocket stream is therefore the real-time path. Authenticated PAPER smoke on 2026-11-06 returned valid KIS REST quote contracts for KR symbol 005930 and US symbol AAPL. This verifies these two quote requests only; it does not validate account access, WebSocket continuity, or trading entitlement.

## Security and verification status

- Account reads require both `KIS_ACCOUNT_READ_ENABLED=true` and the environment-specific switch. Both remain false by default.
- REAL and PAPER credentials are separate. Secrets and account numbers must remain in the ignored local `.env_local` file.
- Authenticated PAPER account smoke on 2026-11-06 reached KIS for both domestic and US balance GETs, and both returned `OPSQ2000`. The official KIS error table defines this as `INPUT INVALID_CHECK_ACNO`; the account identity or PAPER account pairing remains unresolved. No order request was sent. The local PAPER account-read gates are enabled for diagnosis; REAL account reads remain disabled.
- This project has not enabled live orders. The read-only account path must not be used as evidence that order submission, market data entitlement, or fill reconciliation is verified.

## WebSocket market data

The official KIS WebSocket sample answers a JSON `PINGPONG` message by sending its payload back as a WebSocket Pong frame. Both the local quote relay and the PAPER session monitor now do this and skip the control message in the trade-tick parser. This was checked against the [official KIS WebSocket sample](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/websocket/python/ops_ws_sample.py). Live feed connectivity still requires an authenticated account-level smoke test.

## Official references

- [KIS official domestic stock balance example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_balance/inquire_balance.py)
- [KIS official overseas stock balance example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/overseas_stock/overseas_stock_functions.py)
- [KIS official domestic quote example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_price/inquire_price.py)
- [KIS official overseas quote example](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/price/price.py)
- [KIS Developers error code reference](https://apiportal.koreainvestment.com/faq-error-code)
- [KIS Developers portal](https://apiportal.koreainvestment.com/)

Recheck these references and the API portal before changing transaction IDs, fields, pagination behavior, or account environment handling.
