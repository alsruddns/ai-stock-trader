# AI Stock Trader — v1.0 개발

Windows 로컬에서 실행하는 국내·미국 주식 자동매매 프로젝트의 첫 개발 기반입니다.

## 브랜치 사용

- `main`은 GitHub 기본 브랜치이자 안정 기준 브랜치로 둡니다.
- 현재 개발 작업은 사용자의 요청에 따라 `develop`에서만 수행합니다. `main`은 안정 기준으로 유지합니다.
- 커밋 제목은 `Add : [업무명] 내용`, `Update : [업무명] 내용`, `Fix : [업무명] 내용` 형식을 사용합니다.

> **안전 범위:** 기본값은 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_ACCOUNT_READ_ENABLED=false`, `KIS_PAPER_ACCOUNT_READ_ENABLED=false`, `KIS_REAL_ACCOUNT_READ_ENABLED=false`, `KIS_PAPER_ORDERS_ENABLED=false`, `KIS_PAPER_RECONCILIATION_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`, `KIS_REAL_RECONCILIATION_ENABLED=false`, `REAL_PROTECTIVE_EXIT_MANAGER_ENABLED=false`, `KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED=false`, `REAL_SIGNAL_SUBMISSION_ENABLED=false`입니다. 계좌 조회는 전체 허용 스위치와 해당 환경 허용 스위치가 모두 켜져야 합니다. 실계좌 신규 매수는 보호청산 관리기가 검증되기 전까지 별도 잠금 상태이며, 실계좌 주문도 기본 비활성입니다.

> **현재 시장 범위:** 국내·미국 공통 코드는 계속 개발하지만 실행하는 기능 테스트·KIS 연동·시장 세션 검증·PAPER/REAL 운용·데이터 백테스트 및 학습 검증은 국내(KR)만 대상으로 합니다. 미국 계좌나 미국 세션에 연결하는 검증은 하지 않습니다. 국내 전용 운영 probe와 작업 등록 가드가 적용되어 있으며, 일반 US API 경로도 현재 호출하지 않습니다.

국내 KIS PAPER 주문 상태를 주기적으로 대조하려면 `KIS_PAPER_RECONCILIATION_ENABLED=true`와 전체/PAPER 계좌 읽기 스위치를 모두 명시해야 합니다. 이 작업은 허용된 국내 운영 범위의 PAPER 주문만 페이지별 최대 50건씩 순환 조회하고, 알려진 브로커 주문번호가 있는 주문만 읽기 전용 KIS 일별 체결조회로 대조합니다. 주문번호가 없는 UNKNOWN 주문은 건너뜁니다. 페이지 순환으로 처음 50건을 넘는 주문도 차례로 확인합니다. PAPER 주문 제출 스위치와는 분리되어 있고 기본 비활성입니다.

## 구성

```text
backend/trading-api/     Java 21 + Spring Boot 주문·리스크 API (Maven Wrapper)
services/market-ai/      Python FastAPI 시세·AI 분석 API
backend/.../db/migration Flyway PostgreSQL 스키마 변경
frontend/dashboard/      Next.js 조회 전용 운영 대시보드
```

Python은 국내·미국 KIS 시세·계좌 읽기 기반을 제공하고, Spring은 KIS 계좌조회와 paper 주문·위험 관리 API를 제공합니다. 2026-11-06의 국내·미국 PAPER 시세·잔고 결과는 당시의 이력입니다. 2026-11-09 이후 새 KIS 연동·시장·매매 검증은 국내만 수행합니다. REST 현재가 조회는 실시간 WebSocket 스트림이 아니며 응답 시각은 조회 시각입니다.

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

`.env_local`은 Git에서 제외되는 로컬 파일이며 커밋하지 않습니다. 커밋 전 `.\scripts\test-tracked-credentials.ps1`로 추적 중인 파일의 KIS 키·시크릿·계좌번호 설정과 환경 파일 추가 여부를 검사할 수 있습니다. 값은 출력하지 않으며, 같은 검사는 `develop`에 푸시할 때 Windows CI에서도 실행됩니다.

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

브라우저에서 `http://127.0.0.1:13000`을 엽니다. 일일 JSON 요약은 `.\scripts\export-daily-report.ps1`로 수동 저장할 수 있습니다. 이 보고서는 Spring PAPER 운영 요약에 Python 상태, PAPER 세션, AI 모델 승인 상태, 거래소 세션, 최근 마감 세션 저장 품질과 Windows 세션 수집 작업과 PAPER 주문 수명주기·P4 학습/홀드아웃 예약의 등록·실행 상태와 Windows 시간대 ID를 추가합니다. 연결할 수 없는 자료는 `null`로 남기고 API 키·Secret·계좌번호는 포함하지 않습니다. 테스트 출력은 `-OutputPath '.local/reports/preview.json'`로 지정할 수 있습니다. 매일 오전 7시 30분(Windows 로컬 시간)에 자동 저장하려면 `.\scripts\install-daily-report-task.ps1`을 실행하세요. 다른 시간은 `-DailyTime "08:00"`처럼 지정할 수 있습니다. 작업은 현재 Windows 사용자가 로그인해 있고 Spring Boot가 실행 중일 때 동작하며, API 시작을 최대 5분 기다립니다. 실행 결과는 `.local/reports/daily-report-task.log`에서 확인할 수 있습니다. 예약을 제거하려면 `.\scripts\remove-daily-report-task.ps1`을 실행하세요.

Windows 로그인 때 조회 전용 Dashboard를 자동으로 시작하려면 먼저 Spring/FastAPI 로컬 스택과 Dashboard production build를 준비한 다음 `.\scripts\install-dashboard-task.ps1`을 실행하세요. 예약 작업은 현재 사용자 로그인 때만 실행되고, 127.0.0.1:13000 포트가 비어 있는지 확인한 뒤 Spring과 FastAPI가 PAPER 및 실주문 잠금을 보고할 때만 Dashboard를 시작합니다. 포트가 사용 중이거나 잠금 상태를 확인할 수 없으면 기존 프로세스를 건드리지 않고 로그에 실패를 남깁니다. 상태와 로그는 `.local/logs/dashboard-task-pid.json`, `.local/logs/dashboard-task.log` 및 Dashboard 표준 출력·오류 로그에 기록됩니다. 자동 시작을 해제하고 이 작업이 시작한 Dashboard만 정리하려면 `.\scripts\remove-dashboard-task.ps1`을 실행하세요. 수동 실행은 기존 `.\scripts\start-dashboard.ps1` 방식도 사용할 수 있습니다.

