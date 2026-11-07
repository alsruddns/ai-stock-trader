# AI Stock Trader — v1.0 개발

Windows 로컬에서 실행하는 국내·미국 주식 자동매매 프로젝트의 첫 개발 기반입니다.

## 브랜치 사용

- `main`은 GitHub 기본 브랜치이자 안정 기준 브랜치로 둡니다.
- 현재 개발 작업은 사용자의 요청에 따라 `develop`에서만 수행합니다. `main`은 안정 기준으로 유지합니다.
- 커밋 제목은 `Add : [업무명] 내용`, `Update : [업무명] 내용`, `Fix : [업무명] 내용` 형식을 사용합니다.

> **안전 범위:** 기본값은 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_ACCOUNT_READ_ENABLED=false`, `KIS_PAPER_ACCOUNT_READ_ENABLED=false`, `KIS_REAL_ACCOUNT_READ_ENABLED=false`, `KIS_PAPER_ORDERS_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`, `KIS_REAL_RECONCILIATION_ENABLED=false`, `REAL_PROTECTIVE_EXIT_MANAGER_ENABLED=false`, `KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED=false`, `REAL_SIGNAL_SUBMISSION_ENABLED=false`입니다. 계좌 조회는 전체 허용 스위치와 해당 환경 허용 스위치가 모두 켜져야 합니다. 실계좌 신규 매수는 보호청산 관리기가 검증되기 전까지 별도 잠금 상태이며, 실계좌 주문도 기본 비활성입니다.

## 구성

```text
backend/trading-api/     Java 21 + Spring Boot 주문·리스크 API (Maven Wrapper)
services/market-ai/      Python FastAPI 시세·AI 분석 API
backend/.../db/migration Flyway PostgreSQL 스키마 변경
frontend/dashboard/      Next.js 조회 전용 운영 대시보드
```

Python은 국내·미국 KIS 시세·계좌 읽기 기반을 제공하고, Spring은 KIS 계좌조회와 paper 주문·위험 관리 API를 제공합니다. PAPER KIS 국내·미국 현재가 읽기는 통과했습니다. 2026-11-06 재등록한 PAPER 설정으로 국내·미국 잔고 GET 통합 테스트가 모두 통과했습니다. REST 현재가 조회는 실시간 WebSocket 스트림이 아니며 응답 시각은 조회 시각입니다.

## 준비

- Java 21
- Python 3.11 이상
- PostgreSQL server listening on `localhost:5432` (Windows 설치본 사용 가능, Docker 불필요)
- 첫 실행에는 Maven Wrapper가 Maven을 내려받으므로 인터넷 연결

## Windows 시작

PowerShell에서 저장소 폴더로 이동합니다.

```powershell
Copy-Item .env.example .env_local
notepad .env_local
```

`.env_local`에서 설치된 PostgreSQL `postgres` 사용자의 암호를 입력합니다. 현재 앱 데이터베이스는 `ai_stock_trader`입니다. 이 DB가 아직 없으면 pgAdmin에서 `localhost:5432`에 연결해 Query Tool에서 다음 SQL을 한 번 실행합니다:

```sql
CREATE DATABASE ai_stock_trader;
```

PowerShell에서 아래 스크립트로 `.env_local`을 현재 창에 불러오고 Spring API를 실행합니다. 시작 시 Flyway가 `ai_stock_trader`에 초기 테이블을 만듭니다.

```powershell
.\scripts\start-local.ps1
```

운영 대시보드는 별도 PowerShell에서 다음 스크립트로 실행합니다. Next.js 화면은 로컬 Spring/Python API 상태, paper 현금·포지션·주문, 모델 승인 상태를 조회 전용으로 보여줍니다.

```powershell
.\scripts\start-dashboard.ps1
```

브라우저에서 `http://127.0.0.1:3000`을 엽니다. 일일 JSON 요약은 `.\scripts\export-daily-report.ps1`로 수동 저장할 수 있습니다. 이 보고서는 Spring PAPER 운영 요약에 Python 상태, PAPER 세션, AI 모델 승인 상태, 거래소 세션, 최근 마감 세션 저장 품질을 추가합니다. 연결할 수 없는 자료는 `null`로 남기고 API 키·Secret·계좌번호는 포함하지 않습니다. 테스트 출력은 `-OutputPath '.local/reports/preview.json'`로 지정할 수 있습니다. 매일 오전 7시 30분(Windows 로컬 시간)에 자동 저장하려면 `.\scripts\install-daily-report-task.ps1`을 실행하세요. 다른 시간은 `-DailyTime "08:00"`처럼 지정할 수 있습니다. 작업은 현재 Windows 사용자가 로그인해 있고 Spring Boot가 실행 중일 때 동작하며, API 시작을 최대 5분 기다립니다. 실행 결과는 `.local/reports/daily-report-task.log`에서 확인할 수 있습니다. 예약을 제거하려면 `.\scripts\remove-daily-report-task.ps1`을 실행하세요.

