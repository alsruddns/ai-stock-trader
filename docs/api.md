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
| POST | `/api/v1/orders/paper/{id}/amend` | 미체결 PAPER 장부 주문 수량·지정가 정정 (`Idempotency-Key` 헤더 필수) |
| POST | `/api/v1/orders/paper/{id}/cancel` | Paper 취소 처리 |
| GET | `/api/v1/orders/paper/positions` | Paper 포지션 조회 |
| GET | `/api/v1/kis/accounts/{paper\|real}/{KR\|US}/balance` | KIS 계좌 읽기 전용 잔고조회 |
| GET | `/api/v1/kis/accounts/realized-pnl/real/{KR\|US}?from=YYYY-MM-DD&to=YYYY-MM-DD` | KIS REAL 기간손익 원문 조회(최대 31일, REAL 계좌읽기 스위치 필요) |
| POST | `/api/v1/kis/orders/real` | REAL 주문 의도 제출 (`Idempotency-Key` 필수, 모든 REAL 주문·계좌 읽기 스위치 필요) |
| POST | `/api/v1/kis/orders/real/signals` | 모델 승인·시세·계좌 확인 후 서버 수량으로 REAL 매수 신호 제출 (가격·수량은 요청 불가) |
| GET | `/api/v1/kis/orders/real/{idempotencyKey}` | 저장된 REAL 주문 의도 상태 조회 |
| POST | `/api/v1/kis/orders/real/{idempotencyKey}/reconcile` | 저장된 REAL 주문의 KIS 상태 재조회 (REAL 주문·계좌 읽기 스위치 필요) |
| POST | `/api/v1/kis/orders/real/{idempotencyKey}/cancel` | 잔량 대조 후 REAL 주문 취소 (REAL 주문·계좌 읽기 스위치 필요) |
| WS | `/api/v1/market/stream` | KIS 국내·미국 실시간 체결 데이터 읽기 전용 릴레이 |
| POST | `/api/v1/strategies/orb-vwap/backtest` | 전달된 단일 세션 봉 데이터의 ORB/VWAP 비용 반영 백테스트 |
| POST | `/api/v1/ai/models/{model_id}/predict` | 등록 모델의 비실행 예측 확률 미리보기 |
| GET | `/api/v1/ai/models` | 모델 승인·활성 상태와 최근 거버넌스 이벤트 |
| POST | `/api/v1/ai/models/{model_id}/approve` | 로컬 거버넌스 토큰과 검토자·사유로 모델 승인 기록 |
| POST | `/api/v1/ai/models/{model_id}/activate` | 승인된 advisory 모델 활성화 |
| POST | `/api/v1/ai/models/{model_id}/rollback` | 승인된 이전 모델로 advisory 포인터 복구 |

`GET /api/v1/ai/models` 응답의 `governance_audit`은 `integrity_verified`와 `event_count`를 제공합니다. 승인·활성화·롤백 이벤트는 앞 이벤트 SHA-256을 포함하는 JSONL 해시 체인으로 저장됩니다. 이벤트 내용 변경, 중간 이벤트 삭제, 순서 변경 또는 JSONL 파손이 발견되면 체인 전체를 무효로 보고 모델 승인과 활성 선택을 차단하며, 운영 화면은 무결성 실패를 표시합니다. 마지막 이벤트의 삭제처럼 로그 끝을 잘라낸 경우는 별도 외부 체크포인트 없이는 감지하지 못합니다. 이 로컬 해시 체인은 로그 변경 탐지용이며 외부 서명이나 원격 불변 저장소를 대체하지 않습니다.

REAL 매수의 지정가는 서버가 읽은 최신 시세를 넘을 수 없습니다. 시장가격보다 낮은 지정가는 허용될 수 있으나 체결을 보장하지 않으며, 시세가 오래됐거나 없으면 주문 위험 게이트가 거부합니다.