Windows 로그인 때 Spring Boot와 Python 분석 API도 함께 시작하려면 `.\scripts\install-local-stack-task.ps1`을 실행하고 한 번만 `Start-ScheduledTask -TaskName AIStockTrader-LocalStack`으로 시작을 확인하세요. PostgreSQL이 먼저 실행 중이어야 합니다. 이 작업은 두 API를 `127.0.0.1`에 바인딩하고 프로세스 종료 시 재시작을 시도합니다. 로그는 `.local/logs`에 저장됩니다. 자동 시작 작업은 `.env_local`을 읽되 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`를 강제합니다. PAPER 주문과 읽기 전용 계좌 대조 스위치는 `.env_local` 값을 따릅니다. 작업 실행 중 `.env_local`이 변경되면 현재 운영 범위가 유지되는 동안 해당 시장의 폐장을 확인한 뒤 새 설정을 적용합니다. 운영 시장 범위 자체가 바뀌면 자동 재기동을 보류해 의도치 않은 해외 연결을 막습니다. 세션 응답이 없거나 오래되면 재기동을 미룹니다. 설정 재로딩 확인은 `.\scripts\test-local-stack-env-reload.ps1`로 실행할 수 있습니다. 수동 API를 이미 실행 중이면 포트 충돌을 피하도록 먼저 종료한 뒤 자동 시작 작업을 실행하세요. 자동 시작을 해제하려면 `.\scripts\remove-local-stack-task.ps1`을 실행합니다. 현재 Windows 사용자가 로그인해 있고 PC가 깨어 있어야 합니다. 작업 실행 자체가 PAPER 자동매매를 켜지는 않습니다.

자동 API 작업을 코드 갱신 후 수동 재시작할 때는 `.\scripts\restart-local-stack-task.ps1`을 사용하세요. 현재처럼 `.env_local`의 `MARKET_OPERATIONAL_SCOPE=KR`이고 미국 feed가 꺼져 있을 때는 장중 국내 감시 종료 후 `-DomesticOnly -Confirm:$false`를 지정해 KRX 폐장만 확인합니다. 미국 운영 범위가 설정된 환경에서는 기본 동작을 사용해 모든 활성 거래소의 폐장을 요구하세요. 세션 정보가 오래됐거나 해당 시장이 열려 있으면 아무것도 중단하지 않습니다. 재기동 뒤에도 실전 주문과 PAPER 자동매매 잠금이 적용됐는지 검사합니다. 게이트 회귀는 `.\scripts\test-local-stack-task-restart.ps1`로 확인할 수 있습니다.

새 PowerShell 창에서 Python 분석 API를 실행합니다.

```powershell
cd services/market-ai
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,ai,ops]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 18000 --env-file ../../.env_local
```

KIS 조회를 쓸 때는 `.env_local`의 `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NUMBER`에 모의계좌 값을 넣습니다. 실전 값은 별도 `KIS_REAL_*` 항목에만 저장합니다. PAPER REST 시세는 `KIS_MARKET_DATA_ENABLED=true`가 필요합니다. REAL 미국 REST 시세·분봉용 코드와 설정은 유지하지만 현재 국내 전용 검증 범위에서는 활성화하지 않습니다. WebSocket 시세는 `KIS_WEBSOCKET_ENABLED=true`가 필요합니다. Spring PAPER 계좌조회는 전체 잠금 `KIS_ACCOUNT_READ_ENABLED=true`와 `KIS_PAPER_ACCOUNT_READ_ENABLED=true`를 모두 요구합니다. REAL 조회는 `KIS_REAL_ACCOUNT_READ_ENABLED=true`도 별도로 요구합니다. 계좌번호는 8자리 계좌번호와 2자리 상품코드를 붙인 총 10자리입니다. 일반 이름 `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NUMBER`는 호환 설정이 아니므로 쓰지 않습니다. 인증정보는 Git에 추가하지 마세요. 계좌 조회는 읽기 전용이며 실주문 스위치는 계속 꺼둡니다.

국내 KIS 분봉 이력은 `GET /api/v1/market/bars/KR/{종목코드}?session_date=YYYY-MM-DD&end_time=HHMMSS`로 한 번에 최대 120개, 전체 KRX 세션은 `GET /api/v1/market/sessions/KR/{종목코드}/bars?session_date=YYYY-MM-DD`로 제한된 페이지 범위에서 조회할 수 있습니다. 미국 분봉 endpoint와 페이지네이션 구현도 코드에 있지만 현재 조회·세션·데이터 검증 범위는 국내만입니다. 미국 endpoint는 현 단계에서 호출하지 마세요. 해외 분봉은 KIS 모의투자를 지원하지 않으며 구현은 향후 개발용으로 보존합니다. [KIS 공식 해외 분봉 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_time_itemchartprice/inquire_time_itemchartprice.py)

두 시장의 REST 분봉은 과거 호가 스프레드를 제공하지 않아 `spread_bps`를 null로 저장합니다. 따라서 이 봉만으로 스프레드 필터가 필요한 전략·학습 자료의 적격성을 증명하지 않습니다. `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`이면 정규장 내 REST 이력과 WebSocket 집계 분봉을 출처와 함께 `market_minute_bar`에 저장합니다. 기존 WebSocket 봉에 스프레드가 있으면 REST 보충 과정에서 그 값을 덮어쓰지 않습니다. WebSocket 집계봉은 당시 호가 스프레드를 포함하며, PostgreSQL 쓰기 오류는 상태에 표시하고 PAPER 자동주문을 차단합니다. 동일 시각 재수집은 출처별 품질 규칙에 따라 저장합니다. 기본 저장값은 `false`입니다.

KIS의 현재 공지 기준 PAPER는 초당 1건, REAL은 계좌당 초당 18건이며 동시 호출은 100~150ms 간격을 권고합니다. Python과 Spring은 PostgreSQL의 `kis_rest_rate_limit_slot`에서 환경과 앱키 지문별 요청 슬롯을 원자적으로 예약해 프로세스 간에도 호출 간격을 공유합니다. 앱키 원문은 저장하지 않고 SHA-256 지문만 저장합니다. 슬롯 확보에 실패하면 KIS 요청을 보내지 않습니다. `.env.example`의 Python 간격은 PAPER 1초, REAL 0.1초이며, 이보다 느리게만 조정할 수 있습니다. [KIS API 호출 유량 안내](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000001/post/d0d1a83f-6f8d-4437-9700-6d26702fd989)

- Spring 상태: `http://localhost:18080/api/v1/health`
- Python 상태: `http://localhost:18000/api/v1/health`
- Python API 문서: `http://localhost:18000/docs`
- 운영 대시보드: `http://127.0.0.1:13000`

