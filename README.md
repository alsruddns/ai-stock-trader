# AI Stock Trader — P1 기반

Windows 로컬에서 실행하는 국내·미국 주식 자동매매 프로젝트의 첫 개발 기반입니다.

## 브랜치 사용

- `main`은 GitHub 기본 브랜치이자 안정 기준 브랜치로 둡니다.
- 개발 변경과 푸시는 `develop`에서만 진행합니다.
- 기능 검토와 준비가 끝난 경우에만 `develop`을 `main`에 반영합니다.

> **안전 범위:** 주문 API는 모의 주문만 저장합니다. `LIVE_TRADING_ENABLED=true` 또는 `TRADING_MODE`를 `paper` 이외 값으로 설정하면 Spring Boot가 시작을 거부합니다. Python KIS 연동은 가격·계좌 조회 전용이며 주문 API는 구현하지 않았습니다. 계좌 조회는 기본 잠금 상태입니다.

## 구성

```text
backend/trading-api/     Java 21 + Spring Boot 주문·리스크 API
services/market-ai/      Python FastAPI 시세 분석 미리보기 API (읽기 전용)
backend/.../db/migration Flyway PostgreSQL 초기 스키마
compose.yaml             PostgreSQL 로컬 컨테이너
```

KIS 국내·미국 현재가 및 계좌 잔고 조회는 Python 서비스에서 읽기 전용으로 지원합니다. 가격 조회와 계좌 조회는 각각 기본 비활성화되어 있습니다. 계좌 조회는 국내/해외 주식 잔고 GET만 요청하며 계좌번호는 결과에서 제외합니다. REST 현재가 조회는 실시간 WebSocket 스트림이 아니며 응답 시각은 조회 시각입니다.

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

KIS 조회를 쓸 때는 `.env`의 해당 환경 키를 입력하세요. 모의와 실전 자격증명을 각각 `KIS_PAPER_*`, `KIS_REAL_*`에 넣습니다. 가격 조회에 `KIS_MARKET_DATA_ENABLED=true`가 필요하고, 계좌 잔고에는 별도로 `KIS_ACCOUNT_READ_ENABLED=true`가 필요합니다. 두 기능 모두 기본 `false`입니다. `KIS_ENVIRONMENT`는 가격 조회 환경을 선택합니다.

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

Paper 주문 미리보기:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/orders/paper `
  -Headers @{ 'Idempotency-Key' = 'd8d449d8-f154-4c5a-b1d0-6ce69f790f51' } `
  -ContentType 'application/json' `
  -Body '{"market":"KR","symbol":"005930","side":"BUY","quantity":1,"limitPrice":70000,"currency":"KRW"}'
```

미리보기 주문은 `SIMULATED`로만 PostgreSQL에 기록됩니다. 같은 요청을 재전송할 때는 같은 `Idempotency-Key`를 보내 중복 기록을 막으세요. 다른 주문에는 새 UUID를 사용합니다. Spring과 Python은 기본적으로 `127.0.0.1`에만 바인딩되며, 인증이 없는 로컬 개발용 API이므로 외부 네트워크에 노출하지 마세요.

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
- **P2 — 주문·리스크 엔진:** 다음 단계. 상태 전이, paper broker, 모의 체결, 보유 포지션·잔고 동기화, 중복·부분체결·타임아웃·재시작 복구를 구현합니다.
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
- [해외주식 잔고조회 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/overseas_stock/overseas_stock_functions.py)
- [해외주식 현재체결가 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/price/price.py)
- [Spring Boot 3.5 시스템 요구사항](https://docs.spring.io/spring-boot/3.5/system-requirements.html)
- [FastAPI 테스트 가이드](https://fastapi.tiangolo.com/tutorial/testing/)