Windows 로그인 때 Spring Boot와 Python 분석 API도 함께 시작하려면 `.\scripts\install-local-stack-task.ps1`을 실행하고 한 번만 `Start-ScheduledTask -TaskName AIStockTrader-LocalStack`으로 시작을 확인하세요. PostgreSQL이 먼저 실행 중이어야 합니다. 이 작업은 두 API를 `127.0.0.1`에 바인딩하고 프로세스 종료 시 재시작을 시도합니다. 로그는 `.local/logs`에 저장됩니다. 자동 시작 작업은 `.env_local`을 읽되 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`를 강제합니다. PAPER 주문과 읽기 전용 계좌 대조 스위치는 `.env_local` 값을 따릅니다. 작업 실행 중 `.env_local`이 변경되면 두 API를 정리하고 새 설정으로 재기동합니다. 설정 재로딩 확인은 `.\scripts\test-local-stack-env-reload.ps1`로 실행할 수 있습니다. 수동 API를 이미 실행 중이면 포트 충돌을 피하도록 먼저 종료한 뒤 자동 시작 작업을 실행하세요. 자동 시작을 해제하려면 `.\scripts\remove-local-stack-task.ps1`을 실행합니다. 현재 Windows 사용자가 로그인해 있고 PC가 깨어 있어야 합니다. 작업 실행 자체가 PAPER 자동매매를 켜지는 않습니다.

자동 API 작업을 코드 갱신 후 수동 재시작할 때는 `.\scripts\restart-local-stack-task.ps1`을 사용하세요. 이 명령은 최신 거래소 세션 응답으로 KR·US 모두 폐장했음을 확인하고, 해당 작업이 소유한 프로세스만 재기동합니다. 세션 정보가 오래됐거나 시장이 열려 있으면 아무것도 중단하지 않습니다. 재기동 뒤에도 실전 주문과 PAPER 자동매매 잠금이 적용됐는지 검사합니다. 게이트 회귀는 `.\scripts\test-local-stack-task-restart.ps1`로 확인할 수 있습니다.

새 PowerShell 창에서 Python 분석 API를 실행합니다.

```powershell
cd services/market-ai
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ai,ops]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --env-file ../../.env_local
```

KIS 조회를 쓸 때는 `.env_local`의 `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NUMBER`에 모의계좌 값을 넣습니다. 실전 값은 별도 `KIS_REAL_*` 항목에만 저장합니다. Python REST 시세는 `KIS_MARKET_DATA_ENABLED=true`, WebSocket 시세는 `KIS_WEBSOCKET_ENABLED=true`가 필요합니다. Spring PAPER 계좌조회는 전체 잠금 `KIS_ACCOUNT_READ_ENABLED=true`와 `KIS_PAPER_ACCOUNT_READ_ENABLED=true`를 모두 요구합니다. REAL 조회는 `KIS_REAL_ACCOUNT_READ_ENABLED=true`도 별도로 요구합니다. 계좌번호는 8자리 계좌번호와 2자리 상품코드를 붙인 총 10자리입니다. 일반 이름 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NUMBER`는 호환 설정이 아니므로 쓰지 않습니다. 인증정보는 Git에 추가하지 마세요. 계좌 조회는 읽기 전용이며 실주문 스위치는 계속 꺼둡니다.