LightGBM·XGBoost 학습용 선택 의존성은 `python -m pip install -e ".[dev,ai,ops]"`로 설치합니다. 허가된 단일 종목 1분봉 CSV(`timestamp,open,high,low,close,volume,spread_bps,symbol`)로 모델 후보를 학습하고 미사용 세션에서 AI+퀀트 및 퀀트 단독 전략 백테스트를 비교하려면 저장소 루트에서 아래 명령을 사용합니다. 각 행의 시각은 시간대를 포함해야 하고 데이터 제공·저장 권한을 확인한 자료만 사용하세요. 기본 label 왕복 비용은 20 bps, 백테스트 체결당 수수료와 슬리피지는 각각 5 bps이며 계좌별 실제 요율을 확인한 후 인자를 조정하세요. 모델 manifest에는 label 기간, 비용과 전략 필터 가정이 보존됩니다.

각 CSV는 같은 폴더에 `<파일명>.csv.provenance.json` 권리 확인 파일이 있어야 학습·백테스트를 실행할 수 있습니다. [권리 manifest 예시](docs/data-provenance-template.json)를 복사해 제공자, 원본·약관 참조, 수집·검토 시각, 로컬 저장·백테스트·학습 허용 여부를 확인해 작성하고, `dataset_sha256`에는 `Get-FileHash <CSV경로> -Algorithm SHA256`의 값을 넣습니다. KIS 공개 안내 자체에는 개인용 로컬 저장·백테스트·모델 학습의 세부 허용 여부가 모두 적혀 있지 않지만, 사용자는 2026-11-07 KIS의 서면 답변으로 세 목적이 모두 허용됨을 확인했다고 전달했습니다. 이 프로젝트는 해당 범위를 사용자 전달 확인으로 기록하며 본인 계좌 운용에만 적용하고 공개·제3자 제공은 허용하지 않습니다. [KIS 데이터 권리 확인 절차](docs/kis-market-data-rights.md)와 개별 manifest를 확인하고, KIS 답변 원문이나 세부 조건은 저장소에 포함하지 마세요.

퀀트 백테스트도 같은 권리 manifest가 필요하며, 예를 들어 `python .\scripts\backtest-quant.py .\data\licensed\005930.csv --market KR --symbol 005930`으로 실행합니다. CSV 내용이 바뀌어 SHA-256이 달라지면 기존 manifest는 사용할 수 없습니다.

여러 국내 종목을 공통 자본으로 재생하려면 각 CSV와 SHA-256에 연결된 권리 sidecar를 준비한 후 `scripts/backtest-portfolio.py`를 사용합니다. 종목마다 `--dataset`을 여러 번 지정할 수 있으며, 각 CSV는 별도 검토 sidecar를 가져야 하고 동일 종목 안에서 거래일이 겹치면 중단합니다. 종목 간에는 날짜 집합이 일치해야 하며, 각 날짜의 정규장 1분봉 380개가 완전해야 합니다. 같은 시점에 진입 후보가 슬롯보다 많으면 신호 봉 거래대금이 큰 순서, 스프레드가 좁은 순서, 종목 코드 순서로 자본·슬롯을 배정합니다. 결과는 종목별 모든 원본 파일명·SHA-256·권리 기록과 비용 후 승률·손익비·평균/중앙 거래손익을 종목 및 포트폴리오 단위로 보존합니다. 거래가 없거나 손실 거래가 없으면 손익비는 숫자 대신 그 상태를 표시합니다. 원화 손익 통계는 금액 지표이며, 별도 순수익률 bps는 각 거래의 진입 체결명목 기준 단순 수익률로 계산하고 복리 수익률로 해석하지 마세요. 아래 수수료는 입력 형식 예시이며 실제 확인한 비용으로 바꾸세요. JSON은 매수·매도 수수료 입력(유관기관 비용이 포함된 경우 그 비용 포함), 매도세, 진입·청산 슬리피지와 스프레드를 거래별·기간별로 분리합니다. 비용 구성 합계와 총비용 및 gross−costs=net 대사가 실패하면 결과를 만들지 않습니다. 포트폴리오 최대 낙폭은 각 봉의 시가·저가·종가 자산 표시를 사용해 불리한 장중 저가를 반영하고, 초기 자산에서 고점 추적을 시작합니다. 이는 봉 내부 가격 경로를 재구성하거나 실제 체결 손실을 보장하는 값이 아닙니다. 기본은 날짜별 독립 세션이며, 여러 날짜를 한 계좌의 연속 자본으로 재생하려면 `--carry-equity-between-sessions`를 추가합니다. 이때 전일 장마감 자본과 연속 손실 횟수가 다음 거래일로 이어지고, 일일 손실 한도는 매 세션 시작 자본 기준으로 다시 설정됩니다. 복리 모드 기간 최대 낙폭은 거래일 간 고점을 유지해 계산하며, 날짜가 엄격히 증가하고 각 세션 시작 자본이 직전 종료 자본과 연결되는지도 확인합니다. 각 세션의 총손익−비용=순손익 및 시작 자본+순손익=종료 자본 대사도 통과해야 하며, 잘못된 순서·자본 연결·손익·낙폭 입력은 집계를 거부합니다. 복리 모드의 전체 기간 수익률은 종료 가상자본/시작 가상자본 기준으로 표시합니다. 독립 세션 결과에는 단일 종료 자본·기간 수익률·기간 낙폭을 제시하지 않습니다. 명령은 계좌 잔고를 조회하거나 주문하지 않습니다.