REAL 매수의 전략·모델 ID는 요청 본문 값으로 신뢰하지 않습니다. Spring은 FastAPI의 현재 완료 신호가 `BUY_CANDIDATE`이고, 신호의 모델 ID가 활성 승인·권리 검증 모델과 일치하며, 유효한 전략 ID·손절 거리·신선도가 확인될 때만 해당 서버 근거를 주문 이력에 기록합니다. 이 증거가 없으면 AI 매수 게이트를 통과하지 못합니다.

승인된 REAL 매수 주문 의도에는 검증된 종목당 손절거리와 v1 보상/위험 배수 2.0을 함께 저장합니다. 부분 체결 대조로 평균 체결가가 확정된 뒤 두 값을 이용해 보호 가격을 재구성할 수 있습니다. 이 저장은 보호청산 감시나 매도 주문 실행을 뜻하지 않습니다.

REAL 매수 요청의 `quantity`는 주문자가 제시한 상한으로만 사용하며, Spring은 최신 KIS 계좌 스냅샷의 주문가능금액·종목 평가액·계좌 평가액과 거래당 위험 예산·주문 한도를 적용해 요청 수량을 줄일 수 있습니다. 산정 입력이 없으면 원래 요청을 위험 게이트에 넘기며, 게이트가 누락 정보를 이유로 거부합니다. 산정된 수량은 주문 의도에 저장되고 같은 위험 게이트에서 다시 검증됩니다. 이 수량 제한은 실전 주문 활성화나 수익·체결을 보장하지 않습니다.

Python `SpringRealSignalOrderClient`는 이 로컬 Spring 경로에만 요청하며 KIS를 직접 호출하지 않습니다. 별도 `REAL_SIGNAL_MONITOR_ENABLED=true`로 REAL WebSocket 세션 감시를 선택하고, `REAL_SIGNAL_SUBMISSION_ENABLED=true`와 실전 모드·주문·계좌읽기·보호청산·주문대조 잠금, REAL REST 시세 설정 및 분봉 저장이 모두 준비돼야 signal POST를 보냅니다. 요청은 신호 봉마다 결정적인 UUID 멱등키를 사용하므로 재시작이나 모델 전환 뒤 동일 봉을 재전송해도 같은 키가 유지됩니다. 타임아웃·전송오류·Spring 5xx는 결과 불명으로 처리해 재전송하지 않고, 해당 프로세스의 신규 신호 전달을 중단합니다. 기본값은 `.env.example`에서 false입니다. 실거래 스위치는 자동으로 켜지지 않으며 실계좌 종단 검증은 별도입니다.

`POST /api/v1/kis/orders/real/signals` 요청 본문은 `market`, `symbol`만 받습니다. 미국 시장은 `exchange` query 값도 필요합니다. Spring은 전체 REAL 잠금과 `REAL_PROTECTIVE_EXIT_MANAGER_ENABLED`를 먼저 확인하고 FastAPI의 최신 승인 모델 신호·시세를 확인한 뒤 읽기 전용 REAL 계좌 조회를 합니다. 일반 REAL 매수 API도 같은 보호청산 잠금을 확인합니다. 매도·취소·대조 경로는 이 진입 잠금과 분리해 보유 위험을 줄일 수 있도록 합니다. 주문 가격은 서버 시세, 수량 상한은 서버가 설정하며 기존 위험 게이트에서 재검증합니다. 중복 방지를 위해 신호 봉 단위로 고정된 `Idempotency-Key`가 필요합니다. PAPER 기본 설정에서는 AI·계좌·주문 서비스에 도달하지 않습니다.

