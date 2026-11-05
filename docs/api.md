# API

## Local Spring API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Paper/live-lock 상태 확인 |
| GET | `/api/v1/operations/summary` | paper 현금·보유·주문·당일 체결의 로컬 운영 요약 |
| POST | `/api/v1/orders/paper` | 로컬 paper 주문 생성 (선택 `strategyId`, `modelId` 보존) |
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
| POST | `/api/v1/ai/models/{model_id}/approve` | 로컬 거버넌스 토큰과 검토자·사유로 모델 승인 기록 |
| POST | `/api/v1/ai/models/{model_id}/activate` | 승인된 advisory 모델 활성화 |
| POST | `/api/v1/ai/models/{model_id}/rollback` | 승인된 이전 모델로 advisory 포인터 복구 |

모델 `approve`, `activate`, `rollback` 변경 요청은 `.env`에 설정한 `MODEL_GOVERNANCE_TOKEN`을 `X-Model-Governance-Token` 헤더로 전달해야 합니다. 토큰 미설정 시 `503`, 불일치 시 `403`을 반환합니다. 토큰은 응답과 감사 기록에 저장되지 않습니다.

DB 접근 또는 트랜잭션 커밋 실패는 HTTP `503`과 일반 오류 메시지를 반환합니다. 응답에는 DB 드라이버 세부 내용이나 비밀값을 포함하지 않습니다. 클라이언트는 주문 완료로 간주하지 말고 idempotency key로 결과를 조회해야 합니다.

KIS API는 기본 잠금(`KIS_ACCOUNT_READ_ENABLED=false`)입니다. 조회를 열려면 전체 스위치와 환경별 스위치를 모두 설정해야 합니다. REAL 조회에는 `KIS_REAL_ACCOUNT_READ_ENABLED=true`가 추가로 필요하며 기본값은 false입니다. 종목별 매수가능액 확인 시 `symbol`과 `unitPrice`를 함께 전달합니다. 미국 조회의 `exchange`는 `NASD`, `NYSE`, `AMEX` 중 하나입니다. 계좌 API는 계좌번호, App Key, App Secret을 응답에 포함하지 않습니다.

## KIS REST 요청 범위

국내·미국 잔고 경로와 PAPER/REAL 거래 ID는 [KIS API compatibility](kis-api-compatibility.md)에 공식 예제 대조 기록과 함께 정리했습니다. 해당 검토는 계좌 인증 연동 성공을 의미하지 않습니다.

- 인증: OAuth client credentials `/oauth2/tokenP`
- 국내 잔고: `/uapi/domestic-stock/v1/trading/inquire-balance`
- 국내 종목 매수가능: `/uapi/domestic-stock/v1/trading/inquire-psbl-order`
- 미국 잔고: `/uapi/overseas-stock/v1/trading/inquire-balance`
- 미국 종목 매수가능: `/uapi/overseas-stock/v1/trading/inquire-psamount`

미국 잔고 응답은 공식 예제 구조에 따라 `output1`을 보유 종목, `output2`를 계좌 요약으로 처리합니다. 공개 샘플만으로 USD 현금 필드 의미를 확정할 수 없어 USD `cashBalance`는 `null`로 둡니다. 특정 종목의 매수가능액은 `inquire-psamount` 조회 결과만 사용합니다. 총자산 평가액은 현금이나 주문가능금액으로 사용하지 않습니다.

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

LightGBM·XGBoost 후보는 `--market KR`과 `--market US`로 분리 학습하고 세션 날짜 기준 시간순 학습/검증/테스트로 나눕니다. 각 피처는 신호 시점까지의 데이터만 사용하고, 레이블은 미래 N개 봉 수익에서 왕복 비용 추정치를 차감해 계산합니다. 추론 요청의 시장은 모델 메타데이터 시장과 일치해야 합니다. 테스트 성능은 후보 검토 자료이며 전략 우위를 증명하지 않습니다. 모델·메타데이터는 `.local/models`에만 저장하고 모델 아티팩트와 입력 CSV SHA-256을 기록합니다. 새 모델은 승인 전 상태이며 예측 응답 `executable`은 항상 `false`입니다.

승인·활성화·롤백 API는 운영자와 사유를 요구하고 모델 상태 및 JSONL 변경 이력을 `.local/models`에 기록합니다. 이는 로컬 감사 편의 기록이며 파일 자체 변조를 막는 보안 감사 시스템은 아닙니다. 활성 모델 포인터는 예측 결과나 주문 실행 권한을 바꾸지 않습니다.

## Operations reporting

Spring의 `/api/v1/operations/summary`는 paper 주문·체결·현금·포지션과 별도로 REAL 주문 의도별 상태·체결량을 반환합니다. REAL 계좌번호와 브로커 주문 식별자는 응답하지 않습니다. `kisRealOrdersEnabled`, `kisAccountReadsEnabled`, `kisPaperAccountReadsEnabled`, `kisRealAccountReadsEnabled`, `kisRealReconciliationEnabled`, `kisPaperCredentialsConfigured`, `kisRealCredentialsConfigured`, `realOrderCounts`, `recentRealOrders`는 운영 잠금 및 로컬 DB의 REAL 주문 의도 상태이며 실제 계좌 잔고를 뜻하지 않습니다. 자격정보 플래그는 PAPER/REAL App Key·Secret 입력과 10자리 계좌번호 형식만 확인하며, KIS 인증 성공이나 계좌 접근 가능성을 검증하지 않습니다. 포지션 원가는 통화별 평균 취득원가이며 실시간 평가액이 아닙니다. `realizedPnlTodayGrossByCurrency`는 paper 실현 손익 총액이고, `realizedPnlTodayNetByCurrency`는 매수 비용을 평균원가에 반영하고 매도 비용을 차감합니다. `paperTransactionCostsTodayByCurrency`는 오늘 체결 건에 기록된 비용입니다. `PAPER_COST_MODEL_ENABLED=false`면 비용은 0으로 기록되어 총액과 순액이 같으므로, 비용률을 입력하고 설정을 켜기 전의 순손익은 비용 반영 수치가 아닙니다. `paperPerformanceByInstrument`는 전략·AI 모델·시장·종목별 매도 주문 수, 승·패, 승률, 평균 이익·손실, 매도 주문당 기대손익, 손익비, 실현 손익 경로의 최대 하락액, 누적 순실현손익을 포함합니다. 자동 진입의 `strategyId`와 승인된 `modelId`를 PAPER 주문 및 손절·익절 상태에 저장하고 청산 주문으로 이어 붙입니다. 이 ID가 없는 과거·수동 주문은 `null`로 분류됩니다. 체결 원장을 평균원가로 재생하며 PAPER 장부 전용이므로 실계좌 수익이나 전략 우위나 평가손익을 뜻하지 않습니다. 손익비는 총이익/총손실이며 손실이 없으면 `null`입니다. 실현 최대 하락액은 해당 전략·모델 분류의 매도 주문 실현손익 경로만 반영합니다. `scripts/export-daily-report.ps1`은 이 요약을 `.local/reports`에 날짜별 JSON으로 저장합니다. 대시보드는 조회 전용이며 주문·취소 기능을 제공하지 않습니다.
