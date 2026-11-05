# AI Stock Trader — v1.0 개발

Windows 로컬에서 실행하는 국내·미국 주식 자동매매 프로젝트의 첫 개발 기반입니다.

## 브랜치 사용

- `main`은 GitHub 기본 브랜치이자 안정 기준 브랜치로 둡니다.
- 기능 변경은 `feature/Pn-...` 브랜치에서 작업하고 단위 커밋 후 PR로 `develop`에 통합합니다.
- 검증이 끝난 `develop`만 PR로 `main`에 반영합니다.
- 커밋 제목은 `Add : [업무명] 내용`, `Update : [업무명] 내용`, `Fix : [업무명] 내용` 형식을 사용합니다.

> **안전 범위:** 현재 KIS 연결은 읽기 전용이며 계좌조회는 기본 잠금 상태입니다. `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`를 유지합니다. P6의 별도 전환 승인 전까지 실주문을 활성화하지 않습니다.

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

- **P1 — 기반·KIS 읽기 전용 연결:** 완료. 국내·미국 시세, 국내·해외 잔고 스냅샷 API와 PostgreSQL 저장 구조를 추가했습니다. KIS 실제 연결은 로컬 `.env`에 각 환경 자격증명을 입력한 후 선택적으로 확인합니다.
- **P1 — 기반·KIS 읽기 전용 연결:** 진행 중. Python 시세 조회 기반과 DB 저장 구조가 있고, 이번 `feature/P1-001-kis-auth`에서 Java 환경별 OAuth·국내/미국 계좌·주문가능 금액·오류 처리 및 테스트를 완료합니다. 실제 paper 계좌 통합 테스트는 키 입력 후 별도 실행합니다.
- **P2 — 주문·리스크 엔진:** 기초 paper 상태·체결·현금·포지션 코드는 추가되어 있습니다. P1 통과 후 재시작 복구·장애 테스트를 마무리합니다.
- **P3 — 전략·백테스트:** VWAP/시초가 돌파 전략, 비용·슬리피지 모델, 과거 데이터 검증과 미래 데이터 누수 방지를 구현합니다.
- **P4 — AI 모델:** 피처 생성, LightGBM/XGBoost 후보 비교, 모델 버전·학습 분리 검증을 구현합니다.
- **P5 — 자동 모의매매:** 국내·미국 장 운영 시간, 신호·paper 주문 자동화, 체결 감시, 장 마감 처리와 장시간 안정성을 구현합니다.
- **P6 — 실거래 준비 코드:** 주문 어댑터·제한·감사·복구 검증을 별도로 구현합니다. 실주문 활성화는 별도의 명시적 승인 전까지 잠금 상태로 둡니다.
- **P7 — 운영 고도화:** 공시·뉴스, 전략 비교, 모델 재학습·롤백, 운영 보고서와 장애 시 자동 중단을 추가합니다.

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