REAL 보호청산 감시 작업은 `KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED=true`라는 별도 잠금까지 열려야 작동합니다. 이 작업은 REAL 모드·실주문 잠금·보호청산 잠금·전체/REAL 계좌읽기 잠금·자격정보 구성·`KIS_REAL_RECONCILIATION_ENABLED=true` 여부를 모두 확인하고, KIS 계좌 수량과 매도 가능 수량이 영속 계획의 체결 수량과 정확히 같을 때만 최신 시세의 bid 가격으로 한 번 매도 의도를 선점합니다. 같은 종목에 계획이 겹치거나 보유 수량이 어긋나면 수동 확인 상태로 두며 자동 정정/재주문은 하지 않습니다. 재시작 시 기존 멱등 키를 사용하고, 이미 알려진 주문은 주문 대조 작업에 맡깁니다. 신규 REAL BUY API와 Python 신호 전송도 보호청산 자동화와 REAL 주문 대조가 모두 켜져 있어야 허용됩니다. 네 설정을 동시에 켜지 않으면 진입은 잠금 상태입니다. 기본값은 false이며, 현재는 CI Mock/PostgreSQL 테스트로 소프트웨어 동작을 확인했지만 실계좌 안전성 검증 완료로 간주하지 않습니다.

REAL 주문 API는 Spring이 `127.0.0.1:18080`에 바인딩된 로컬 운영만 대상으로 합니다. `TRADING_MODE=real`, `LIVE_TRADING_ENABLED=true`, `KIS_REAL_ORDERS_ENABLED=true`, `KIS_ACCOUNT_READ_ENABLED=true`, `KIS_REAL_ACCOUNT_READ_ENABLED=true`가 모두 설정되어야 POST 쓰기 경로가 KIS 계좌를 사전 조회합니다. PAPER 기본 설정에서는 HTTP 423으로 거부하고 계좌·주문 API를 호출하지 않습니다. 네트워크 타임아웃 등 결과가 불명확하면 같은 키로 다시 주문하지 말고 저장된 의도 조회와 재조회를 사용하세요. 실계좌 연결·실주문 검증은 아직 완료되지 않았습니다.

## Python 시세·AI API

`GET /api/v1/health`와 `GET /api/v1/paper/session`은 `kr_calendar_compatibility`를 제공합니다. `COMPATIBLE`은 `pandas<3.1` 및 `exchange-calendars>=4.13.1`을 뜻합니다. `PANDAS_UNSUPPORTED`, `EXCHANGE_CALENDAR_UNSUPPORTED`, `DEPENDENCIES_MISSING`, `VERSION_UNKNOWN`이면 PAPER 세션 게이트가 신규 진입과 청산 판단을 차단합니다. 이 필드는 시세 연결이나 주문 가능 여부를 뜻하지 않습니다.

Python health의 `broker_connected=true`는 모니터가 활성화되어 있고 구성된 모든 PAPER/REAL WebSocket feed가 `RUNNING`일 때만 반환합니다. 이는 연결 상태이며, 최신 tick이나 세션 전체의 데이터 완전성을 보증하지 않습니다.

`GET /api/v1/paper/session`의 `orderbook_quote_quality_by_feed`는 구독 종목과 일치해 파싱된 국내 호가를 feed별로 집계합니다. `usable`, `empty_or_nonpositive`, `crossed`는 호가값 분류이며 `stale_rejected`는 이미 처리 중인 분보다 오래되어 거부한 수로 다른 분류와 겹칠 수 있습니다. 이 카운터는 프로세스 재시작 시 초기화되고, 원시 프레임 수나 저장 분봉의 스프레드 포함률을 대신하지 않습니다.

같은 응답의 `last_stream_failure_stage`, `last_stream_error_category`, `last_stream_failure_at`, `last_stream_failure_environment`는 마지막 WebSocket 재연결 실패의 구간·안전한 오류 분류·UTC 발생시각·PAPER/REAL feed 환경을 제공합니다. 원시 예외 메시지, 프레임 내용, 계좌정보는 반환하지 않습니다. 진단은 feed가 다시 연결된 뒤에도 다음 실패 또는 Python 프로세스 재시작까지 보존됩니다.

운영 대시보드는 이 필드를 WebSocket 재연결 상태로 번역해 KST 발생 시각과 함께 표시합니다. 새 진단 필드가 없는 구버전 API 응답은 `진단 미지원`으로 구분합니다. 국내 감시 progress 기록에는 허용 목록에 있는 구간·분류 코드와 발생시각만 저장하고 예외 메시지는 저장하지 않습니다.