슬리피지 비용 민감도를 함께 보려면 `--slippage-scenario-bps`를 반복 지정합니다. 예를 들어 `--slippage-scenario-bps 0 --slippage-scenario-bps 5 --slippage-scenario-bps 10 --slippage-scenario-bps 20`은 동일한 입력 데이터·수수료·매도세·전략으로 편도 체결 슬리피지만 바꿔 각 시나리오의 비용, 순손익, 체결 수와 낙폭을 JSON의 `slippage_sensitivity`에 기록합니다. 기본 백테스트 결과는 입력한 `--slippage-bps`를 그대로 사용하며, 이 시나리오 결과도 실체결 추정이나 주문 가능 판정이 아닙니다.

```powershell
python .\scripts\backtest-portfolio.py `
  --dataset 005930=.\data\licensed\005930.csv `
  --dataset 000660=.\data\licensed\000660.csv `
  --starting-equity 10000000 `
  --max-order-notional 1000000 `
  --buy-commission-bps 5 `
  --sell-commission-bps 5 `
  --sell-tax-bps 0 `
  --slippage-bps 5
```

승인·활성된 국내 AI 모델로 진입 신호를 추가로 거르려면 `--with-active-ai-models --registry .local/models`를 붙입니다. 입력한 모든 종목에 유효한 승인 모델이 있어야 하고, CSV의 가장 이른 거래일도 모델의 학습·검증·시험·홀드아웃 날짜보다 뒤여야 합니다. 모델이 없거나 날짜가 겹치면 중단합니다. 기본 실행은 퀀트 신호만 사용하며 결과 JSON에 데이터 해시와 provenance, 퀀트 후보·AI 통과·AI 차단 수, 위험 제한으로 수락·거부된 진입 및 실현 순손익이 기록됩니다. `executable`은 항상 `false`입니다. 봉을 공통 시간순으로 재생하고 진입 가능 여부·수량은 공유 현금, 보유 슬롯, 손실 중단 상태를 반영합니다. 한 종목을 보유 중에는 그 종목의 새 신호를 만들지 않고, 포트폴리오 한도로 거부된 종목은 이후 완료 봉에서 전략 신호를 다시 평가합니다.

백테스트 요약에는 거래별 진입금액 대비 총수익률, 비용, 순수익률(bps), 중앙값·최악 거래, 승률과 손익비를 추가합니다. 여러 거래의 수익률은 단순 산술 평균이며 복리나 실계좌 성과로 해석하지 않습니다. 거래가 없거나 손실 거래가 없는 경우 손익비는 임의의 0 대신 미정으로 표시합니다.

### LEAN 로컬 전략 백테스트

`scripts/run-lean-backtest.ps1`은 권리 sidecar와 완전한 단일 세션을 검사하고, 공식 LEAN 엔진 소스와 .NET 10 SDK를 무시된 `.local/lean-toolchain`에 준비한 뒤 C# ORB/VWAP/EMA/ATR 전략을 로컬로 실행합니다. 이 Windows 네이티브 경로에는 Docker나 QuantConnect 유료 조직 계정이 필요하지 않습니다. KIS 데이터는 로컬 data folder만 사용하며 cloud로 업로드하지 않습니다. 첫 실행은 인터넷에서 Microsoft SDK 설치 스크립트, QuantConnect LEAN 소스, NuGet 패키지를 가져옵니다. 빌드·실행 기록은 `.local/logs`에 저장됩니다. 시장별 `.env_local`의 매수·매도 수수료, 매도세, 체결별 슬리피지가 모두 설정되면 두 엔진이 그 값을 함께 사용합니다. 전부 비어 있으면 5/5/0/5 bps 진단 기본값임을 결과에 표시하고, 일부만 설정하면 중단합니다. 네 값을 명령행 인자로 명시해 비교할 수도 있습니다.

```powershell
# Overseas operational backtest commands are disabled in the current KR-only validation phase.
.\scripts\run-lean-backtest.ps1 -CsvFile .\data\backtests\kis-kr-005930-2026-10-30.csv -Market KR -Symbol 005930 -Session 2026-10-30

```

국내 범위에서 실행 도구는 Python 기준 백테스트와 LEAN의 봉 수·퀀트/AI 신호·거래 수·순손익을 비교하고 차이가 0.01원을 넘으면 실패합니다. 모든 LEAN 주문이 체결됐는지도 확인하므로 매수가능액 부족으로 거부된 주문을 성과 비교에 포함하지 않습니다. 단일 종목 Python 기준과 맞추기 위한 LEAN 초기 현금은 KRW 1천만원 또는 USD 10만이며, 이 값은 포트폴리오 replay나 실제 계좌 규모의 성과 근거가 아닙니다. 권리 확인된 국내 데이터로 2026-11-11 005930(380봉)은 3거래·6주문 전체 체결, Python/LEAN 순손익 -1,728.004814476원, 000660(380봉)은 15거래·30주문 전체 체결, 순손익 -147,136.896536원으로 각각 0.01원 미만 차이로 일치했습니다. 두 종목 모두 비용 가정 반영 후 음수 순손익이며, 이 작은 한 세션 표본은 전략 승인 근거가 아닙니다. LEAN이 현재 UTC 날짜의 세션을 기간으로 허용하지 않는 경우, 로컬 LEAN 전용 CSV와 AI 점수 파일의 날짜만 마지막 허용 날짜로 옮겨 재생합니다. UTC 시각·가격·거래량·스프레드 값과 원본 세션, 권리 sidecar, 원본/스테이징 SHA-256은 보존하고 원본 CSV는 수정하지 않습니다. 결과에는 원본 세션과 LEAN 재생 세션을 따로 표시합니다. 2026-11-13 두 종목은 380봉 무거래·순손익 0으로 같은 날짜 자료의 Python/LEAN 대조를 통과했고, 005930 11월 11일의 3거래 세션도 11월 12일 LEAN 재생일로 옮긴 뒤 거래 3건·주문 6건 전체 체결과 동일 손익을 재현했습니다. 예전 미국 자료 결과는 당시 구현 비교 이력일 뿐이고 현재 운영 검증 근거로 보지 않습니다. 소수 표본의 수익은 전략 성과나 실전 적합성을 입증하지 않습니다.

