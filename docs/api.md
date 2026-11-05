# API

## Local Spring API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Paper/live-lock 상태 확인 |
| POST | `/api/v1/orders/paper` | 로컬 paper 주문 생성 |
| GET | `/api/v1/orders/paper/{id}` | Paper 주문 상태 확인 |
| POST | `/api/v1/orders/paper/{id}/fills` | Paper 체결 기록 |
| POST | `/api/v1/orders/paper/{id}/cancel` | Paper 취소 처리 |
| GET | `/api/v1/orders/paper/positions` | Paper 포지션 조회 |
| GET | `/api/v1/kis/accounts/{paper\|real}/{KR\|US}/balance` | KIS 계좌 읽기 전용 잔고조회 |
| WS | `/api/v1/market/stream` | KIS 국내·미국 실시간 체결 데이터 읽기 전용 릴레이 |

KIS API는 기본 잠금(`KIS_ACCOUNT_READ_ENABLED=false`)입니다. 종목별 매수가능액 확인 시 `symbol`과 `unitPrice`를 함께 전달합니다. 미국 조회의 `exchange`는 `NASD`, `NYSE`, `AMEX` 중 하나입니다. 계좌 API는 계좌번호, App Key, App Secret을 응답에 포함하지 않습니다.

## KIS REST 요청 범위

- 인증: OAuth client credentials `/oauth2/tokenP`
- 국내 잔고: `/uapi/domestic-stock/v1/trading/inquire-balance`
- 국내 종목 매수가능: `/uapi/domestic-stock/v1/trading/inquire-psbl-order`
- 미국 잔고: `/uapi/overseas-stock/v1/trading/inquire-balance`
- 미국 USD 잔고: `/uapi/overseas-stock/v1/trading/inquire-present-balance`
- 미국 종목 매수가능: `/uapi/overseas-stock/v1/trading/inquire-psamount`

Read timeout, HTTP 429 및 KIS rate-limit 오류는 안전한 오류 응답으로 바꾸고 요청을 자동 재전송하지 않습니다.

## KIS WebSocket

`KIS_WEBSOCKET_ENABLED=true`와 선택한 `KIS_ENVIRONMENT` 자격증명이 필요합니다. 연결 뒤 아래 JSON 중 하나를 전송하면 국내 또는 해외 체결 데이터 한 종목을 릴레이합니다.

```json
{"market":"KR","symbol":"005930"}
{"market":"US","symbol":"AAPL","exchange":"NAS"}
```

시세 전용이며 주문·체결통보 구독은 노출하지 않습니다. 공식 샘플의 모의 31000/실전 21000 포트와 `H0STCNT0`/`HDFSCNT0` 구독 ID를 사용합니다. 계정별 제공 데이터와 세션 제한은 실계정 연결 전에 해당 KIS 환경에서 검증해야 합니다.