Python health의 `strategy_cost_assumptions`는 KR/US별 백테스트·학습 비용 설정 상태와 누락되거나 유효하지 않은 환경변수 이름만 반환합니다. 비용 숫자와 자격정보는 반환하지 않습니다. 왕복 라벨 비용이 매수·매도 수수료, 매도세금, 양쪽 체결 슬리피지 합보다 작으면 미준비로 표시합니다. 대시보드는 이 상태를 사용해 `.env_local`에서 확인할 항목을 보여주며, 이 진단 자체는 비용을 승인하거나 PAPER/REAL 주문을 활성화하지 않습니다.

`GET /api/v1/market/sessions/{market}`는 `market`이 `KR` 또는 `US`일 때 거래소 캘린더가 확인한 현지 세션 날짜와 UTC 개장·폐장 시각, 조회 시점의 장중 여부를 반환합니다. 휴장일에는 `is_session=false`와 빈 개장·폐장 시각을 돌려줍니다. 캘린더를 검증할 수 없으면 HTTP 503으로 닫습니다. 이 endpoint는 읽기 전용 세션 정보이며 주문 승인이 아닙니다.

Next.js `GET /api/operations`는 이 endpoint를 KR·US 각각 조회해 `marketSessions.KR` 및 `marketSessions.US`로 대시보드에 전달합니다. 한쪽 캘린더를 확인할 수 없으면 해당 값은 `null`이고 UI에는 "판정 불가"가 표시됩니다.

모델 `approve`, `activate`, `rollback` 변경 요청은 `.env`에 설정한 `MODEL_GOVERNANCE_TOKEN`을 `X-Model-Governance-Token` 헤더로 전달해야 합니다. 토큰 미설정 시 `503`, 불일치 시 `403`을 반환합니다. 토큰은 응답과 감사 기록에 저장되지 않습니다.

DB 접근 또는 트랜잭션 커밋 실패는 HTTP `503`과 일반 오류 메시지를 반환합니다. 응답에는 DB 드라이버 세부 내용이나 비밀값을 포함하지 않습니다. 클라이언트는 주문 완료로 간주하지 말고 idempotency key로 결과를 조회해야 합니다.

PAPER 장부 주문 정정은 동일 주문의 `Idempotency-Key`별로 한 번만 적용됩니다. 주문이 `PENDING` 또는 `PARTIALLY_FILLED`이고 새 수량이 기존 체결량보다 많으며 변경 후 주문금액이 시장별 한도 이하여야 합니다. 이전/변경 수량·가격은 `paper_order_amendment`에 보존됩니다. 이 API는 KIS 주문을 전송하지 않습니다.

KIS API는 기본 잠금(`KIS_ACCOUNT_READ_ENABLED=false`)입니다. 조회를 열려면 전체 스위치와 환경별 스위치를 모두 설정해야 합니다. REAL 조회에는 `KIS_REAL_ACCOUNT_READ_ENABLED=true`가 추가로 필요하며 기본값은 false입니다. 종목별 매수가능액 확인 시 `symbol`과 `unitPrice`를 함께 전달합니다. 미국 조회의 `exchange`는 PAPER·REAL 공통 지원 코드인 `NASD`, `NYSE`, `AMEX` 중 하나입니다. KIS 공식 예제의 REAL 전용 추가 코드 `NAS`는 현재 경로에서 허용하지 않습니다. 계좌 API는 계좌번호, App Key, App Secret을 응답에 포함하지 않습니다.

국내·미국 계좌 조회는 KIS 잔고 응답에 보유목록(`output1`)과 요약(`output2`)이 유효한 배열 또는 객체로 포함되어야 성공합니다. 보유 종목 행에 종목번호나 수량이 없거나 유효하지 않으면 전체 조회를 HTTP `502`로 거부하며, 부분 목록을 빈 계좌로 표시하지 않습니다. REAL 주문 위험검사는 보유수량이 양수인 종목에 평가금액이 없거나 음수인 계좌 스냅샷도 거부합니다.