국내 KIS 분봉 이력은 `GET /api/v1/market/bars/KR/{종목코드}?session_date=YYYY-MM-DD&end_time=HHMMSS`로 한 번에 최대 120개를 조회할 수 있습니다. 미국 최신 분봉 페이지는 `GET /api/v1/market/bars/US/{종목코드}?exchange=NAS|NYS|AMS`로 한 번에 최대 120개를 조회하며, 선택한 `session_date`를 지정하면 응답된 최신 페이지 안에서만 필터링합니다. KIS 해외 분봉 API는 날짜 직접 지정 방식이 아니라 cursor 방식이므로 이 API는 임의 과거 세션 조회를 보장하지 않습니다. 또한 KIS 공식 문서상 해외 분봉은 모의투자를 지원하지 않으므로 이 조회만 `KIS_ENVIRONMENT=real`과 REAL 시세 인증정보가 필요하며, 시세 조회 설정은 주문 권한을 열지 않습니다. 두 시장 모두 REST 분봉에는 과거 호가 스프레드가 없어 `spread_bps`는 null이며, 스프레드 필터를 요구하는 전략·학습 자료의 검증을 대신하지 않습니다. 이 경로는 읽기 전용이며 KIS REST 시세 스위치와 해당 환경의 API 인증이 필요합니다. `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`이면 공식 캘린더로 정규장 안에 있는 REST 이력과 WebSocket 집계 분봉만 `market_minute_bar`에 출처와 함께 저장합니다. monitor 재시작 시 같은 거래일의 정규장 시각만 복원해 지표 준비 상태를 이어가고, DB 복원에 실패하면 시세 monitor를 시작하지 않습니다. WebSocket 집계봉은 당시 호가 스프레드도 포함하며, PostgreSQL 쓰기 오류는 상태에 표시하고 paper 자동주문을 차단합니다. 동일 시각 재수집은 기존 행을 갱신합니다. 기본값은 `false`입니다.

KIS의 현재 공지 기준 PAPER는 초당 1건, REAL은 계좌당 초당 18건이며 동시 호출은 100~150ms 간격을 권고합니다. Python과 Spring은 PostgreSQL의 `kis_rest_rate_limit_slot`에서 환경과 앱키 지문별 요청 슬롯을 원자적으로 예약해 프로세스 간에도 호출 간격을 공유합니다. 앱키 원문은 저장하지 않고 SHA-256 지문만 저장합니다. 슬롯 확보에 실패하면 KIS 요청을 보내지 않습니다. `.env.example`의 Python 간격은 PAPER 1초, REAL 0.1초이며, 이보다 느리게만 조정할 수 있습니다. [KIS API 호출 유량 안내](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000001/post/d0d1a83f-6f8d-4437-9700-6d26702fd989)

- Spring 상태: `http://localhost:8080/api/v1/health`
- Python 상태: `http://localhost:8000/api/v1/health`
- Python API 문서: `http://localhost:8000/docs`
- 운영 대시보드: `http://127.0.0.1:3000`

LightGBM·XGBoost 학습용 선택 의존성은 `python -m pip install -e ".[dev,ai,ops]"`로 설치합니다. 허가된 단일 종목 1분봉 CSV(`timestamp,open,high,low,close,volume,spread_bps,symbol`)로 모델 후보를 학습하고 미사용 세션에서 AI+퀀트 및 퀀트 단독 전략 백테스트를 비교하려면 저장소 루트에서 아래 명령을 사용합니다. 각 행의 시각은 시간대를 포함해야 하고 데이터 제공·저장 권한을 확인한 자료만 사용하세요. 기본 label 왕복 비용은 20 bps, 백테스트 체결당 수수료와 슬리피지는 각각 5 bps이며 계좌별 실제 요율을 확인한 후 인자를 조정하세요. 모델 manifest에는 label 기간, 비용과 전략 필터 가정이 보존됩니다.

