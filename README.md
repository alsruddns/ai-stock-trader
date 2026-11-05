# AI Stock Trader — v1.0 개발

Windows 로컬에서 실행하는 국내·미국 주식 자동매매 프로젝트의 첫 개발 기반입니다.

## 브랜치 사용

- `main`은 GitHub 기본 브랜치이자 안정 기준 브랜치로 둡니다.
- 기능 변경은 `feature/Pn-...` 브랜치에서 작업하고 단위 커밋 후 PR로 `develop`에 통합합니다.
- 검증이 끝난 `develop`만 PR로 `main`에 반영합니다.
- 커밋 제목은 `Add : [업무명] 내용`, `Update : [업무명] 내용`, `Fix : [업무명] 내용` 형식을 사용합니다.

> **안전 범위:** 기본값은 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_ACCOUNT_READ_ENABLED=false`, `KIS_PAPER_ORDERS_ENABLED=false`입니다. P6에는 모의 주문 어댑터 코드가 있지만 별도 스위치가 꺼져 있고 실주문 경로는 제공하지 않습니다. 실계좌 주문은 비활성화 상태로 유지합니다.

## 구성

```text
backend/trading-api/     Java 21 + Spring Boot 주문·리스크 API
services/market-ai/      Python FastAPI 시세 분석 미리보기 API (읽기 전용)
backend/.../db/migration Flyway PostgreSQL 초기 스키마
compose.yaml             PostgreSQL 로컬 컨테이너
```

Python은 읽기 전용 국내·미국 가격 조회 기반을 제공합니다. Spring에서는 P1-001 KIS 인증·계좌 조회를 구현 중입니다. REST 현재가 조회는 실시간 WebSocket 스트림이 아니며 응답 시각은 조회 시각입니다.

## 준비

- Java 21
- Python 3.11 이상
- PostgreSQL server listening on `localhost:5432` (Docker is optional)
- 첫 실행에는 Maven Wrapper가 Maven을 내려받으므로 인터넷 연결

## Windows 시작

PowerShell에서 저장소 폴더로 이동합니다.

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env`에서 설치된 PostgreSQL `postgres` 사용자의 암호를 입력합니다. 앱 테이블을 기본 `postgres` DB에 섞지 않도록 새 데이터베이스 `ai_stock_trader`를 한 번 만듭니다. pgAdmin에서 서버에 연결해 Query Tool을 열고 다음 SQL을 실행해도 됩니다:

```sql
CREATE DATABASE ai_stock_trader;
```

PowerShell에서 아래 스크립트로 `.env`를 현재 창에 불러오고 Spring API를 실행합니다. 시작 시 Flyway가 `ai_stock_trader`에 초기 테이블을 만듭니다.

```powershell
.\scripts\start-local.ps1
```

운영 대시보드는 별도 PowerShell에서 다음 스크립트로 실행합니다. Next.js 화면은 로컬 Spring/Python API 상태, paper 현금·포지션·주문, 모델 승인 상태를 조회 전용으로 보여줍니다.

```powershell
.\scripts\start-dashboard.ps1
```

브라우저에서 `http://127.0.0.1:3000`을 엽니다. 일일 JSON 요약은 저장소 루트에서 `.\scripts\export-daily-report.ps1`을 실행하면 `.local/reports`에 저장됩니다. Windows 작업 스케줄러에 이 스크립트를 등록할 수 있습니다.

새 PowerShell 창에서 Python 분석 API를 실행합니다.

```powershell
cd services/market-ai
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file ../../.env
```

KIS 조회를 쓸 때는 `.env`에 모의·실전 자격증명을 각각 `KIS_PAPER_*`, `KIS_REAL_*`로 분리해 입력하세요. Python REST 시세는 `KIS_MARKET_DATA_ENABLED=true`, WebSocket 시세는 `KIS_WEBSOCKET_ENABLED=true`, Spring 계좌조회는 별도로 `KIS_ACCOUNT_READ_ENABLED=true`가 필요합니다. 모두 기본 `false`입니다. 계좌번호는 8자리 계좌번호와 2자리 상품코드를 붙인 총 10자리 형식으로 입력하세요. Spring 계좌 API는 URL의 `paper`/`real` 환경을 선택합니다.

- Spring 상태: `http://localhost:8080/api/v1/health`
- Python 상태: `http://localhost:8000/api/v1/health`
- Python API 문서: `http://localhost:8000/docs`
- 운영 대시보드: `http://127.0.0.1:3000`

LightGBM·XGBoost 학습용 선택 의존성은 `python -m pip install -e ".[dev,ai]"`로 설치합니다. 허가된 1분봉 CSV(`timestamp,open,high,low,close,volume,spread_bps`)로 모델 후보를 학습하려면 저장소 루트에서 아래 명령을 사용합니다. 각 행의 시각은 시간대를 포함해야 하고 데이터 제공·저장 권한을 확인한 자료만 사용하세요.

```powershell
python .\scripts\train-ai-models.py .\data\licensed\005930.csv --market KR
```