기간손익 조회는 REAL 계좌에 한해 `/api/v1/kis/accounts/realized-pnl/real/KR?from=2026-11-02&to=2026-11-06` 또는 `.../US?...&exchange=NASD`를 사용합니다. 전체 계좌 읽기와 REAL 계좌 읽기 스위치가 모두 필요하며 요청은 GET만 수행하고 DB에 저장하지 않습니다. 31일 이내의 날짜만 허용합니다. KIS `output1` 상세와 `output2` 요약은 JSON 원문으로 반환합니다. 공식 예제는 컨테이너와 페이지 커서는 확인하지만 두 시장의 손익 필드 의미를 같은 기준으로 매핑하지 않으므로, 이 응답을 자동 위험 계산이나 확정 회계 값으로 취급하지 않습니다. 해외 기간손익은 KIS 안내상 매매일 다음 날부터 조회 가능하고 참고용 손익이며 양도소득세 계산과 다릅니다. 수수료 비용 포함 기준도 KIS 안내를 함께 확인해야 하므로, 자동화 손익과 실제 정산의 필드 단위 대조는 아직 미완료입니다.

## KIS REST 요청 범위

국내·미국 잔고 및 현재가 경로와 PAPER/REAL 거래 ID는 [KIS API compatibility](kis-api-compatibility.md)에 공식 예제 대조 기록과 함께 정리했습니다. 현재가는 REST 스냅샷이며, 실시간 체결 데이터는 별도 WebSocket 경로를 사용합니다. 해당 문서 검토는 계좌 인증 연동 성공을 의미하지 않습니다.

- 인증: OAuth client credentials `/oauth2/tokenP`
- 국내 잔고: `/uapi/domestic-stock/v1/trading/inquire-balance`
- 국내 종목 매수가능: `/uapi/domestic-stock/v1/trading/inquire-psbl-order`
- 미국 잔고: `/uapi/overseas-stock/v1/trading/inquire-balance`
- 미국 종목 매수가능: `/uapi/overseas-stock/v1/trading/inquire-psamount`

미국 잔고 응답은 공식 예제 구조에 따라 `output1`을 보유 종목, `output2`를 계좌 요약으로 처리합니다. 공개 샘플만으로 USD 현금 필드 의미를 확정할 수 없어 USD `cashBalance`는 `null`로 둡니다. 특정 종목의 매수가능액은 `inquire-psamount` 조회 결과만 사용합니다. 총자산 평가액은 현금이나 주문가능금액으로 사용하지 않습니다.

국내 잔고의 `cashBalance`는 KIS 응답 `dnca_tot_amt`(예수금총금액)만 사용합니다. `nass_amt`는 순자산금액이므로 현금 필드로 대체하지 않습니다. `dnca_tot_amt`가 없으면 `cashBalance`를 미확인(`null`)으로 유지합니다. 참고: [KIS 공식 잔고 응답 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py).

국내 잔고의 `accountEquity`는 같은 응답의 `nass_amt`(순자산금액)로 매핑하며, 현금과 별도 필드로 유지합니다. REAL 매수 위험 한도는 요청 문맥에서 전달한 자산 값 대신 KIS 계좌 스냅샷의 양수 `accountEquity`를 사용합니다. 미국 잔고 요약 필드의 공식 의미는 아직 검증되지 않아 `accountEquity`를 미확인으로 두며, 이 상태에서는 REAL 매수 위험 검사를 통과할 수 없습니다. KIS의 `asst_icdc_amt`(자산증감액)는 당일 손실 기준으로 사용하지 않습니다. 참고: [KIS 공식 국내 잔고 필드 매핑](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_balance/chk_inquire_balance.py).

REAL 신규 주문은 같은 시장의 미해결 KIS 주문 의도(`SUBMITTING`, `UNKNOWN`, `ACCEPTED`, `PARTIALLY_FILLED`, `CANCEL_PENDING`)가 DB에 있으면 거부됩니다. 조회는 주문 서버가 수행하므로 호출 문맥의 상태 표시만으로 이 검사를 우회할 수 없습니다.