P4 모델 학습은 왕복 레이블 비용과 매수·매도 수수료, 매도세, 체결별 슬리피지 값을 모두 요구합니다. 실제 계좌 적용값을 확인한 뒤 [비용 설정 및 학습 절차](docs/operations.md)에 있는 명령 형식으로 실행하세요. 진단 기본값을 실계좌 비용으로 대신 사용하지 않습니다.


PostgreSQL에 저장된 분봉은 로컬 CSV로 내보낼 수 있습니다. 예: `.\services\market-ai\.venv\Scripts\python.exe .\scripts\export-market-data.py --market KR --symbol 005930 --from-date 2026-11-01 --to-date 2026-11-06 --output .\data\exports\005930.csv`. 날짜 범위는 거래소 현지 날짜 기준으로 양 끝을 포함합니다. 먼저 `--dry-run`을 추가하면 파일을 만들지 않고 전체 행·출처와 세션별 기대 봉 수, 봉 완전성, 스프레드 완전성을 확인합니다. 실제 내보내기 조회도 DB 읽기 전용이며 `data/`는 Git에서 제외됩니다. 모든 행 출처가 KIS Open API인 경우 sidecar는 사용자가 확인한 로컬 저장·백테스트·본인 계좌용 학습 권한과 개인 운용 제한을 SHA-256에 묶어 기록합니다. 타사 또는 혼합 출처가 포함되면 모든 권한을 `false`로 유지해 해당 제공자의 별도 검토를 요구합니다.

해외 모델 학습 코드는 `--market US`도 지원하지만 현재 실행하는 학습·검증은 `--market KR`만 대상으로 합니다. 모델 파일과 메타데이터는 `.local/models`에만 저장됩니다. 생성된 모델은 자동 승인되지 않으며, 승인된 활성 모델도 예측 API는 `executable=false`를 반환합니다.

국내 거래소 영업일 판정과 모의 운영 가드 검사에는 `python -m pip install -e ".[dev,ai,ops]"`의 선택 `ops` 의존성이 필요합니다. 해외 시장 지원 코드는 유지하지만 현재 실행하는 테스트와 KIS 연결·모의매매·실전 검증은 국내주식만 대상으로 합니다. `PAPER_AUTOTRADING_ENABLED=false`가 기본값이며, 시세 지연·모델/브로커 상태·일일 손실·연속 손실·보유 종목 수·장 마감 구간의 조건 중 하나라도 실패하면 신규 진입을 차단합니다. 자동매매를 켤 때는 당일 전략 분봉의 재시작 복구를 위해 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`도 필수입니다. 분봉 저장/복원 설정이 없으면 PAPER 자동매매 monitor가 시작되지 않습니다. 미국 PAPER 감시용 설정 키는 해외 개발 호환성 때문에 남겨 두었지만 현재 켜거나 검증하지 않습니다.

KIS 모의 현재가 조회 예시 (선택 기능):

```powershell
Invoke-RestMethod http://localhost:18000/api/v1/market/quotes/KR/005930
```

미국 경로도 코드에 있지만 현재 KIS 조회·테스트는 국내 종목만 대상으로 합니다. 미국 URL은 지금 호출하지 마세요.

계좌 조회는 명시적으로 활성화한 후 환경과 시장을 URL에 지정합니다. 계좌번호는 KIS가 요구하는 8자리+2자리 총 10자리 형식으로 `.env_local`에 저장하세요. 조회 결과에는 계좌번호가 포함되지 않습니다.

```powershell
Invoke-RestMethod http://localhost:18000/api/v1/accounts/paper/KR/balance
```

실전 계좌 경로도 현재 국내 잔고 GET만 검증합니다. 이 조회는 읽기 전용이며 실주문은 계속 잠겨 있습니다.

Spring KIS 계좌 API 예시:

```powershell
Invoke-RestMethod http://localhost:18080/api/v1/kis/accounts/paper/KR/balance
Invoke-RestMethod 'http://localhost:18080/api/v1/kis/accounts/paper/KR/balance?symbol=005930&unitPrice=71000'
```

미국 API 경로는 해외 기능 개발용으로 보존하며, 현재 호출·연동 검증 범위에는 포함하지 않습니다.

P1-001 계좌 스냅샷은 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`일 때만 PostgreSQL에 저장합니다. Spring 계좌 스모크 `.\scripts\test-paper-kis.ps1`은 국내 PAPER 잔고·주문가능액 GET을, Python 스모크 `.\scripts\test-paper-kis-python.ps1`은 국내 PAPER REST 시세·잔고 GET을 실제 KIS에 요청합니다. 두 스크립트 모두 `.env_local`의 자격정보와 읽기 전용 스위치를 요구하며 주문 또는 자동매매 잠금이 해제된 설정이면 중단합니다. 현재 두 스크립트에 `-IncludeUS` 옵션은 없고, 명시적으로 실행하지 않으면 외부 KIS 요청도 하지 않습니다. API·DB·운영 상세는 [docs](docs/README.md)를 참고하세요.

Paper 주문 미리보기:

국내 KIS 지정가 주문은 공식 KIS 종목마스터를 기준으로 보통주 여부와 KRX 가격대별 호가단위를 먼저 확인합니다. 이 종목마스터는 계정 인증정보 없이 공개 파일에서 내려받고 `.local/reference-data`에만 저장됩니다. 첫 실행 후 또는 파일이 7일보다 오래되면 저장소 루트에서 갱신하세요. 지원하지 않는 상품군과 종목마스터 누락·오래됨은 주문 전 차단됩니다.

```powershell
python .\scripts\update-kis-domestic-symbol-master.py
```

현재 주문 어댑터에서는 보통주(`S`)만 허용합니다. ETF/ETN 및 기타 상품군은 각 상품 호가·수량 규칙을 별도 검증하기 전까지 주문할 수 없습니다. 국내 종목마스터 데이터는 로컬 전용이며 Git에 추가하지 않습니다.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:18080/api/v1/orders/paper `
  -Headers @{ 'Idempotency-Key' = 'd8d449d8-f154-4c5a-b1d0-6ce69f790f51' } `
  -ContentType 'application/json' `
  -Body '{"market":"KR","symbol":"005930","side":"BUY","quantity":1,"limitPrice":70000,"currency":"KRW"}'
```