각 CSV는 같은 폴더에 `<파일명>.csv.provenance.json` 권리 확인 파일이 있어야 학습·백테스트를 실행할 수 있습니다. [권리 manifest 예시](docs/data-provenance-template.json)를 복사해 제공자, 원본·약관 참조, 수집·검토 시각, 로컬 저장·백테스트·학습 허용 여부를 확인해 작성하고, `dataset_sha256`에는 `Get-FileHash <CSV경로> -Algorithm SHA256`의 값을 넣습니다. KIS 공개 안내 자체에는 개인용 로컬 저장·백테스트·모델 학습의 세부 허용 여부가 모두 적혀 있지 않지만, 사용자는 2026-11-07 KIS의 서면 답변으로 세 목적이 모두 허용됨을 확인했다고 전달했습니다. 이 프로젝트는 해당 범위를 사용자 전달 확인으로 기록하며 본인 계좌 운용에만 적용하고 공개·제3자 제공은 허용하지 않습니다. [KIS 데이터 권리 확인 절차](docs/kis-market-data-rights.md)와 개별 manifest를 확인하고, KIS 답변 원문이나 세부 조건은 저장소에 포함하지 마세요.

퀀트 백테스트도 같은 권리 manifest가 필요하며, 예를 들어 `python .\scripts\backtest-quant.py .\data\licensed\005930.csv --market KR --symbol 005930`으로 실행합니다. CSV 내용이 바뀌어 SHA-256이 달라지면 기존 manifest는 사용할 수 없습니다.

### LEAN 로컬 전략 백테스트

`scripts/run-lean-backtest.ps1`은 권리 sidecar와 완전한 단일 세션을 검사하고, 공식 LEAN 엔진 소스와 .NET 10 SDK를 무시된 `.local/lean-toolchain`에 준비한 뒤 C# ORB/VWAP/EMA/ATR 전략을 로컬로 실행합니다. 이 Windows 네이티브 경로에는 Docker나 QuantConnect 유료 조직 계정이 필요하지 않습니다. KIS 데이터는 로컬 data folder만 사용하며 cloud로 업로드하지 않습니다. 첫 실행은 인터넷에서 Microsoft SDK 설치 스크립트, QuantConnect LEAN 소스, NuGet 패키지를 가져옵니다. 빌드·실행 기록은 `.local/logs`에 저장됩니다.

```powershell
.\scripts\run-lean-backtest.ps1 -CsvFile .\data\licensed\AAPL-2026-11-06.csv -Market US -Symbol AAPL -Session 2026-11-06
.\scripts\run-lean-backtest.ps1 -CsvFile .\data\backtests\kis-kr-005930-2026-10-30.csv -Market KR -Symbol 005930 -Session 2026-10-30
# 선택: 검증용 후보 모델의 AI 확률 필터 포함 재생
.\scripts\run-lean-backtest.ps1 -CsvFile .\data\licensed\AAPL-session.csv -Market US -Symbol AAPL -Session 2026-11-06 -ModelId xgboost-<32자리hex>
```

실행 도구는 Python 기준 백테스트와 LEAN의 봉 수·양자/AI 신호·거래 수·순손익을 비교하고 차이가 1센트를 넘으면 실패합니다. `-ModelId`를 주면 로컬 registry에서 현재 feature schema와 권리 provenance를 확인하고, 시점별 확률 파일의 해시와 세션 timestamp를 대조합니다. 검증된 현재 표본은 미국 AAPL 2026-11-06 390봉/9신호/9거래와 국내 005930 2026-10-30 380봉/0신호/0거래입니다. 미국 한 세션의 순손익 추정은 두 엔진에서 -$7.7139928212093으로 일치했습니다. 국내 자료는 과거 스프레드가 없어 거래가 생성되지 않았습니다. 이 소수 표본은 전략 성과를 입증하지 않으며, AI 모델을 포함한 LEAN 재생은 후보·완전한 세션 자료가 준비된 뒤 결과 검증이 필요합니다.

```powershell
python .\scripts\train-ai-models.py .\data\licensed\005930.csv --market KR --symbol 005930 --commission-bps 5 --slippage-bps 5
```


