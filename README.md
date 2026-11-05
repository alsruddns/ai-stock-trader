# AI Stock Trader — P1 시작점

Windows 로컬에서 실행하는 국내·미국 주식 자동매매 프로젝트의 첫 개발 기반입니다.

## 브랜치 사용

- `main`은 GitHub 기본 브랜치이자 안정 기준 브랜치로 둡니다.
- 개발 변경과 푸시는 `develop`에서만 진행합니다.
- 기능 검토와 준비가 끝난 경우에만 `develop`을 `main`에 반영합니다.

> **P1 안전 범위:** 주문 API는 모의 주문만 저장합니다. `LIVE_TRADING_ENABLED=true` 또는 `TRADING_MODE`를 `paper` 이외 값으로 설정하면 Spring Boot가 시작을 거부합니다. Python 서비스의 KIS 연결은 기본 비활성인 읽기 전용 가격 조회뿐입니다. 이 저장소에는 실주문 어댑터가 없습니다.

## 구성

```text
backend/trading-api/     Java 21 + Spring Boot 주문·리스크 API
services/market-ai/      Python FastAPI 시세 분석 미리보기 API (읽기 전용)
backend/.../db/migration Flyway PostgreSQL 초기 스키마
compose.yaml             PostgreSQL 로컬 컨테이너
```

KIS 현재가 조회는 Python 서비스에서 읽기 전용으로 지원합니다. 기본값은 비활성화이며, 설정해도 국내·미국 가격 스냅샷 GET만 호출합니다. 주문, 잔고, 체결 내역 호출은 없습니다. REST 현재가 조회는 실시간 WebSocket 스트림이 아니며 응답 시각은 조회 시각으로만 기록합니다.

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

KIS 현재가 조회를 사용할 때만 `.env`에 모의투자 App Key와 App Secret을 입력하고 `KIS_MARKET_DATA_ENABLED=true`로 설정하세요. `KIS_ENVIRONMENT=paper`는 모의투자 도메인을 선택합니다. 계좌번호는 시세 조회에 필요하지 않습니다. 비활성 상태에서는 `/api/v1/market/quotes/...`가 KIS를 호출하지 않고 `503`을 반환합니다.

- Spring 상태: `http://localhost:8080/api/v1/health`
- Python 상태: `http://localhost:8000/api/v1/health`
- Python API 문서: `http://localhost:8000/docs`

KIS 모의 현재가 조회 예시 (선택 기능):

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/market/quotes/KR/005930
Invoke-RestMethod 'http://localhost:8000/api/v1/market/quotes/US/AAPL?exchange=NAS'
```

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
mvn test
cd ../../services/market-ai
python -m pip install -e ".[dev]"
python -m pytest
```

## 비밀정보 취급

- 실제 KIS App Key, App Secret, 계좌번호는 `.env` 또는 Windows 환경 변수에만 보관하고 절대 커밋하지 마세요.
- `.env`는 Git에서 제외됩니다. 키가 비어 있어도 P1은 정상 작동합니다.
- 모의투자 자격증명도 민감정보로 취급하세요. 로그나 이슈에 붙여 넣지 마세요.
- `.env.example`을 바꾸어 실제 키를 넣지 마세요.

Docker를 선호하면 기존 PostgreSQL 대신 컨테이너 DB도 선택할 수 있습니다. 이 경우 Docker Desktop을 켠 상태에서 `docker compose up -d postgres`를 실행하고, `.env`의 DB 접속 암호를 동일하게 사용하세요.

## 아직 필요한 개발

1. KIS 시세 인증과 국내/미국 시세 어댑터를 읽기 전용으로 구현하고 호출 제한·토큰 수명·장 운영시간을 확인합니다.
2. 데이터 품질·시장 캘린더·체결 및 환율 모델을 정한 뒤 paper broker와 주문 상태 동기화를 구현합니다.
3. 전략 검증, 포지션·일일 손실 한도, 킬 스위치, 감사기록, 재시작 복구와 모의 계좌 E2E 검증을 진행합니다.
4. 운영 접근제어·인증·관측성 및 별도의 보안 검토를 완료하기 전에는 로컬 개발 환경을 벗어나지 않습니다.
5. 실거래는 별도 구현, 검토와 명시적 승인 전까지 비활성 상태를 유지합니다.

## 공식 참고자료

- [KIS Developers API 문서 포털](https://apiportal.koreainvestment.com/)
- [한국투자증권 공식 Open Trading API 샘플 저장소](https://github.com/koreainvestment/open-trading-api)
- [국내주식 현금주문 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_cash/order_cash.py)
- [국내주식 현재가 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_price/inquire_price.py)
- [해외주식 현재체결가 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/price/price.py)
- [Spring Boot 3.5 시스템 요구사항](https://docs.spring.io/spring-boot/3.5/system-requirements.html)
- [FastAPI 테스트 가이드](https://fastapi.tiangolo.com/tutorial/testing/)