주문은 `PENDING` 상태로 PostgreSQL에 기록됩니다. 모의 체결은 별도 paper 전용 API로 직접 기록하며, 주문 수량보다 많이 체결시키거나 보유 수량보다 많이 매도하지 못합니다. 주문 요청은 같은 `Idempotency-Key`, 체결 이벤트는 같은 `fillKey`로 재전송 중복을 막습니다. Spring과 Python은 기본적으로 `127.0.0.1`에만 바인딩되며, 인증이 없는 로컬 개발용 API이므로 외부 네트워크에 노출하지 마세요.

KIS PAPER 주문·취소 lifecycle 스크립트는 구현되어 있지만 현재 국내 전용 운영 범위에서는 실제 브로커 주문을 실행하지 않습니다. 국내 PAPER 취소 가능수량 조회 지원이 확인되지 않아 `-ConfirmPaperOrderAndCancel`은 국내 주문 전 차단되며, 미국 주문·취소 검증은 현 단계에서 실행하지 않습니다. 세부 조건은 [Windows 운영 문서](docs/operations.md)를 참고하세요.

수동 paper 체결과 가상 포지션 조회 예시:

매수 체결에는 가상 현금 잔고가 필요합니다. `.env_local`의 `PAPER_STARTING_CASH_KRW`와 `PAPER_STARTING_CASH_USD`에 직접 선택한 모의 시작 금액을 설정하세요. 기본값은 0이며, 기존 잔고는 재시작해도 설정값으로 덮어쓰지 않습니다. 가상 현금 조회는 `/api/v1/orders/paper/cash`입니다.
선택 사항인 `PAPER_ORDER_TIMEOUT_SECONDS`는 미체결/부분체결 주문을 만료시키는 시간입니다. 기본값 0은 자동 만료를 끕니다.

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:18080/api/v1/orders/paper/1/fills `
  -ContentType 'application/json' `
  -Body '{"fillKey":"d4f78a48-1600-4c60-95a8-8cc53dfec06c","quantity":1,"fillPrice":70000}'
Invoke-RestMethod http://localhost:18080/api/v1/orders/paper/positions
Invoke-RestMethod -Method Post -Uri http://localhost:18080/api/v1/orders/paper/1/amend `
  -Headers @{ 'Idempotency-Key' = 'd3952b31-e893-48ce-bb5e-a621d1c513c4' } `
  -ContentType 'application/json' `
  -Body '{"quantity":2,"limitPrice":71000}'
Invoke-RestMethod -Method Post http://localhost:18080/api/v1/orders/paper/1/cancel
Invoke-RestMethod http://localhost:18080/api/v1/orders/paper/1
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

기능 개발 중에는 변경한 동작에 해당하는 테스트만 실행합니다(예: `python -m pytest services/market-ai/tests/test_paper_runtime.py::test_name -q`, `Invoke-Pester -Path scripts/tests/paper-session-monitor-status.Tests.ps1`). 위 전체 Spring/Python 테스트와 Windows PAPER preflight는 최종 통합 검증 때 실행하며, GitHub Actions 전체 검사는 수동 실행 또는 `main` 대상 PR에서만 수행합니다.

## 비밀정보 취급

- 실제 KIS App Key, App Secret, 계좌번호는 `.env_local`에만 보관하고 절대 커밋하지 마세요.
- `.env_local`은 Git에서 제외됩니다. 키가 비어 있으면 앱의 paper/mock 기능만 사용할 수 있으며, 실제 KIS 계좌 조회와 P1 완료 검증은 수행되지 않습니다.
- 모의투자 자격증명도 민감정보로 취급하세요. 로그나 이슈에 붙여 넣지 마세요.
- `.env.example`을 바꾸어 실제 키를 넣지 마세요.

Docker를 선호하면 기존 PostgreSQL 대신 컨테이너 DB도 선택할 수 있습니다. 이 경우 Docker Desktop을 켠 상태에서 `docker compose up -d postgres`를 실행하고, `.env_local`의 DB 접속 암호를 동일하게 사용하세요.

## 단계별 개발 순서

**현재 운영 범위(2026-11-13):** 해외 API·전략·모델 코드는 국내 기능과 함께 계속 개발합니다. 신규 기능·회귀 테스트, KIS 연결, 시장 세션·시세 확인, 실데이터 백테스트·학습 검증은 국내만 대상으로 합니다. 해외 기능 테스트와 미국 계좌·시세·PAPER·REAL 검증은 별도 범위 변경 전까지 보류하며, 국내 전용 잠금의 안전 회귀 검사만 예외입니다. `.env_local`의 PAPER watchlist는 `KR:005930;KR:000660`이고 미국 feed 및 미국 PAPER/P4 예약 작업은 비활성입니다. 현재 FastAPI 세션 상태에서도 국내 구독 2개와 PAPER feed `RUNNING`을 확인했습니다. 과거 미국 결과는 현재 검증 근거가 아닙니다. 실전 주문과 PAPER 자동매매는 잠금 상태입니다.

**실시간 검증 상태(2026-11-13 23:30 KST):** 로컬 GET 확인에서 Spring/FastAPI 정상, 국내 PAPER WebSocket 감시 `RUNNING`, 구독 2개, 분봉 저장 `RUNNING`·대기 0건을 확인했습니다. 장 종료 후라 조회 순간 틱 수신은 0건이며 장중 데이터 수집 근거로 계산하지 않습니다. PAPER KIS 주문·자동매매·REAL 주문·REAL 대조는 모두 비활성입니다. 국내 학습 적격 세션은 005930·000660 각각 2/13입니다. 다음 KRX 전체 세션 감시 작업은 Windows 작업 스케줄러에서 `Ready`, 2026-11-16 08:45 KST로 등록되어 있습니다. 저장 자료 감사에서 11월 11일·13일 두 종목 각각 380/380분, KIS PAPER WebSocket 출처, 스프레드 및 호가 관측 시각 100%를 확인했습니다. 11월 12일은 두 종목 모두 10:11 한 분이 빠져 379/380이며 감시 기록에는 10:08 `SESSION_STATUS_UNAVAILABLE`가 남았습니다. 인과관계는 확정되지 않았습니다. 실전 주문은 비활성입니다.