PostgreSQL에 저장된 분봉은 로컬 CSV로 내보낼 수 있습니다. 예: `.\services\market-ai\.venv\Scripts\python.exe .\scripts\export-market-data.py --market KR --symbol 005930 --from-date 2026-11-01 --to-date 2026-11-06 --output .\data\exports\005930.csv`. 날짜 범위는 거래소 현지 날짜 기준으로 양 끝을 포함합니다. 먼저 `--dry-run`을 추가하면 파일을 만들지 않고 전체 행·출처와 세션별 기대 봉 수, 봉 완전성, 스프레드 완전성을 확인합니다. 실제 내보내기 조회도 DB 읽기 전용이며 `data/`는 Git에서 제외됩니다. 모든 행 출처가 KIS Open API인 경우 sidecar는 사용자가 확인한 로컬 저장·백테스트·본인 계좌용 학습 권한과 개인 운용 제한을 SHA-256에 묶어 기록합니다. 타사 또는 혼합 출처가 포함되면 모든 권한을 `false`로 유지해 해당 제공자의 별도 검토를 요구합니다.

한국과 미국 데이터는 각각 `--market KR` 또는 `--market US`로 별도 학습합니다. 모델 파일과 메타데이터는 `.local/models`에만 저장됩니다. 생성된 모델은 자동 승인되지 않으며, 승인된 활성 모델도 예측 API는 `executable=false`를 반환합니다.

국내·미국 거래소 영업일 판정과 모의 운영 가드 테스트에는 `python -m pip install -e ".[dev,ai,ops]"`의 선택 `ops` 의존성이 필요합니다. `PAPER_AUTOTRADING_ENABLED=false`가 기본값이며, 시세 지연·모델/브로커 상태·일일 손실·연속 손실·보유 종목 수·장 마감 구간의 조건 중 하나라도 실패하면 신규 진입을 차단합니다. 자동매매를 켤 때는 당일 전략 분봉의 재시작 복구를 위해 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`도 필수입니다. 분봉 저장/복원 설정이 없으면 PAPER 자동매매 monitor가 시작되지 않습니다. 미국 paper 감시에는 KIS의 REAL WebSocket 시세 연결을 별도 `KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED=true`로 명시해야 합니다. 이 연결은 읽기 전용 미국 시세만 받아 paper 장부에 기록하며 REAL 주문 기능은 사용하지 않습니다.

KIS 모의 현재가 조회 예시 (선택 기능):

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/market/quotes/KR/005930
Invoke-RestMethod 'http://localhost:8000/api/v1/market/quotes/US/AAPL?exchange=NAS'
```

계좌 조회는 명시적으로 활성화한 후 환경과 시장을 URL에 지정합니다. 계좌번호는 KIS가 요구하는 8자리+2자리 총 10자리 형식으로 `.env_local`에 저장하세요. 조회 결과에는 계좌번호가 포함되지 않습니다.

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

P1-001 구현이 완료되면 계좌 스냅샷은 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`일 때만 PostgreSQL에 저장합니다. Spring 계좌 잔고 스모크는 `.\scripts\test-paper-kis.ps1`, Python FastAPI의 국내·미국 현재가 및 PAPER 잔고 읽기 전용 스모크는 `.\scripts\test-paper-kis-python.ps1`로 실행합니다. Python 스크립트는 `.env_local`에서 PAPER 키와 읽기 전용 스위치를 확인한 뒤 네 개 GET 경로만 호출하며, 주문 스위치가 켜져 있으면 중단합니다. 기본 테스트에서는 외부 KIS에 연결하지 않습니다. API·DB·운영 상세는 [docs](docs/README.md)를 참고하세요.

Paper 주문 미리보기:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/orders/paper `
  -Headers @{ 'Idempotency-Key' = 'd8d449d8-f154-4c5a-b1d0-6ce69f790f51' } `
  -ContentType 'application/json' `
  -Body '{"market":"KR","symbol":"005930","side":"BUY","quantity":1,"limitPrice":70000,"currency":"KRW"}'