REAL 국내 일일 손실률은 거래소 캘린더 API가 확인한 개장 후 3분 안에 기록한 불변 `nass_amt` 기준값과 최신 REAL KIS 잔고 스냅샷으로 계산합니다. 기준값은 `kis_real_daily_risk_baseline`에 세션 날짜·시장·자격정보 HMAC-SHA256 지문별 한 번 저장됩니다. 계좌번호·App Key·App Secret 원문은 기준선 테이블에 저장하지 않습니다. 자격정보가 바뀌면 과거 기준선을 재사용하지 않습니다. FastAPI 거래소 일정 조회 실패, 개장 기준값 부재, 오래된 계좌 스냅샷 또는 순자산 미확인 시 손실률을 미확인으로 처리해 REAL 매수 위험 검사를 통과시키지 않습니다. 미국 계좌 순자산 매핑은 확인되지 않아 미국 일일 손실 기준값을 저장하지 않습니다. `.env_local`에서 `KIS_REAL_ACCOUNT_READ_ENABLED=true`일 때만 개장 기준값 감시기가 REAL 읽기 전용 KIS 잔고를 조회합니다. 기본값은 false입니다.

Read timeout, HTTP 429 및 KIS rate-limit 오류는 안전한 오류 응답으로 바꾸고 요청을 자동 재전송하지 않습니다.

국내 과거 1분봉 조회는 KIS `inquire-time-dailychartprice`의 `FID_INPUT_DATE_1`·`FID_INPUT_HOUR_1`로 날짜와 커서를 지정하고 공식 샘플과 같이 `FID_PW_DATA_INCU_YN=Y`를 사용합니다. KIS 서버는 과거 분봉을 최대 1년 범위에서 보관한다고 안내하지만, 실제 보유 기간은 종목·서버 자료에 따라 더 짧을 수 있습니다. 이 REST 응답에는 과거 호가 스프레드가 없으므로 실시간 WebSocket 스프레드 자료와 섞어 비용 검증에 사용하지 않습니다. [KIS 국내 과거 분봉 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py).

Paper 주문 응답이 타임아웃되면 `GET /api/v1/orders/paper/by-idempotency/{key}`로 결과를 먼저 대조합니다. 자동화 클라이언트는 주문 쓰기를 재시도하지 않습니다.

## KIS WebSocket

`KIS_WEBSOCKET_ENABLED=true`와 선택한 `KIS_ENVIRONMENT` 자격증명이 필요합니다. 연결 뒤 아래 JSON 중 하나를 전송하면 국내 또는 해외 체결 데이터 한 종목을 릴레이합니다.

```json
{"market":"KR","symbol":"005930"}
{"market":"US","symbol":"AAPL","exchange":"NAS"}
```

시세 전용이며 주문·체결통보 구독은 노출하지 않습니다. 공식 샘플의 모의 31000/실전 21000 포트와 `H0STCNT0`/`HDFSCNT0` 구독 ID를 사용합니다. KIS가 `PINGPONG` 제어 메시지를 보낼 때 상류 WebSocket에 Pong으로 응답하며, PAPER 세션 감시기도 같은 처리를 합니다. 계정별 제공 데이터와 세션 제한은 실계정 연결 전에 해당 KIS 환경에서 검증해야 합니다.

## Quant backtest

`POST /api/v1/strategies/orb-vwap/backtest`는 클라이언트가 전달한 시간대 포함 1분봉 한 세션에 대해 ORB/VWAP/EMA/ATR·상대거래량·스프레드·거래대금 조건을 평가합니다. 요청에는 `buy_commission_bps`, `sell_commission_bps`, `sell_tax_bps`, `slippage_bps` 비용 가정이 모두 필수이며 기본 추정값은 없습니다. 진입은 신호 다음 봉의 시가부터 실행하고, 매수수수료·매도수수료·매도세금·슬리피지를 각각 비용으로 반영합니다. 같은 봉에서 손절과 목표가가 모두 닿으면 보수적으로 손절 우선 처리합니다. 데이터 공급·권리·정합성 및 비용 가정은 요청자가 책임지며 API는 저장하거나 주문하지 않습니다. 결과의 `executable`은 항상 `false`입니다.