한국과 미국 데이터는 각각 `--market KR` 또는 `--market US`로 별도 학습합니다. 모델 파일과 메타데이터는 `.local/models`에만 저장됩니다. 생성된 모델은 자동 승인되지 않으며, 승인된 활성 모델도 예측 API는 `executable=false`를 반환합니다.

국내·미국 거래소 영업일 판정과 모의 운영 가드 테스트에는 `python -m pip install -e ".[dev,ai,ops]"`의 선택 `ops` 의존성이 필요합니다. `PAPER_AUTOTRADING_ENABLED=false`가 기본값이며, 시세 지연·모델/브로커 상태·일일 손실·연속 손실·보유 종목 수·장 마감 구간의 조건 중 하나라도 실패하면 신규 진입을 차단합니다.

KIS 모의 현재가 조회 예시 (선택 기능):

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/market/quotes/KR/005930
Invoke-RestMethod 'http://localhost:8000/api/v1/market/quotes/US/AAPL?exchange=NAS'
```

계좌 조회는 명시적으로 활성화한 후 환경과 시장을 URL에 지정합니다. 계좌번호는 KIS가 요구하는 8자리+2자리 총 10자리 형식으로 `.env`에 저장하세요. 조회 결과에는 계좌번호가 포함되지 않습니다.

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/accounts/paper/KR/balance
Invoke-RestMethod 'http://localhost:8000/api/v1/accounts/real/US/balance?exchange=NASD'
```

실전 계좌 경로는 읽기 전용 잔고 GET만 합니다. 실주문은 계속 잠겨 있으며 이 경로는 주문을 전송하지 않습니다.

Spring KIS 계좌 API 예시:

```powershell
Invoke-RestMethod http://localhost:8080/api/v1/kis/accounts/paper/KR/balance
Invoke-RestMethod 'http://localhost:8080/api/v1/kis/accounts/paper/US/balance?exchange=NASD'
Invoke-RestMethod 'http://localhost:8080/api/v1/kis/accounts/paper/KR/balance?symbol=005930&unitPrice=71000'
```

P1-001 구현이 완료되면 계좌 스냅샷은 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`일 때만 PostgreSQL에 저장합니다. 모의 KIS 읽기 전용 통합 테스트는 `.env` 자격증명 준비 후 `.\scripts\test-paper-kis.ps1`로 명시적으로 실행합니다. 기본 테스트에서는 외부 KIS에 연결하지 않습니다. API·DB·운영 상세는 [docs](docs/README.md)를 참고하세요.

Paper 주문 미리보기:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/orders/paper `
  -Headers @{ 'Idempotency-Key' = 'd8d449d8-f154-4c5a-b1d0-6ce69f790f51' } `
  -ContentType 'application/json' `
  -Body '{"market":"KR","symbol":"005930","side":"BUY","quantity":1,"limitPrice":70000,"currency":"KRW"}'
```

주문은 `PENDING` 상태로 PostgreSQL에 기록됩니다. 모의 체결은 별도 paper 전용 API로 직접 기록하며, 주문 수량보다 많이 체결시키거나 보유 수량보다 많이 매도하지 못합니다. 주문 요청은 같은 `Idempotency-Key`, 체결 이벤트는 같은 `fillKey`로 재전송 중복을 막습니다. Spring과 Python은 기본적으로 `127.0.0.1`에만 바인딩되며, 인증이 없는 로컬 개발용 API이므로 외부 네트워크에 노출하지 마세요.

수동 paper 체결과 가상 포지션 조회 예시:

매수 체결에는 가상 현금 잔고가 필요합니다. `.env`의 `PAPER_STARTING_CASH_KRW`와 `PAPER_STARTING_CASH_USD`에 직접 선택한 모의 시작 금액을 설정하세요. 기본값은 0이며, 기존 잔고는 재시작해도 설정값으로 덮어쓰지 않습니다. 가상 현금 조회는 `/api/v1/orders/paper/cash`입니다.
선택 사항인 `PAPER_ORDER_TIMEOUT_SECONDS`는 미체결/부분체결 주문을 만료시키는 시간입니다. 기본값 0은 자동 만료를 끕니다.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/orders/paper/1/fills `
  -ContentType 'application/json' `
  -Body '{"fillKey":"d4f78a48-1600-4c60-95a8-8cc53dfec06c","quantity":1,"fillPrice":70000}'
Invoke-RestMethod http://localhost:8080/api/v1/orders/paper/positions
Invoke-RestMethod -Method Post http://localhost:8080/api/v1/orders/paper/1/cancel
Invoke-RestMethod http://localhost:8080/api/v1/orders/paper/1
```

## 테스트

```powershell
cd backend/trading-api
.\mvnw.cmd test
cd ../../services/market-ai
python -m pip install -e ".[dev]"
python -m pytest
```

Spring Boot는 Maven으로 빌드합니다. 프로젝트에 포함된 `mvnw.cmd`를 사용하므로 Maven을 별도로 설치할 필요가 없습니다. Gradle 빌드 구성은 없습니다.

## 비밀정보 취급