```

주문은 `PENDING` 상태로 PostgreSQL에 기록됩니다. 모의 체결은 별도 paper 전용 API로 직접 기록하며, 주문 수량보다 많이 체결시키거나 보유 수량보다 많이 매도하지 못합니다. 주문 요청은 같은 `Idempotency-Key`, 체결 이벤트는 같은 `fillKey`로 재전송 중복을 막습니다. Spring과 Python은 기본적으로 `127.0.0.1`에만 바인딩되며, 인증이 없는 로컬 개발용 API이므로 외부 네트워크에 노출하지 마세요.

한국투자증권 PAPER 주문·취소를 실제 모의 계좌에서 확인할 때는 `scripts/test-kis-paper-order-lifecycle-isolated.ps1`을 사용하세요. 이 스크립트는 `.env_local`의 주문 잠금을 변경하지 않고 임시 Spring 프로세스에서 PAPER만 허용합니다. 먼저 `-PreflightOnly`로 읽기 전용 확인을 하세요. 국내 모의 취소 가능수량 조회의 KIS 지원이 확인되지 않아 국내 `-ConfirmPaperOrderAndCancel`은 주문 전 차단됩니다. 미국은 주문가능 USD가 충분할 때만 장중 주문·취소 확인이 가능합니다. 지원되는 모의 주문도 PAPER 계좌 상태를 바꾸며 시세 급변 시 체결될 수 있으므로 체결된 가상 포지션을 정리하세요. 세부 조건은 [Windows 운영 문서](docs/operations.md)를 참고하세요.

수동 paper 체결과 가상 포지션 조회 예시:

매수 체결에는 가상 현금 잔고가 필요합니다. `.env_local`의 `PAPER_STARTING_CASH_KRW`와 `PAPER_STARTING_CASH_USD`에 직접 선택한 모의 시작 금액을 설정하세요. 기본값은 0이며, 기존 잔고는 재시작해도 설정값으로 덮어쓰지 않습니다. 가상 현금 조회는 `/api/v1/orders/paper/cash`입니다.
선택 사항인 `PAPER_ORDER_TIMEOUT_SECONDS`는 미체결/부분체결 주문을 만료시키는 시간입니다. 기본값 0은 자동 만료를 끕니다.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/orders/paper/1/fills `
  -ContentType 'application/json' `
  -Body '{"fillKey":"d4f78a48-1600-4c60-95a8-8cc53dfec06c","quantity":1,"fillPrice":70000}'
Invoke-RestMethod http://localhost:8080/api/v1/orders/paper/positions
Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/v1/orders/paper/1/amend `
  -Headers @{ 'Idempotency-Key' = 'd3952b31-e893-48ce-bb5e-a621d1c513c4' } `
  -ContentType 'application/json' `
  -Body '{"quantity":2,"limitPrice":71000}'