- **P1 — 기반·KIS 읽기 전용 연결:** Spring 계좌 조회와 Python REST/WebSocket 시세 기능을 구현했습니다. 국내 PAPER 잔고·주문가능액과 국내 REAL 잔고·종목 기준 주문가능현금의 읽기 전용 GET 검증이 통과했습니다. 11월 14일 국내 PAPER 계좌 전용 통합검사도 잔고 조회·주문가능현금 2/2 통과했으며, 실제 금액이나 계좌정보는 출력하지 않았습니다. 재연결마다 KIS 접속키를 다시 요청하지 않도록 메모리 재사용·23시간 갱신을 추가했고 집중 테스트 2건과 GitHub Actions 두 workflow가 통과했습니다. 11월 13일 운영 상태는 Spring/FastAPI 정상, PAPER feed `RUNNING`, 신규 분별 진단 지원입니다. 미국 검증은 현재 보류이며 실주문·PAPER 주문·자동매매는 비활성입니다.
- **P2 — 주문·리스크 엔진:** PostgreSQL PAPER 주문, 부분체결, 중복 체결 방지, 현금·포지션 정산, 정정·취소·만료와 재시작 복구가 있습니다. 정정 내역은 별도 이력 테이블에 저장하며 같은 키의 반복 적용을 차단합니다. 로컬 PostgreSQL 통합 및 DB 장애 복구 검증을 통과했습니다. KIS 주문 없이 paper ledger에서 확인한 결과입니다. 11월 14일 PostgreSQL 통합에서 같은 멱등키 중복 제출이 브로커 Mock에 한 번만 전달되는 검사, 제출 타임아웃 뒤 `UNKNOWN` 상태로 남아 재전송하지 않는 검사, 같은 체결 키 동시 제출에도 부분체결·현금·포지션이 한 번만 반영되는 검사를 각각 통과했습니다. 이어 시험 전용 매수 수수료 10bp·매도 비용 20bp를 적용한 매수/매도 정산 검사에서 체결비용·잔액·포지션과 비용 후 손익 717원이 일치했습니다. 이 시험 입력은 원장 계산 검사용 가정이며 실제 계좌 수수료 확인값이 아닙니다. 11월 13일 국내 PAPER 069500 1주·50,000원 기준 읽기 전용 사전점검에서 시세 신선도와 주문가능금액을 확인했지만 주문·취소 수명주기 검증은 아닙니다. 읽기 전용 사전점검은 최신 실행 결과를 `.local/validation/paper-preflight-latest.json`에 민감한 금액·계좌정보 없이 남깁니다. 추가로 opt-in PAPER 상태 poller가 읽기 권한과 PAPER 운영 잠금 아래 최대 50개의 알려진 국내 브로커 주문번호를 읽기 전용 일별 체결조회로 대조하도록 추가했습니다. 기본 비활성이고 PAPER 주문·취소를 제출하지 않으며 브로커 주문번호가 없는 UNKNOWN은 자동 재시도하지 않습니다. Mock 집중검증은 통과했지만 실제 PAPER 주문 수명주기는 아직 검증되지 않았습니다.
- **P3 — 전략·백테스트:** 단일 세션 ORB/VWAP/EMA/ATR 전략, 비용·슬리피지, 미래 봉 미사용을 구현했습니다. 백테스트 요약은 개별 거래 지표와 함께 세션별 양·음·무거래일, 평균·중앙·최악·최선 손익, 거래금액 대비 비용 차감 수익률(bp)을 분리해 보여주며 거래 합계와 세션 총액이 불일치하면 거부합니다. 무거래일은 통화 손익 통계에서는 0으로 계산하지만 수익률 평균의 분모에서는 제외하고 별도 집계합니다. 11월 11일·13일의 완전한 국내 PAPER 세션만 별도 내보내어 독립 종목 및 공용 자금 포트폴리오 재생을 실행했습니다. 독립 1주 재생은 11월 11일 005930 3건·순손익 약 -₩1,728, 000660 15건·약 -₩147,137이며 11월 13일은 두 종목 모두 무거래입니다. 포트폴리오 재생은 가상자금 ₩10,000,000·종목별 최대 ₩1,000,000에서 11월 11일 후보 44건 중 3건만 거래했고, 41건은 SK하이닉스 주가가 종목 배정·주문 금액 한도보다 높아 두 한도 모두 1주 미만으로 거부됐습니다. 거래위험 예산이 원인으로 판정되지는 않았습니다. 비용 후 손익은 약 -₩8,980입니다. 11월 13일은 후보·거래가 없습니다. `.env_local` 비용은 연구 가정이며 이틀 표본은 성과 승인이나 실계좌 비용 검증 근거가 아닙니다. 비용 차감 후 결과가 부정적이므로 전략·위험 sizing 분석을 계속하고, 이 자료로 매개변수를 맞추지 않습니다. 국내 저장·백테스트·학습 권한은 사용자가 확인했습니다.
- **P4 — AI 모델:** LightGBM/XGBoost 학습·추론 후보, 검증된 소스 세션의 학습 샘플 생성, 시간 순서 분리, 해시·권리 manifest 기반 모델 등록을 구현했습니다. 11월 13일 기준 완전한 국내 학습 적격 세션은 005930·000660 각각 2/13으로, 종목당 추가 11개가 필요합니다. 기존 11월 27일 cutoff 예약은 누락 세션을 반영하면 최소 학습 기준에 못 미칠 수 있어 비활성화했습니다. 새 후보 학습은 데이터 범위를 12월 1일까지 포함해 12월 2일, 독립 홀드아웃은 12월 2~9일 데이터를 확보해 12월 10일 실행하도록 국내 두 종목에 각각 예약했습니다. 실제 학습은 미래 세션 품질·권리·최소 표본 게이트를 다시 통과해야 합니다. 11월 12일의 불완전 세션과 스프레드가 없는 과거 REST 봉은 학습 자료로 승격하지 않습니다. 학습 readiness API와 대시보드는 세션 수뿐 아니라 거래소 달력 기준 기대 세션 수, 데이터가 전혀 없는 세션, 최신 연속 적격 세션, 비용 가정, 라벨 표본·양/음성 분포, 시간순 파티션의 표본 하한을 확인합니다. 실시간 추론·학습·홀드아웃·미리보기·승인 입력의 확률 출력은 이진 클래스 순서, 행 수, 유한한 [0,1] 범위와 합계를 검증하며 오류 시 차단합니다. 아직 모델 학습·홀드아웃 비교를 통과한 후보가 없고 모델은 미승인입니다.
- **P5 — 자동 모의매매:** 거래소 캘린더, 실시간 틱 집계, 국내 체결·호가 동시 구독, 같은 분 스프레드, 진입 위험 게이트·위험기반 수량 계산, 로컬 PAPER 브로커 경계를 구현했습니다. 11월 11일·13일은 전체 세션 품질(380/380분, 스프레드·관측시각 100%)을 통과했고 11월 12일은 10:08 상태 API 조회 실패로 감시가 종료됐으며 DB 감사에서 두 종목 모두 10:11 한 분 누락을 확인했습니다. 상태 조회를 최대 2회 재시도하고 재시도 횟수를 비밀정보 없이 세션 증거와 진행 기록에 남기도록 보강했습니다. 추가 감사에서 같은 날 `POSTGRES_WRITE_FAILED`도 기록된 점을 확인했지만, 저장 오류와 10:11 분봉 누락의 시간상 인과는 확인되지 않았습니다. 5f6b115부터 PostgreSQL 분봉 upsert를 최대 3회 즉시 시도하고, 각 분봉을 커밋되지 않은 상태로 로컬 무시 경로 `.local/state/bar-write-outbox.sqlite3`의 SQLite WAL에 먼저 기록합니다. 최대 10,000건까지 재시도하며 PostgreSQL 반영 확인 뒤에만 outbox 항목을 지우고, 프로세스 재기동 때 미완료 항목을 복구합니다. 대기·재시도 수는 API·대시보드에 표시하고 복구 중에는 PAPER 자동매매와 REAL 신호 처리를 차단합니다. outbox 용량 초과 또는 기록 실패는 fail-closed 처리합니다. 추가로 supervisor가 15초마다 두 API의 유일한 loopback 리스너를, 30초마다 API health와 거래 잠금을 검사하고 이상 시 안전 재시작하도록 보강했습니다(4/4 집중검사와 두 Windows CI 통과, 재기동 후 로컬 API/잠금 확인). 실제 PostgreSQL 연결 장애를 주입해 outbox 보존·자동화 차단과 새 runtime 재기동 후 DB 1회 저장·outbox 배출을 확인하는 통합검사가 로컬 PostgreSQL 및 CI에서 통과했습니다. 이는 코드 수준 복구 검증이며 장중 장애 후 전체 세션 연속성은 아직 미검증입니다. 다음 국내 세션 감시는 11월 16일 08:45 KST에 예약되어 있습니다. 지속 복구 관찰과 PAPER 주문 수명주기 검증 전까지 P5는 미완료이며 PAPER 자동매매·주문은 잠금입니다.
- 일일 기준선은 Spring paper API에서 거래일·통화별 최초 값으로 고정되며 동일 값 재요청만 허용합니다. KRW와 USD 평가액은 환산 없이 각각 계산합니다. 장 시작 3분 안에 기준선을 저장하지 못하거나 보유 종목 시세가 빠지거나 지연되면 해당 통화 신규 진입을 차단합니다. 모든 신규 진입은 `PAPER_AUTOTRADING_ENABLED=true`와 paper WebSocket 설정까지 명시적으로 켜야 하며 KIS 주문 API와 연결되지 않습니다.
- **P6 — KIS 주문 엔진 준비:** KIS PAPER/REAL 주문 어댑터, 계좌 사전확인, 영속 멱등성, 일별 체결 재조회와 PAPER/REAL 주문 의도를 구현했습니다. 국내 PAPER 취소는 KIS 취소 가능수량 조회의 모의투자 지원이 확인되지 않아 잠겨 있습니다. REAL 주문은 국내 기준으로도 아직 검증하지 않았으며 모든 주문 잠금은 비활성입니다. 해외 지원 코드는 유지하지만 해외 계좌 조회·주문은 검증하지 않습니다.