## AI inference

LightGBM·XGBoost 후보는 `--market KR`과 `--market US`로 분리 학습하고 세션 날짜 기준 시간순 학습/검증/테스트로 나눕니다. 각 피처는 신호 시점까지의 데이터만 사용하고, 레이블은 미래 N개 봉 수익에서 왕복 비용 추정치를 차감해 계산합니다. 추론 요청의 시장은 모델 메타데이터 시장과 일치해야 합니다. 테스트 성능은 후보 검토 자료이며 전략 우위를 증명하지 않습니다. 모델·메타데이터는 `.local/models`에만 저장하고 모델 아티팩트와 입력 CSV SHA-256을 기록합니다. 새 모델은 승인 전 상태이며 예측 응답 `executable`은 항상 `false`입니다.

승인·활성화·롤백 API는 운영자와 사유를 요구하고 모델 상태 및 JSONL 변경 이력을 `.local/models`에 기록합니다. 이는 로컬 감사 편의 기록이며 파일 자체 변조를 막는 보안 감사 시스템은 아닙니다. 활성 모델 포인터는 예측 결과나 주문 실행 권한을 바꾸지 않습니다.

## Operations reporting

`scripts/export-daily-report.ps1` preserves the Spring operations summary at the report's top level and adds `reportSchemaVersion=11`, `generatedAt`, endpoint availability, `paperSession`, `aiModels`, `marketSessions`, per-symbol `marketDataQuality`, and Windows `localStackTask`, `sessionMonitorSchedule` and `validationTaskSchedule` summaries. It also includes the latest domestic PAPER session monitor result from the local progress log, with bounded safe fields only. The dashboard labels a bounded sample pass separately from a verified full-session pass. These summaries include enabled/state/next/last run metadata, `wakeToRun` and `startWhenAvailable` settings, and each task principal's logon type plus a boolean indicating whether it requires an interactive user logon. This exposes when local monitoring or the service stack will not start while the user is signed out; it does not change the Windows task configuration. The summaries use the Windows local `timeZoneId` and report safe unavailable or partial states without exposing principal identity, actions, or arguments. `optionalDataStatus` describes whether optional API endpoints responded; local stack task availability is separate, and the status does not certify complete market bars. `marketDataQualitySummary` separately reports `sessionCompleteness` and `spreadCoverage` as `COMPLETE`, `INCOMPLETE`, `UNVERIFIED`, or `UNAVAILABLE`, plus session counts. Session quality uses each market's latest closed official session and current watched symbols. Unavailable Python or quality data is represented explicitly (and `pythonConnected=false` when health is unavailable); the export does not call KIS or include API credentials, secrets, or account numbers. `-OutputPath` can direct a manual preview to a separate local file.

The Next.js dashboard's `/api/operations` reads the newest schema 10+ report from the repository `.local/reports` directory and returns only the local stack summary and allowlisted session-monitor/model-validation task fields. It does not return the report's account, cash, holdings, order, credential, or KIS response sections. When no valid local report is available, the dashboard shows the schedule state as unavailable instead of assuming tasks are healthy. A report older than 26 hours is visibly marked stale.

`GET /api/v1/market/training-readiness/{market}/{symbol}` reports the stored session count eligible for P4 model training over a bounded 90-day default window (or a caller-specified range of at most 121 calendar days). Only rows within the exchange's regular continuous session are evaluated. A session counts only when all expected regular-session bars are present, every bar has a spread, and the source count for every bar exactly matches the market's approved KIS WebSocket feed. Exported training provenance applies the same count check, so mixed-source sidecars and older sidecars without `source_counts` must be re-exported before training. Each session includes `ineligibility_reasons`, which may contain `incomplete_session`, `incomplete_spread_coverage`, and/or `unapproved_data_source`; eligible sessions return an empty list. The response includes the 13-session training minimum and how many eligible sessions are still needed. It reads PostgreSQL in a read-only transaction and never requests KIS data. The dashboard displays this status for symbols in registered P4 training tasks; missing API results remain unavailable.