Invoke-RestMethod -Method Post http://localhost:8080/api/v1/orders/paper/1/cancel
Invoke-RestMethod http://localhost:8080/api/v1/orders/paper/1
```

## 테스트

```powershell
cd backend/trading-api
.\mvnw.cmd test
cd ../../services/market-ai
python -m pip install -e ".[dev,ai,ops]"
python -m pytest
```

Spring Boot는 Maven으로 빌드합니다. 프로젝트에 포함된 `mvnw.cmd`를 사용하므로 Maven을 별도로 설치할 필요가 없습니다. Gradle 빌드 구성은 없습니다.

## 비밀정보 취급

- 실제 KIS App Key, App Secret, 계좌번호는 `.env_local`에만 보관하고 절대 커밋하지 마세요.
- `.env_local`은 Git에서 제외됩니다. 키가 비어 있으면 앱의 paper/mock 기능만 사용할 수 있으며, 실제 KIS 계좌 조회와 P1 완료 검증은 수행되지 않습니다.
- 모의투자 자격증명도 민감정보로 취급하세요. 로그나 이슈에 붙여 넣지 마세요.
- `.env.example`을 바꾸어 실제 키를 넣지 마세요.

Docker를 선호하면 기존 PostgreSQL 대신 컨테이너 DB도 선택할 수 있습니다. 이 경우 Docker Desktop을 켠 상태에서 `docker compose up -d postgres`를 실행하고, `.env_local`의 DB 접속 암호를 동일하게 사용하세요.

## 단계별 개발 순서

- **P1 — 기반·KIS 읽기 전용 연결:** Spring REST 계좌 조회와 Python REST/WebSocket 시세 연결 및 Mock 검증이 구현되어 있습니다. 2026-11-07 16:25–16:27 KST의 일회성 읽기 전용 통합 점검에서 Spring PAPER 국내·미국 잔고와 KRW/USD 주문가능액, Python 국내·미국 시세·잔고, Spring REAL 국내·미국 잔고 GET이 통과했습니다. 같은 날 앞선 점검에서 `EGW00002`가 발생했으나 이후 재발하지 않았습니다. 이 결과는 점검 시점의 읽기 요청 성공이며 상시 연결 상태를 뜻하지 않습니다. 별도 로컬 운영 상태 점검에서는 `broker_connected=false`가 관찰됐습니다. P1 구현과 읽기 전용 연결 점검은 완료됐으며, KIS 서버 오류 재발 여부와 지속 운영 상태는 계속 관찰합니다. REAL 조회와 모든 주문은 기본 잠금 상태입니다.
- **P2 — 주문·리스크 엔진:** PostgreSQL PAPER 주문, 부분체결, 중복 체결 방지, 현금·포지션 정산, 정정·취소·만료와 재시작 복구가 있습니다. 정정 내역은 별도 이력 테이블에 저장하며 같은 키의 반복 적용을 차단합니다. 로컬 PostgreSQL 통합 및 DB 장애 복구 검증을 통과했습니다. KIS 주문 없이 paper ledger에서 확인한 결과입니다.
- **P3 — 전략·백테스트:** 단일 세션 ORB/VWAP/EMA/ATR 전략, 비용·슬리피지, 미래 봉 미사용을 구현했습니다. 공식 KR·US 거래소 캘린더로 정규장 시각을 검증하고, 개장부터 폐장 직전까지 끊김 없는 1분봉 전체 세션만 백테스트·학습에 사용합니다. 장전·장후, 휴장일, 개장 누락, 분봉 공백, 세션 끝 누락은 거부합니다. KIS 국내 과거 분봉 및 미국 최신 분봉 페이지의 읽기 전용 수집을 지원합니다. 공식 LEAN 엔진 네이티브 로컬 실행과 Python 결과 대조는 KR·US 각 한 세션에서 통과했습니다. 사용자는 KIS 데이터의 로컬 저장·백테스트·학습 허용을 확인했습니다. 표본은 성능을 입증하지 않으며 다중 세션 검증은 남아 있습니다.
- **P4 — AI 모델:** LightGBM/XGBoost 학습·추론 후보, 시간 순서 분리, 해시와 출처·권리 확인 manifest 기반 모델 등록을 구현했습니다. PAPER 주문 작업이 꺼져 있어도 읽기 전용 WebSocket 분봉을 PostgreSQL에 저장하도록 수집 경로를 수정했습니다. 미사용 세션에서 실제 P5 신호와 같은 ORB/VWAP 통과 AND AI 통과 조건을 P3 체결·손절·익절·비용 백테스트에 적용해 퀀트 단독 결과와 비교합니다. 별도로 양성률과 평균 전방 순수익률(bps)도 보이지만 이는 겹칠 수 있는 표본별 레이블 통계이며 실제 체결 성과가 아닙니다. 세션별 1주 독립 시뮬레이션은 계좌 비용·세금·시장충격이나 복리 투자 성과를 증명하지 않습니다. 권리 manifest가 없거나 CSV 해시가 다르면 학습·백테스트가 거부되고 해당 모델은 승인·활성화되지 않습니다. 모델은 기본 미승인입니다. P5는 승인·활성화된 시장 모델이 퀀트 조건과 위험 게이트를 통과할 때만 local paper 후보로 사용합니다.
- **P5 — 자동 모의매매:** 거래소 캘린더, 실시간 틱 집계, 신규 진입 위험 게이트, 위험 예산·종목 비중·현금·주문 한도를 함께 적용하는 수량 계산, 로컬 paper 브로커 통신 경계와 ledger 기반 연속 손실 매도 주문 카운터를 구현했습니다. opt-in 세션 worker는 장 초반에 통화별 자산 기준선을 고정하고, 닫힌 세션별 분봉의 ORB/VWAP와 해당 시장의 활성·승인 AI 모델을 확인합니다. 신규 paper 매수는 일손실 1%, 연속 손실 3회, 총 보유 3종목, 최신 시세, 미체결 주문 여부와 포지션 크기 검사를 모두 통과할 때만 로컬 Spring paper ledger에 기록합니다. 보유 포지션은 영속 손절·익절과 장 마감 청산으로 관리합니다. 기본 스위치는 잠금 상태이며 연속 세션 복구와 모의 운영 검증이 남아 있습니다.
- 일일 기준선은 Spring paper API에서 거래일·통화별 최초 값으로 고정되며 동일 값 재요청만 허용합니다. KRW와 USD 평가액은 환산 없이 각각 계산합니다. 장 시작 3분 안에 기준선을 저장하지 못하거나 보유 종목 시세가 빠지거나 지연되면 해당 통화 신규 진입을 차단합니다. 모든 신규 진입은 `PAPER_AUTOTRADING_ENABLED=true`와 paper WebSocket 설정까지 명시적으로 켜야 하며 KIS 주문 API와 연결되지 않습니다.
- **P6 — KIS 주문 엔진 준비:** KIS paper 주문·취소, 계좌 사전확인, 영속 멱등성, 일별 체결 재조회와 PAPER/REAL 주문 의도를 구현했습니다. REAL API 진입점은 `/api/v1/kis/orders/real`에 연결했으며 REAL 모드, 주문 잠금, 전체·REAL 계좌 읽기 스위치를 모두 요구하고 DB 선기록, 중복 재전송 차단 및 체결 대조를 사용합니다. 확정된 REAL 매수·매도 체결과 세션 시작 보유원가에서 KRW/USD별 순실현손익·연속손실을 재구성하고 현재 양 시장 KIS 잔고와 장부 수량을 대조합니다. 계좌별 비용률, 양 시장 세션 기준선, 완결 주문 이력 또는 현재 잔고가 누락·불일치하면 매수를 닫습니다. 실제 KIS 계좌 연결·주문은 아직 검증되지 않았고 모든 REAL 주문 잠금은 기본 비활성입니다.

실제 KIS PAPER 주문·취소 통합 실행은 별도 opt-in 스크립트로 준비되어 있습니다. 현재 `KIS_PAPER_ORDERS_ENABLED=false`이므로 모의 브로커 주문도 아직 보내지 않았으며, 실행 절차와 제한은 [Windows 운영 문서](docs/operations.md)에 있습니다.
- **P7 — 운영 고도화:** 조회 전용 Next.js 대시보드, 일일 JSON 운영 리포트, paper 총액/비용후 손익, 오늘 체결비용, 시장별 모델 승인·활성화·롤백 기록을 구현했습니다. 대시보드에서 PAPER 장부와 REAL 주문 의도를 분리해 보고, 국내 PAPER와 미국 REAL 읽기 전용 시세 feed의 연결 상태, 주문 잠금·자동 대조 스위치·미확인 REAL 건수를 확인합니다. 보호청산 준비·자동 보호매도 스위치도 표시하고, 계좌읽기·체결대조·자격정보까지 모두 갖춰져야 신규 REAL 진입 게이트가 열린다고 나타냅니다. 수수료 모델은 `.env_local`에서 실제 계좌의 비용률을 설정한 후 켜야 하며 기본 비활성입니다. 연속 paper 세션, 실제 KIS 계좌·체결비용 대조, 데이터 권리와 모델 성능 검증이 끝나기 전까지 전체 운영 승인 기준은 통과하지 않습니다.

P7 모델 승인은 `MODEL_GOVERNANCE_TOKEN`에 32자 이상 무작위 비밀값을 설정하고 요청 헤더 `X-Model-Governance-Token`에 전달해야 합니다. 토큰이 없거나 일치하지 않으면 승인·활성화·롤백 변경은 거부됩니다. 승인은 최소 5개 서로 다른 held-out 세션과 5개 AI+퀀트 백테스트 체결을 요구하며, 그 순손익이 양수이고 같은 세션의 퀀트 단독 기준선보다 커야 합니다. operator 이름, 검토 사유, 데이터·모델 해시와 held-out 전략 백테스트 결과를 감사 로그에 남깁니다. 활성 모델 변경과 롤백은 `.local/models/governance-audit.jsonl`에 기록됩니다. 이 토큰은 로컬 변경 잠금이며 OS 사용자별 인증을 대신하지 않습니다. API를 외부 네트워크에 노출하지 마세요.

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
