# API

## Local Spring API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Paper/live-lock 상태 확인 |
| GET | `/api/v1/operations/summary` | paper 현금·보유·주문·당일 체결의 로컬 운영 요약 |
| POST | `/api/v1/orders/paper` | 로컬 paper 주문 생성 |
| GET | `/api/v1/orders/paper/{id}` | Paper 주문 상태 확인 |
| GET | `/api/v1/orders/paper/by-idempotency/{key}` | 애매한 쓰기 결과 대조용 주문 조회 |
| POST | `/api/v1/orders/paper/{id}/fills` | Paper 체결 기록 |
| POST | `/api/v1/orders/paper/{id}/cancel` | Paper 취소 처리 |
| GET | `/api/v1/orders/paper/positions` | Paper 포지션 조회 |
| GET | `/api/v1/kis/accounts/{paper\|real}/{KR\|US}/balance` | KIS 계좌 읽기 전용 잔고조회 |
| WS | `/api/v1/market/stream` | KIS 국내·미국 실시간 체결 데이터 읽기 전용 릴레이 |
| POST | `/api/v1/strategies/orb-vwap/backtest` | 전달된 단일 세션 봉 데이터의 ORB/VWAP 비용 반영 백테스트 |
| POST | `/api/v1/ai/models/{model_id}/predict` | 등록 모델의 비실행 예측 확률 미리보기 |
| GET | `/api/v1/ai/models` | 모델 승인·활성 상태와 최근 거버넌스 이벤트 |
| POST | `/api/v1/ai/models/{model_id}/approve` | 검토자·사유와 함께 모델 승인 기록 |
| POST | `/api/v1/ai/models/{model_id}/activate` | 승인된 advisory 모델 활성화 |
| POST | `/api/v1/ai/models/{model_id}/rollback` | 승인된 이전 모델로 advisory 포인터 복구 |

KIS API는 기본 잠금(`KIS_ACCOUNT_READ_ENABLED=false`)입니다. 조회를 열려면 전체 스위치와 환경별 스위치를 모두 설정해야 합니다. REAL 조회에는 `KIS_REAL_ACCOUNT_READ_ENABLED=true`가 추가로 필요하며 기본값은 false입니다. 종목별 매수가능액 확인 시 `symbol`과 `unitPrice`를 함께 전달합니다. 미국 조회의 `exchange`는 `NASD`, `NYSE`, `AMEX` 중 하나입니다. 계좌 API는 계좌번호, App Key, App Secret을 응답에 포함하지 않습니다.

## KIS REST 요청 범위

- 인증: OAuth client credentials `/oauth2/tokenP`
- 국내 잔고: `/uapi/domestic-stock/v1/trading/inquire-balance`
- 국내 종목 매수가능: `/uapi/domestic-stock/v1/trading/inquire-psbl-order`
- 미국 잔고: `/uapi/overseas-stock/v1/trading/inquire-balance`
- 미국 USD 잔고: `/uapi/overseas-stock/v1/trading/inquire-present-balance`
- 미국 종목 매수가능: `/uapi/overseas-stock/v1/trading/inquire-psamount`

Read timeout, HTTP 429 및 KIS rate-limit 오류는 안전한 오류 응답으로 바꾸고 요청을 자동 재전송하지 않습니다.

Paper 주문 응답이 타임아웃되면 `GET /api/v1/orders/paper/by-idempotency/{key}`로 결과를 먼저 대조합니다. 자동화 클라이언트는 주문 쓰기를 재시도하지 않습니다.

## KIS WebSocket

`KIS_WEBSOCKET_ENABLED=true`와 선택한 `KIS_ENVIRONMENT` 자격증명이 필요합니다. 연결 뒤 아래 JSON 중 하나를 전송하면 국내 또는 해외 체결 데이터 한 종목을 릴레이합니다.

```json
{"market":"KR","symbol":"005930"}
{"market":"US","symbol":"AAPL","exchange":"NAS"}
```

시세 전용이며 주문·체결통보 구독은 노출하지 않습니다. 공식 샘플의 모의 31000/실전 21000 포트와 `H0STCNT0`/`HDFSCNT0` 구독 ID를 사용합니다. 계정별 제공 데이터와 세션 제한은 실계정 연결 전에 해당 KIS 환경에서 검증해야 합니다.

## Quant backtest

`POST /api/v1/strategies/orb-vwap/backtest`는 클라이언트가 전달한 시간대 포함 1분봉 한 세션에 대해 ORB/VWAP/EMA/ATR·상대거래량·스프레드·거래대금 조건을 평가합니다. 진입은 신호 다음 봉의 시가부터, 수수료·슬리피지를 반영합니다. 같은 봉에서 손절과 목표가가 모두 닿으면 보수적으로 손절 우선 처리합니다. 데이터 공급·권리·정합성은 요청자가 책임지며 API는 저장하거나 주문하지 않습니다. 결과의 `executable`은 항상 `false`입니다.

## AI inference

LightGBM·XGBoost 후보는 `--market KR`과 `--market US`로 분리 학습하고 세션 날짜 기준 시간순 학습/검증/테스트로 나눕니다. 각 피처는 신호 시점까지의 데이터만 사용하고, 레이블은 미래 N개 봉 수익에서 왕복 비용 추정치를 차감해 계산합니다. 추론 요청의 시장은 모델 메타데이터 시장과 일치해야 합니다. 테스트 성능은 후보 검토 자료이며 전략 우위를 증명하지 않습니다. 모델·메타데이터는 `.local/models`에만 저장하고 SHA-256을 확인합니다. 새 모델은 승인 전 상태이며 예측 응답 `executable`은 항상 `false`입니다.

승인·활성화·롤백 API는 운영자와 사유를 요구하고 모델 상태 및 JSONL 변경 이력을 `.local/models`에 기록합니다. 이는 로컬 감사 편의 기록이며 파일 자체 변조를 막는 보안 감사 시스템은 아닙니다. 활성 모델 포인터는 예측 결과나 주문 실행 권한을 바꾸지 않습니다.

## Operations reporting

Spring의 `/api/v1/operations/summary`는 저장된 paper 주문·체결·현금·포지션 상태를 요약합니다. 포지션 원가는 통화별 평균 취득원가이며 실시간 평가액이 아닙니다. `realizedPnlTodayGrossByCurrency`는 평균 원가 기준의 실현 손익으로, 수수료·세금 차감 전입니다. `scripts/export-daily-report.ps1`은 이 응답을 `.local/reports`에 날짜별 JSON으로 저장합니다. 대시보드는 Spring 및 Python 상태와 이 요약을 조회하며 주문·취소 기능을 제공하지 않습니다.