- 실제 KIS App Key, App Secret, 계좌번호는 `.env` 또는 Windows 환경 변수에만 보관하고 절대 커밋하지 마세요.
- `.env`는 Git에서 제외됩니다. 키가 비어 있어도 P1은 정상 작동합니다.
- 모의투자 자격증명도 민감정보로 취급하세요. 로그나 이슈에 붙여 넣지 마세요.
- `.env.example`을 바꾸어 실제 키를 넣지 마세요.

Docker를 선호하면 기존 PostgreSQL 대신 컨테이너 DB도 선택할 수 있습니다. 이 경우 Docker Desktop을 켠 상태에서 `docker compose up -d postgres`를 실행하고, `.env`의 DB 접속 암호를 동일하게 사용하세요.

## 단계별 개발 순서

- **P1 — 기반·KIS 읽기 전용 연결:** REST 계좌 조회와 Python REST/WebSocket 시세 기반을 구현했습니다. Mock 검증은 통과했으며 `.env`에 paper 계정 자격정보를 설정한 뒤 실제 읽기 전용 스모크 테스트가 필요합니다.
- **P2 — 주문·리스크 엔진:** PostgreSQL paper 주문, 부분체결, 중복 체결 방지, 현금·포지션 정산 및 취소·만료를 구현했습니다. 재시작 복구 및 장애 시나리오를 추가 검증합니다.
- **P3 — 전략·백테스트:** 단일 세션 ORB/VWAP/EMA/ATR 전략, 비용·슬리피지, 미래 봉 미사용을 구현했습니다. 권한이 확인된 다중 세션 시장 데이터의 성능 재현은 남아 있습니다.
- **P4 — AI 모델:** LightGBM/XGBoost 학습·추론 후보, 시간 순서 분리, 해시 기반 모델 등록을 구현했습니다. 모델은 기본 미승인이고 결과는 주문 실행에 연결되지 않습니다.
- **P5 — 자동 모의매매:** 거래소 캘린더, 실시간 틱 집계, 신규 진입 위험 게이트, 로컬 paper 브로커 통신 경계와 ledger 기반 연속 손실 매도 주문 카운터를 구현했습니다. 추가한 opt-in 세션 모니터는 KIS paper WebSocket을 재연결하고 분봉을 수집하지만 주문을 생성하지 않습니다. 기본 비활성이고, 신호·위험상태·paper 체결을 연결하는 연속 세션 worker와 다중 세션 검증은 남아 있습니다.
- **P6 — KIS 모의 주문 준비:** 모의 지정가 주문·취소 어댑터, 계좌 잔고 사전확인, 영속 idempotency, 브로커 일별 주문·체결 재조회 및 부분체결·취소 상태 대조를 구현했습니다. `POST /api/v1/kis/orders/paper/{idempotencyKey}/reconcile`은 KIS 기록을 읽기만 합니다. `KIS_PAPER_ORDERS_ENABLED=false`가 기본이라 주문·취소는 잠겨 있고, timeout으로 주문번호를 받지 못한 UNKNOWN 결과는 잘못된 매칭을 막기 위해 자동 복구하지 않고 그대로 격리합니다. 실제 계좌 주문 경로는 없습니다.
- **P7 — 운영 고도화:** 조회 전용 Next.js 대시보드, 일일 JSON 운영 리포트, 통화별 paper 손익, 시장별 모델 분리와 검토 사유가 남는 승인·활성화·롤백 API를 구현했습니다. 대시보드에는 P5 시세 모니터의 연결·분봉 상태도 표시합니다. P1 실제 계좌 읽기, P5 자동 paper 주문과 연속 세션, 수수료 포함 손익 대조, 데이터 권한과 성능 검증은 완료 전이라 전체 운영 승인 기준은 아직 통과하지 않았습니다.

P7 모델 승인은 operator 이름, 검토 사유, holdout test 결과가 있는 모델 메타데이터를 남깁니다. 활성 모델 변경과 롤백은 `.local/models/governance-audit.jsonl`에 기록되며 예측은 계속 조언용입니다. 모델 승인 API와 운영 API는 로컬 개발용이며 운영체제 사용자 인증이나 원격 접근 제어를 제공하지 않습니다. 외부 네트워크에 노출하지 마세요.

## 공식 참고자료

- [KIS Developers API 문서 포털](https://apiportal.koreainvestment.com/)
- [한국투자증권 공식 Open Trading API 샘플 저장소](https://github.com/koreainvestment/open-trading-api)
- [국내주식 현금주문 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_cash/order_cash.py)
- [국내주식 현재가 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_price/inquire_price.py)
- [국내주식 잔고조회 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_balance/inquire_balance.py)
- [국내주식 매수가능 조회 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_order/inquire_psbl_order.py)
- [해외주식 매수가능금액 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py)
- [해외주식 잔고조회 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/overseas_stock/overseas_stock_functions.py)
- [해외주식 현재체결가 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/price/price.py)
- [Spring Boot 3.5 시스템 요구사항](https://docs.spring.io/spring-boot/3.5/system-requirements.html)
- [FastAPI 테스트 가이드](https://fastapi.tiangolo.com/tutorial/testing/)