실제 KIS PAPER 주문·취소 통합 실행은 별도 opt-in 스크립트로 준비되어 있습니다. 현재 `KIS_PAPER_ORDERS_ENABLED=false`이므로 모의 브로커 주문도 아직 보내지 않았으며, 실행 절차와 제한은 [Windows 운영 문서](docs/operations.md)에 있습니다.
- **P7 — 운영 고도화:** 조회 전용 Next.js 대시보드, 일일 JSON 운영 리포트, paper 총액/비용후 손익, 오늘 체결비용, 국내 KRX 감시 최신 결과·실패 코드와 전체 세션/일부 구간 구분, 시장별 모델 승인·활성화·롤백 기록을 구현했습니다. 일일 리포트는 Spring·Python 선택 API 진단과 최신 로컬 모델 재학습 결과(검토 필요 후보·실패 단계)를 포함하며, 예외 원문이나 임의 보고서 필드는 복사하지 않고 최신 보고서가 손상되면 유효한 오래된 결과로 숨기지 않습니다. 보고서는 완성된 JSON을 원자적으로 교체합니다. 최신 PAPER 읽기 전용 사전점검의 결과·단계·시각·국내/해외 구분·시세/주문가능금액 확인을 안전 필드만 허용해 일일 리포트와 대시보드에 표시하며, 주문·취소 여부는 항상 미전송으로 고정합니다. 대시보드에서 PAPER 장부와 REAL 주문 의도를 분리해 보고, 국내 PAPER 상태와 실거래 잠금 상태를 확인하며, 주문 잠금·자동 대조 스위치·미확인 REAL 건수를 확인합니다. 보호청산 준비·자동 보호매도와 KR/US 비용률 형식 유효 여부도 표시하며, 계좌읽기·체결대조·자격정보·비용 설정이 모두 갖춰져야 신규 REAL 진입 준비 게이트가 열린다고 나타냅니다. 수수료 모델은 `.env_local`에서 실제 계좌의 비용률을 설정한 후 켜야 하며 기본 비활성입니다. 표시되는 비용 설정 상태는 형식 유효 여부만 뜻하며 KIS가 적용하는 실제 계좌 수수료 확인을 대체하지 않습니다. 연속 paper 세션, 실제 KIS 계좌·체결비용 대조, 데이터 권리와 모델 성능 검증이 끝나기 전까지 전체 운영 승인 기준은 통과하지 않습니다.

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