Spring의 `/api/v1/operations/summary`는 paper 장부 주문·체결·현금·포지션과 KIS PAPER/REAL 주문 의도별 상태를 분리해 반환합니다. KIS 주문 view는 계좌번호, 멱등 키, 브로커 주문 식별자를 노출하지 않으며 최근 주문의 안전 응답 코드와 상태·체결량만 포함합니다. `paperKisOrderCounts`와 `recentPaperKisOrders`는 KIS 모의 주문 기록, `realOrderCounts`와 `recentRealOrders`는 KIS REAL 주문 의도 상태이며 실제 계좌 잔고를 뜻하지 않습니다. `kisRealOrdersEnabled`, `kisAccountReadsEnabled`, `kisPaperAccountReadsEnabled`, `kisRealAccountReadsEnabled`, `kisRealReconciliationEnabled`, `kisPaperCredentialsConfigured`, `kisRealCredentialsConfigured`는 운영 잠금 및 자격정보 형식 상태를 나타냅니다. 자격정보 플래그는 PAPER/REAL App Key·Secret 입력과 10자리 계좌번호 형식만 확인하며, KIS 인증 성공이나 계좌 접근 가능성을 검증하지 않습니다. 포지션 원가는 통화별 평균 취득원가이며 실시간 평가액이 아닙니다. `realizedPnlTodayGrossByCurrency`는 paper 실현 손익 총액이고, `realizedPnlTodayNetByCurrency`는 매수 비용을 평균원가에 반영하고 매도 비용을 차감합니다. `paperTransactionCostsTodayByCurrency`는 오늘 체결 건에 기록된 비용입니다. `PAPER_COST_MODEL_ENABLED=false`면 비용은 0으로 기록되어 총액과 순액이 같으므로, 비용률을 입력하고 설정을 켜기 전의 순손익은 비용 반영 수치가 아닙니다. PAPER 자동매매를 켜려면 Spring에도 같은 설정 파일이 적용되며 `PAPER_COST_MODEL_ENABLED=true`와 해당 시장의 양수 매수 수수료·매도 비용 가정이 필요합니다. 조건을 충족하지 못하면 Spring은 계속 기동하지만 신규 자동매수 주문과 그 체결 기록을 차단합니다. 보유 포지션의 매도·청산 처리는 계속 사용할 수 있습니다. 수수료·매도세 비용은 거래소 공시 기본 가정과 다를 수 있고, 이 PAPER 원가 모델은 슬리피지를 포함하지 않습니다. `paperPerformanceByInstrument`는 전략·AI 모델·시장·종목별 매도 주문 수, 승·패, 승률, 평균 이익·손실, 매도 주문당 기대손익, 손익비, 실현 손익 경로의 최대 하락액, 누적 순실현손익을 포함합니다. 자동 진입의 `strategyId`와 승인된 `modelId`를 PAPER 주문 및 손절·익절 상태에 저장하고 청산 주문으로 이어 붙입니다. 이 ID가 없는 과거·수동 주문은 `null`로 분류됩니다. 체결 원장을 평균원가로 재생하며 PAPER 장부 전용이므로 실계좌 수익이나 전략 우위나 평가손익을 뜻하지 않습니다. 손익비는 총이익/총손실이며 손실이 없으면 `null`입니다. 실현 최대 하락액은 해당 전략·모델 분류의 매도 주문 실현손익 경로만 반영합니다. `scripts/export-daily-report.ps1`은 이 요약을 `.local/reports`에 날짜별 JSON으로 저장합니다. 대시보드는 조회 전용이며 주문·취소 기능을 제공하지 않습니다.


Schema 6 also reports the local Windows Task Scheduler state for dated KR and US session monitor jobs. It includes task name, enabled/running state, next and last run times, and last result code; task query failures are reduced to an error type. A registered or scheduled monitor is not evidence that market data was collected or passed session quality.
