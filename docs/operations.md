# Windows 운영

## 로컬 실행

1. PostgreSQL 서비스가 `localhost:5432`에서 실행 중인지 확인합니다. `postgresql.conf`의 `listen_addresses`는 `localhost`여야 합니다. `start-local.ps1`은 지정된 포트의 listener가 loopback 전용이 아니면 Spring을 실행하지 않고, `restart-local-stack-task.ps1`도 같은 주소 검사를 마친 뒤에만 기존 API를 중지합니다. 설정을 바꾸려면 관리자 권한 PowerShell에서 저장소 루트로 이동해 `powershell.exe -ExecutionPolicy Bypass -File scripts/configure-postgres-loopback.ps1`을 실행합니다. 스크립트는 현재 설정을 `C:\ProgramData\AIStockTrader\backups`에 백업하고 localhost로 바꾼 뒤 서비스를 재시작해 모든 listener가 loopback인지와 `pg_isready`가 연결을 받아들이는지 확인합니다. 검증에 실패하면 원본 설정 파일을 해시로 대조 복구하고, 서비스와 로컬 DB 연결이 복구됐는지 확인합니다. 실행 전 확인 메시지가 표시되며 PostgreSQL을 사용하는 로컬 앱의 연결이 잠시 끊깁니다. 고정된 비루프백 설정 오류는 실행기가 재시도를 반복하지 않고 `BLOCKED`로 종료합니다. 설정 성공 후 현재 사용자 PowerShell에서 `.\scripts\activate-local-stack.ps1 -Confirm:$false`를 실행합니다. 이 명령은 loopback, PAPER 주문·자동매매 잠금, 작업 대상 경로를 검증한 후 비활성 작업을 켜고 Spring/FastAPI의 로컬 health 및 거래 잠금을 확인합니다.
2. 저장소 루트의 Git 제외 `.env_local`에 DB 비밀번호와 필요한 KIS 환경별 설정을 입력합니다.
3. Spring Boot를 실행합니다: `.\scripts\start-local.ps1`
4. Python API는 `services/market-ai` 가상환경에서 실행합니다. API는 `127.0.0.1`에만 바인딩합니다.

### Windows 로그인 시 Dashboard 자동 시작

`AIStockTrader-Dashboard` 작업은 현재 Windows 사용자의 대화형 로그인 때 Dashboard를 시작합니다. 먼저 로컬 스택과 Dashboard production build가 준비되어 있어야 합니다. 설치·시작은 `.\scripts\install-dashboard-task.ps1`, 제거는 `.\scripts\remove-dashboard-task.ps1`입니다. 별도 포트 13000이 이미 점유되었으면 어떤 프로세스도 종료하거나 교체하지 않습니다. Spring/FastAPI의 상태와 PAPER·주문 잠금을 확인할 수 없거나 예상한 값이 아니면 Dashboard를 시작하지 않습니다. 실제 주문과 PAPER 자동매매를 활성화하는 기능은 없습니다. 작업은 관리자 권한 없이 현재 사용자 권한으로 실행되며, 로그는 `.local\logs\dashboard-task.log`에 남습니다.

Java 빌드는 Gradle이 아닌 프로젝트 포함 Maven Wrapper를 사용합니다: `.\backend\trading-api\mvnw.cmd test`.

## GitHub Actions 검증

`develop`에 push하거나 `main`·`develop` 대상 pull request를 열면 전체 스택 검증이 실행됩니다. Python은 AI·거래소 캘린더 의존성을 포함해 pytest를 실행하고, Spring은 PostgreSQL 16 서비스에 Flyway 마이그레이션을 적용해 opt-in PostgreSQL 통합까지 실행합니다. Spring CI에는 KIS 자격정보를 넣지 않으며 KIS 실계좌/PAPER 통합 플래그는 켜지지 않습니다. 대시보드는 ESLint와 production build를 실행하고, 별도 Windows workflow는 PowerShell PAPER 사전검사와 재시작 안전성을 확인합니다.

paper ledger 비용 추정은 `PAPER_COST_MODEL_ENABLED=false`가 기본입니다. 실제 적용 요율을 확인한 뒤 KR/US 매수·매도 수수료율을 basis points로 설정하고 `PAPER_COST_MODEL_ENABLED=true`로 켭니다. 한국·미국 계좌별 우대 수수료, 거래소 및 규제 비용은 다를 수 있으므로 예제의 0 값은 실거래 요율을 의미하지 않습니다. 비활성 상태에서는 P&L을 총액으로만 해석합니다.

### Python 의존성 갱신

`services/market-ai/pyproject.toml` 변경 후에는 시세 감시를 끝내고 로컬 API 작업을 중지한 다음 저장소 루트에서 실행합니다. 한국장 캘린더 호환성을 위해 pandas는 3.1 미만이어야 합니다.

```powershell
Set-Location .\services\market-ai
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ai,ops]"
.\.venv\Scripts\python.exe -c "import pandas, exchange_calendars; v=tuple(int(x) for x in pandas.__version__.split('.')[:2]); assert v < (3, 1), pandas.__version__; print('pandas', pandas.__version__, 'exchange_calendars', exchange_calendars.__version__)"
```

설치 후 Windows 로컬 스택을 다시 시작하고 `/api/v1/health`와 PAPER/REAL 주문 잠금을 확인합니다. 비호환 pandas가 남아 있으면 한국장 세션 판정이 진입·청산 판단을 모두 차단합니다.

## KIS 자격증명

- 모의: `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NUMBER`
- 실전: `KIS_REAL_APP_KEY`, `KIS_REAL_APP_SECRET`, `KIS_REAL_ACCOUNT_NUMBER`
- 계좌번호는 KIS 계좌 앞 8자리와 상품 코드 뒤 2자리를 붙인 형식입니다.
- Spring과 Python KIS 계좌조회는 전체 스위치 `KIS_ACCOUNT_READ_ENABLED=true`와 대상별 스위치가 모두 필요합니다. PAPER는 `KIS_PAPER_ACCOUNT_READ_ENABLED=true`, REAL은 `KIS_REAL_ACCOUNT_READ_ENABLED=true`입니다. REAL 스위치는 기본 false입니다.
- KIS REAL 체결 알림 구독은 추가 읽기 전용 기능이며 기본 잠금입니다. 사용하려면 `KIS_REAL_EXECUTION_NOTICES_ENABLED=true`, REAL 계좌조회 두 스위치, `KIS_REAL_HTS_ID` 및 REAL 자격정보가 모두 있어야 합니다. 알림을 받으면 저장된 주문번호와 일치하는 주문만 REST로 다시 조회해 체결 관측을 갱신합니다. 알림 자체로 주문을 내지 않으며, `KIS_REAL_ORDERS_ENABLED`나 `LIVE_TRADING_ENABLED`를 켜지 않습니다. 아직 실시간 KIS 연결 검증은 완료되지 않았습니다.
- 소유자가 확인한 KIS 데이터 이용 범위는 로컬 저장·백테스트·AI 학습 허용입니다. 이 허용은 매매 성능 검증이나 실계좌 자동주문 승인과 별개입니다.
- Python 실시간 시세 릴레이는 `KIS_WEBSOCKET_ENABLED=true`를 요구합니다. 국내 paper feed는 PAPER 자격정보를 사용합니다. 미국 시세 구독을 PAPER ledger에 공급하려면 `KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED=true`와 REAL 시세 자격정보가 추가로 필요합니다. 이 별도 REAL 연결은 미국 시세만 읽으며 모든 주문은 로컬 PAPER ledger로만 갑니다. 계좌 조회 및 REAL 주문 스위치와 독립이며 기본값은 `false`입니다.
- 미국 REAL 시세 구독 경로만 확인하려면 `scripts/probe-kis-real-us-websocket.py --symbol AAPL --exchange NAS`를 사용합니다. `.env_local`의 REAL app key/secret과 `KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED=true`가 필요하며, 전역 거래 모드는 PAPER이고 LIVE/PAPER/REAL 주문·자동매매 잠금 및 REAL 계좌조회 잠금이 꺼져 있을 때만 실행됩니다. 이 독립 프로세스는 PAPER 키·계좌번호·주문·계좌조회·REAL 시그널 환경변수를 제거하거나 false로 강제하고, KIS 실전 시세용 구독 승인 뒤 즉시 구독 해제를 시도합니다. 기본 모드는 구독 승인만 확인하며 실제 틱은 장중 `--wait-for-tick-seconds 60`처럼 지정해 별도로 확인합니다. 로컬 monitor API가 REAL feed를 CONNECTING/SUBSCRIBING/RUNNING/RECONNECTING으로 보고하면 중복 소켓을 막기 위해 probe를 거부합니다. 이미 운영 monitor가 실행 중이면 그 monitor의 feed 상태를 사용하고, 단독 probe는 기존 monitor를 중지한 상태에서만 실행합니다. 출력에는 연결 상태와 제한된 오류 코드만 남기며 키·토큰·원시 시세는 출력하거나 파일에 저장하지 않습니다. 이 검사는 실주문을 보내지 않으며 세션 전체 수집·데이터 품질 검증을 대신하지 않습니다.
- REAL 국내·미국 REST 시세 스냅샷을 별도 확인하려면 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-real-kis-us-market-data.ps1 -EnableReadOnlyGetForThisRun`을 사용합니다. 기본으로 삼성전자 005930/KR과 AAPL/NAS를 한 번에 조회하며, `-Market KR` 또는 `-Market US -Symbol MSFT -Exchange NAS`로 범위를 바꿀 수 있습니다. 이 1회성 opt-in은 현재 프로세스에서만 적용되며, 실행 모드는 PAPER로 고정하고 실주문·모의주문·자동매매·REAL 계좌조회·REAL 주문대조가 모두 false여야 합니다. KIS REAL 앱 키/시크릿 외 계좌번호와 PAPER 키·계좌번호는 자식 프로세스에서 제거하고, PostgreSQL 공유 호출 제한을 사용합니다. 로컬 `.env_local`은 바꾸지 않으며 출력은 성공 여부·종목/거래소·통화·조회시각만 포함하고 실제 가격, 원시 응답, 키, 토큰, 계좌정보는 출력하지 않습니다. 주문 API와 계좌 API는 이 도구에 연결되어 있지 않습니다.
- REAL 신호 감시기는 `TRADING_MODE=real`, `REAL_SIGNAL_MONITOR_ENABLED=true`, `KIS_WEBSOCKET_ENABLED=true`, `PAPER_WATCHLIST`, REAL 시세 자격정보를 요구합니다. 이때 한국·미국 시세 모두 REAL WebSocket에서 읽고 세션 분봉 저장이 활성화돼야 합니다. Spring이 별도로 확인하는 최신 REST 시세를 위해 `KIS_ENVIRONMENT=real`과 `KIS_MARKET_DATA_ENABLED=true`도 필요합니다. 실제 Spring 신호 POST는 `REAL_SIGNAL_SUBMISSION_ENABLED`와 live 주문·계좌읽기·보호청산·주문대조 게이트가 모두 열려 있을 때만 생성됩니다. 기본값은 false입니다. 전송 결과가 불명확하거나 대기 큐가 넘치면 현재 처리 중인 신호를 제외한 미전송 대기 후보를 버리고 해당 프로세스에서 신규 REAL 신호 전달을 중지합니다. 불명확한 결과는 멱등키를 운영 상태에 남기며 Spring 주문 의도와 증권사 체결 대조 전까지 수동 확인이 필요합니다. UNKNOWN 결과가 남은 상태에서 프로세스를 재시작할 때도 주문 의도를 먼저 대조해야 합니다.
- REAL 대기열 포화가 현재 Spring 요청 처리 중 발생해도 응답 수신 후 `BLOCKED_QUEUE_FULL` 잠금 상태가 유지됩니다. 완료된 in-flight 요청은 최근 신호 상태에 따로 표시되며 대기 중 후보는 재개하지 않습니다.
- Paper 자동 운영 스위치는 `PAPER_AUTOTRADING_ENABLED=false`가 기본값입니다. 이를 켤 때는 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`도 설정해야 하며, 현재 거래일 분봉 저장·복원에 실패하면 monitor 시작을 차단합니다. 이 기록이 없으면 재시작 후 ORB 기준 구간을 잘못 계산할 수 있습니다. 달력·실시간 피드·모델 승인·브로커 상태와 리스크 게이트 검증 전에는 켜지 마세요. Live 모드에서는 Python paper 자동화도 강제로 비활성화됩니다.
- PAPER 자동 주문 생성·체결 기록 결과가 UNKNOWN이면 실행 중인 monitor는 멱등키를 상태와 대시보드에 남기고 신규 진입을 중지합니다. 보호청산 점검은 계속됩니다. Spring에서 해당 키의 주문 상태를 확인하고 원인을 해소한 뒤 local stack을 재시작해야 신규 PAPER 진입 잠금이 초기화됩니다.
- 신규 진입에는 시세가 최신이어야 하고 신호 봉은 기본 120초 이내여야 합니다. WebSocket 재연결이나 작업 대기 때문에 오래된 봉이 늦게 도착하면 신규 진입을 차단합니다. 한도는 `PAPER_MAX_SIGNAL_BAR_AGE_SECONDS`로 설정합니다.
- 저장소에는 `.env_local`을 커밋하지 않습니다. `.env.example`에는 실제 값을 입력하지 않습니다.
- 모델 승인·활성화·롤백에는 `.env_local`의 `MODEL_GOVERNANCE_TOKEN`에 32자 이상의 무작위 값을 설정하고 요청의 `X-Model-Governance-Token` 헤더로 전달해야 합니다. PowerShell에서는 `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`로 64자리 값을 생성할 수 있습니다. 값이 비어 있거나 32자보다 짧으면 변경 API는 잠긴 상태입니다.
- 모의 계좌 연결 테스트는 의도적으로 네트워크를 호출하는 별도 작업입니다: `.\scripts\test-paper-kis.ps1`. 이 스크립트는 paper 자격증명과 전체 및 PAPER 계좌 읽기 스위치를 확인한 뒤 Spring 애플리케이션 컨텍스트와 PostgreSQL 호출 제한기를 사용해 국내·미국 잔고 GET만 실행합니다. 테스트 전 모의/실전 주문, 실전 자동매매, 실전 주문 대조가 모두 잠겼는지 확인합니다. DB 연결이나 슬롯 예약에 실패하면 KIS 요청도 차단합니다.
- Python KIS 읽기 전용 스모크는 `.\scripts\test-paper-kis-python.ps1`로 실행합니다. PAPER 키·시장 시세 스위치·전체/PAPER 계좌 읽기 게이트를 검사한 뒤 국내·미국 REST 시세 및 계좌 GET 네 건만 요청하며, 응답에 키·시크릿·계좌번호가 없는지도 확인합니다. 주문·자동매매 잠금이 꺼져 있으면 요청하지 않습니다.
- 실계좌 잔고의 읽기 전용 확인은 `.\scripts\test-real-kis-account-readonly.ps1 -EnableReadOnlyGetForThisRun`으로 별도 실행할 수 있습니다. 이 옵션은 전체/REAL 계좌 읽기 허용을 테스트 실행 중에만 설정하고, 종료 때 호출 셸의 환경변수를 원래 상태로 복원하며 `.env_local`은 변경하지 않습니다. 스크립트는 PostgreSQL, 계좌번호 형식, `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, PAPER/REAL 주문 잠금, `KIS_REAL_RECONCILIATION_ENABLED=false`를 검사하고 국내·미국 잔고 GET만 요청합니다. 자격정보 값은 출력하지 않습니다. `.env_local`의 read switches를 직접 켜도 되지만, API 실행 프로세스에서 REAL 읽기가 열릴 수 있으므로 검증 후 기본 잠금으로 되돌리세요.

### PAPER WebSocket 연결 사전점검

다음 명령은 PAPER WebSocket 승인 키와 국내 종목 구독을 확인합니다. `.env_local`에 `KIS_WEBSOCKET_ENABLED=true`를 설정해야 하며, `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, PAPER/REAL 주문 잠금, PAPER 자동매매 잠금 및 PostgreSQL 공유 호출 제한을 확인합니다. probe 프로세스는 실행 전에 자신의 프로세스 환경에서 모든 `KIS_REAL_*`, `REAL_SIGNAL_*`, `REAL_PROTECTIVE_*` 값과 자격정보를 제거합니다. 따라서 별도의 읽기 전용 REAL 시세 설정을 가진 `.env_local`을 수정하지 않습니다. 국내 장중 tick 확인은 아래처럼 `--wait-for-tick-seconds`를 지정합니다. 이 PAPER probe는 국내 구독만 지원합니다. 미국 시세 스트림은 PAPER 환경이 아닌 별도의 읽기 전용 REAL 시세 경로를 사용합니다.

```powershell
.\services\market-ai\.venv\Scripts\python.exe .\scripts\probe-kis-paper-websocket.py --market KR --symbol 005930 --wait-for-tick-seconds 120
```

`acknowledged_no_tick`은 KIS 구독 승인만 확인된 상태이고 장중 시세 수신을 뜻하지 않습니다. tick 대기를 지정한 실행은 실제 tick이 오지 않으면 실패 코드로 끝납니다. 출력은 상태와 안전 응답코드만 포함하며 승인 키와 원시 시세는 출력하거나 저장하지 않습니다. 구독 해제 요청을 보낸 뒤 연결을 닫고 주문 API는 호출하지 않습니다.

### P3 종목 후보 발굴

읽기 전용 후보 순위는 거래소 정규 연속매매 중에 `GET /api/v1/market/candidates/KR?minimum_turnover=1` 또는 `GET /api/v1/market/candidates/US?exchange=NAS&minimum_turnover=1`으로 조회합니다. 국내는 KIS 거래대금순 정렬, 미국은 거래대금순 API를 사용합니다. 후보마다 현재가·거래량·거래대금·호가 스프레드를 확인하며, 호가가 없거나 매수·매도 가격이 교차하면 필터 통과로 표시하지 않습니다. 예시의 1은 호출 형식만 보여 주는 값이며 권장 투자 기준이 아닙니다. `minimum_turnover`는 KRW 또는 USD 단위로 반드시 지정해야 합니다.

미국 후보 조회는 `.env_local`의 `KIS_REAL_MARKET_DATA_ENABLED=true`와 REAL 시세 자격정보가 필요하며, REAL 계좌 조회 스위치와는 별개입니다. 이 설정은 기본 `false`입니다. `freshness_verified`는 항상 `false`이며 `passes_market_filter`는 거래대금·스프레드 1차 조건만 뜻합니다. `executable`도 항상 `false`입니다. 종목 구독·AI 신호·주문을 생성하지 않으며, 후보 발굴 API의 실제 KIS 환경 지원은 PAPER/REAL별 읽기 전용 확인이 별도로 필요합니다. KRX 종가 단일가 구간과 거래소 휴장 중에는 조회를 거부합니다.

실행 중인 PAPER 시세 감시기에 필터 통과 종목을 추가하려면 별도 잠금 `PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED=true`를 `.env_local`에서 설정한 뒤 `POST /api/v1/market/candidates/KR/subscribe?minimum_turnover=1000000000` 또는 `POST /api/v1/market/candidates/US/subscribe?exchange=NAS&minimum_turnover=1000000`을 사용합니다. 모니터와 해당 시장 WebSocket이 연결된 연속매매 시간에만 동작하며 PAPER 모드와 실전 거래 비활성도 다시 검사합니다. 기존 종목은 제거하지 않고, 감시기 전체 최대 40개로 제한합니다. 응답은 로컬 WebSocket 등록 요청이 전송됐음을 뜻하며 KIS의 등록 승인이나 신호·주문 가능성을 보증하지 않습니다. 감시기 추가는 실시간 시세 취득만 수행하며 `PAPER_AUTOTRADING_ENABLED`와 주문 안전 게이트를 따릅니다. 기본 잠금은 `false`이고, 현재 운영 `.env_local`은 변경하지 않았습니다.

거래일마다 장 시작 5분 전에 후보 감시 구독을 자동 교체하려면 `PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED=true`와 `PAPER_CANDIDATE_AUTO_REFRESH_ENABLED=true`를 모두 설정합니다. KR은 `PAPER_CANDIDATE_MIN_TURNOVER_KRW`, US는 `PAPER_CANDIDATE_MIN_TURNOVER_USD`에 양수 거래대금 기준을 반드시 입력해야 합니다. US 사용 시 `PAPER_CANDIDATE_US_EXCHANGE=NAS|NYS|AMS`도 필수이며 조회 거래소를 하나로 한정합니다. `PAPER_CANDIDATE_LIMIT`은 1~20이며 기본 10입니다. 자동 갱신은 `PAPER_WATCHLIST`로 해당 시장의 모니터 feed가 준비되어 있어야 실행됩니다. 앱이 장 시작 뒤 켜졌거나 장 전에 feed가 연결되지 않으면 해당 세션은 건너뜁니다. PAPER 자동매매가 켜져 있거나 PAPER 모드·실거래 잠금 조건이 어긋나면 갱신하지 않습니다. 순위는 감시 종목 선택에만 쓰며, 장전 스프레드가 없더라도 양수 가격·거래량과 거래대금 기준으로 구독할 수 있습니다. 실제 PAPER 매수 후보 생성에는 별도의 장중 신선 시세·스프레드·전략·승인 모델·위험 검사가 계속 적용됩니다. 모든 자동 갱신 설정은 예제에서 기본 꺼짐이며 운영 `.env_local`에는 적용하지 않았습니다.

후보 목록을 교체하려면 `POST /api/v1/market/candidates/KR/refresh-subscriptions?minimum_turnover=1000000000` 또는 `POST /api/v1/market/candidates/US/refresh-subscriptions?exchange=NAS&minimum_turnover=1000000`을 사용합니다. 이 작업은 `PAPER_AUTOTRADING_ENABLED=false`일 때만 허용하고 Spring PAPER 운영 요약의 보유 종목·미체결 주문을 먼저 확인합니다. 보유 종목 구독은 유지하고, 미체결 주문이 하나라도 있으면 이전 후보 구독 전체를 보존합니다. Spring 응답 누락·오류·형식 불일치는 교체 작업을 중단합니다. 제거 대상은 이 API로 추가된 후보 구독으로 한정하며 `.env_local`의 수동 `PAPER_WATCHLIST` 종목은 유지합니다. 실시간 해제 요청을 보낸 뒤에도 KIS 승인 응답은 아직 검증되지 않았습니다.

### 국내 PAPER 취소 사전조회 점검

KIS가 PAPER 계좌에 취소가능수량 사전조회 서비스를 제공하는지 확인할 때만 아래 읽기 전용 명령을 사용합니다. `KIS_ACCOUNT_READ_ENABLED=true`, `KIS_PAPER_ACCOUNT_READ_ENABLED=true`가 필요하며 PAPER/REAL 주문, REAL 계좌조회, REAL 주문대조 스위치는 `false`여야 합니다. 이 도구는 PostgreSQL 공유 호출 제한을 지키며 GET만 보냅니다.

```powershell
.\services\market-ai\.venv\Scripts\python.exe .\scripts\probe-kis-paper-cancel-preflight.py --transaction-id VTTC0084R
.\services\market-ai\.venv\Scripts\python.exe .\scripts\probe-kis-paper-cancel-preflight.py --transaction-id TTTC0084R
```

`supported`는 조회 서비스가 응답했다는 뜻일 뿐 취소 허용을 뜻하지 않습니다. 실제 취소를 열기 전에는 해당 주문번호·종목·계좌에 맞는 `psbl_qty` 확인과 로컬 취소 제한 검증이 별도로 필요합니다. `unsupported_or_rejected`이거나 응답이 불완전하면 취소를 잠근 채 둡니다. 이 점검 명령은 주문·취소를 보내지 않습니다.

### P5 거래 세션 시세 감시

시장 세션 감시·P4 학습·홀드아웃 예약 작업은 `WakeToRun` 및 `StartWhenAvailable`을 설정합니다. Windows 절전 해제를 허용하고 지정 시각을 놓치면 이후 실행을 시도하지만, 현재 사용자가 로그인되어 있어야 하고 배터리 시작·중지 제한과 Windows 전원 정책을 따릅니다. 이미 등록한 작업은 `enable-local-automation-task-wake.ps1`로 이 설정을 동기화할 수 있습니다.

세션 성공 판정은 tick·봉 카운터만으로 하지 않습니다. 장전 마지막 카운트를 기준으로 개장 첫 봉을 포함하고, 세션 종료 후 감시 종목별 저장 자료에서 해당 거래일의 정확한 정규장 분봉 수, 누락·중복·격자 이탈 0건, 전 분봉 스프레드 존재, 기대한 KIS WebSocket 출처의 분봉만 사용했는지를 다시 검사합니다. KR PAPER는 `KIS_PAPER_WEBSOCKET`, US 시세 감시는 `KIS_REAL_US_WEBSOCKET` 출처와 정확한 세션 분봉 수를 요구합니다. 초기 구독 요청마다 KIS의 성공 확인 응답을 모두 받기 전까지 feed 상태를 `RUNNING`으로 보고하지 않고 `SUBSCRIBING`으로 유지합니다. 응답 거절은 제한된 broker code만 상태에 남기며, 15초 동안 확인이 없으면 연결을 다시 시도합니다. REST 분봉과 혼합되거나 출처 정보가 없으면 실패 처리합니다. 저장 API 오류나 종목 결과 누락도 실패로 처리합니다.

국내와 미국 feed를 각각 검증할 때는 장이 열린 시간에 `-Feed paper` 또는 `-Feed real`을 지정합니다. 이 모드는 선택한 feed의 tick·완료 분봉 누적량과 마지막 수신시각을 별도로 확인하므로, 비선택 feed의 연결 상태·tick·분봉이 선택 feed의 관측을 대신하지 않습니다. `-RequireBothFeeds`는 두 연결 상태를 함께 검사할 때 사용합니다. 미국 장 관측 중에도 `real` feed는 읽기 전용 시세 용도이며, 주문·자동매매 잠금이 꺼진 상태에서는 감시가 거부됩니다.

`.\scripts\test-paper-websocket-monitor.ps1`은 기본 60초, 최대 10시간 동안 로컬 feed를 읽기 전용으로 점검합니다. 전체 한국장 세션은 08:45 KST에 시작해 15:30 KST에 끝나도록 24,300초 동안 실행합니다. 개장 전 08:45~09:00과 종가 동시호가 15:20~15:30의 무수신은 장애 한도에서 제외합니다. 국내 세션은 `-DurationSeconds 24300 -PollIntervalSeconds 15 -MaxNoTickSeconds 300 -Feed paper -MinimumBarsDelta 380`, 미국 세션은 `-Feed real -MinimumBarsDelta 390 -RequireSessionClose`를 사용합니다. `register-market-data-session-monitor.ps1 -Feed paper|real -SessionDate YYYY-MM-DD`는 프로젝트 거래소 캘린더로 해당 날짜의 정규 세션을 확인한 뒤 KRX 09:00 시작 15분 전 또는 NYSE 09:30 시작 5분 전에 일회성 작업을 등록합니다. 미국 시작 시각은 공식 세션 개장 UTC를 KST로 변환하므로 서머타임을 반영합니다. Windows 시간대는 `Korea Standard Time`이어야 합니다. 앞으로 각 시장 10개 거래일의 감시 일정을 보충하려면 `scripts/register-market-data-session-horizon.ps1 -SessionCount 10`을 실행합니다. `-InstallDailyRefresh`를 추가하면 로그인 사용자의 제한 권한으로 매일 00:05 KST에 다음 10개 세션을 보충하는 작업을 등록합니다. 기존 일회성 작업은 경로·feed·예약 시각·제한 권한·깨우기 설정이 기대값과 일치할 때만 유지 대상으로 인정하며, 다르거나 비활성화된 같은 이름의 작업은 교체하지 않고 오류로 중단합니다. 등록기는 시세·계좌 API를 호출하거나 주문을 보내지 않습니다. 해당 Windows 사용자가 로그인해 있고 로컬 API가 실행 중이어야 합니다. monitor는 종료 시각·틱 신선도·전체 세션 분봉(국내 380, 미국 390) 및 주문/자동매매 잠금을 확인하며, 주문을 전송하지 않습니다. 선택 feed의 틱 수신 시각이 5분 넘게 갱신되지 않거나 프로세스가 재시작되어 누적 tick 수가 초기화되거나 완료 분봉 수가 지정한 최소치에 못 미치면 실패합니다. 성공한 표본은 민감정보 없이 `.local/validation/paper-websocket-samples.jsonl` 또는 `.local/validation/us-websocket-samples.jsonl`에 쌓이며 `.local/`은 Git에서 제외됩니다. 이 감시는 실제 KIS 시세를 받으므로 해당 시장의 거래 시간에 실행하고, 종목 거래정지·시장휴장 시 `MaxNoTickSeconds`를 무리하게 낮추지 마세요.

`-Feed paper` 또는 `-Feed real`을 지정한 표본은 해당 feed의 연결과 틱만 통과 조건으로 봅니다. 다른 feed가 재연결 중이면 API의 집계 상태에 `KIS_STREAM_UNAVAILABLE`가 표시될 수 있으나, 선택한 feed가 `RUNNING`인 동안에는 선택 feed 표본을 계속할 수 있습니다. `-RequireBothFeeds` 또는 feed 미지정 표본은 집계 상태와 모든 요구 feed가 정상이어야 합니다. 주문 잠금 또는 자동매매 비활성 조건은 모든 모드에서 계속 검사합니다.

한국거래소 정규장 연속매매는 09:00~15:20이고 15:20~15:30은 종가 동시호가입니다([KRX 매매거래시간](https://global.krx.co.kr/contents/GLB/06/0602/0602010204/GLB0602020204T1.jsp)). `-Feed paper` 감시는 이 동시호가 동안 무수신을 별도 누적하고 5분 장애 기준에서는 제외하며, 15:30에 표본을 마칩니다. 마지막 연속매매 틱이 동시호가 직전에 온 경우는 feed 장애로 판정하지 않습니다. 이 구간의 실제 종가 체결 틱 수신은 별도 검증 범위입니다.

### KIS PAPER 주문 수명주기 통합 확인

KIS PAPER 주문·취소 통합 확인은 `scripts/test-kis-paper-order-lifecycle-isolated.ps1`을 사용합니다. 이 스크립트는 `.env_local`의 PAPER/REAL 주문 및 자동매매 잠금이 모두 `false`인 것을 확인하고, 별도 loopback 포트(기본 18081)에 임시 Spring Boot 프로세스를 시작합니다. PAPER 주문만 그 프로세스에서 허용하고 REAL 키·계좌번호는 빈 값으로 덮어씁니다. 테스트 종료 시 임시 프로세스를 끄고 호출 PowerShell 환경을 복원하며 `.env_local`을 수정하지 않습니다. 우선 `-PreflightOnly`로 확인합니다. 현재 국내 PAPER 취소 가능수량 조회용 모의 TR 지원이 확인되지 않아 `KR -ConfirmPaperOrderAndCancel`은 두 스크립트에서 네트워크 요청 전에 거부됩니다. 미국은 주문가능 USD가 충분한 경우에만 장중 `-ConfirmPaperOrderAndCancel`을 실행할 수 있습니다. 두 모드는 상호 배타적입니다.

주문 제출 모드는 Python의 `/api/v1/market/sessions/{market}` (`market=KR` 또는 `US`)에서 받은 최근 거래소 캘린더 확인을 요구합니다. 휴장일·장외·캘린더 오류·5분 이내 폐장·오래된 세션 응답이면 주문 전에 중단합니다. 읽기 전용 `-PreflightOnly`는 장외에서도 실행할 수 있습니다.

```powershell
.\scripts\test-kis-paper-order-lifecycle-isolated.ps1 -Market KR -Symbol 069500 -LimitPrice 75000 -PreflightOnly
```

지원 확인된 시장의 모의 주문·취소는 같은 인수에 `-PreflightOnly` 대신 `-ConfirmPaperOrderAndCancel`을 지정합니다. 국내 모의 주문은 취소까지 안전하게 확인할 수 있게 될 때까지 사용하지 마세요.

1주 매수 지정가만 허용하며 KIS 시세와 종목별 주문가능금액의 `retrieved_at`이 현재 시각 기준 60초 이내이고, 최근 시세보다 5% 이상 낮은 가격, 설정 한도와 별도 PAPER 테스트 한도(국내 10만원/미국 100달러) 안에서만 진행합니다. 시세·계좌 조회가 누락되거나 오래됐거나 잔액이 부족하면 주문 전에 중단합니다. Spring은 POST 직전 주문가능금액을 다시 조회합니다. 응답이 끊기면 새 주문을 보내지 않고 같은 멱등 키를 조회하며, 즉시 취소 후 상태를 대조합니다. PAPER 주문은 모의 계좌 상태를 바꾸므로 체결되면 모의 포지션을 정리하세요. 시험 로그는 `.local/logs`에 보관됩니다.

주문을 보내지 않고 현재 PAPER 시세와 종목별 주문가능금액만 읽기 전용으로 확인하려면 `.env_local`의 주문 잠금을 그대로 둔 채 `scripts/test-kis-paper-order-lifecycle.ps1`에 `-PreflightOnly`를 전달합니다. 예: `-Market KR -Symbol 069500 -LimitPrice 75000 -PreflightOnly`. 이 모드는 주문 잠금이 꺼져 있는 Spring 설정만 허용하고, 시세와 계좌 확인이 모두 최신·충분할 때만 통과합니다.

주문 전 시세·계좌 사전검사만 로컬로 회귀 검증하려면 `Invoke-Pester -Path scripts/tests/kis-paper-order-preflight.Tests.ps1`을 실행합니다. 이 테스트는 KIS API를 호출하지 않습니다.

KIS 접근 토큰 요청은 Spring과 Python이 PostgreSQL `kis_token_issue_rate_limit_slot`에서 환경·앱키 SHA-256 지문을 기준으로 함께 예약합니다. Python의 시세와 계좌 요청은 동일 환경별 클라이언트와 토큰 캐시를 공유합니다. 토큰 요청은 보수적으로 앱키별 최소 60초 간격이며, 일반 REST 요청은 별도 제한 슬롯을 사용합니다. PAPER·REAL REST 호출은 각 앱키별 최소 1초 간격입니다. 이 마이그레이션은 Spring 기동 시 Flyway가 적용합니다. Java와 Python은 KIS가 HTTP 500과 `EGW00201`을 함께 반환하면 호출 속도 제한으로 분류해 HTTP 429로 응답합니다. 제한 응답을 받은 즉시 자동 재요청하지 말고 충분히 기다린 뒤 읽기 전용 확인을 다시 실행합니다. KIS 호출량 안내는 [공식 API 포털](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000001/post/d0d1a83f-6f8d-4437-9700-6d26702fd989)을 기준으로 관리합니다.

## 실전 주문 게이트

기본은 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`입니다. REAL 내부 오케스트레이터는 REAL 자격정보와 전체/REAL 계좌 읽기 스위치, 세 주문 잠금, 해당 주문과 3초 이내에 결합된 서버 내부 위험 승인이 있어야만 주문 요청을 보냅니다. 신규 매수의 동시 보유 한도는 국내·미국 KIS 잔고를 모두 읽어 합산합니다. 어느 한 시장의 잔고가 없거나 오래됐거나 종목 중복으로 모호하면 `ALL_MARKET_POSITION_COUNT_REQUIRED`로 차단합니다. 주문 의도를 먼저 DB에 기록하고, 부분체결 취소에는 잔량을 쓰며, 불명 결과는 자동 재전송하지 않습니다. 주기적 실계좌 체결 조회는 별도 잠금으로 기본 비활성입니다. UNKNOWN 중 주문번호가 없는 건은 자동 추측·재주문하지 않습니다. P6의 paper 세션·장애 검증 전까지 세 주문 잠금을 열지 않습니다.

REAL 주문 위험검사는 Python AI·시세 API에서 현재 활성·승인·데이터 권리·검증 증거가 모두 유효한 모델과 요청 종목의 최근 `BUY_CANDIDATE` 결정을 확인합니다. 신호는 승인된 동일 모델이 생성하고 120초 이내여야 하며, ATR 손절 거리도 양수여야 합니다. KIS REST 시세는 출처·종목·시장과 3초 이내 시각을 확인합니다. API 장애, 불완전 응답, 종목 불일치 또는 오래된 값은 매수를 차단합니다. 이 API 연결과 위험 검사는 전체 실전 운용 검증을 대신하지 않습니다.

REAL 연속 손실 횟수는 서버가 세션 시작 보유원가와 확정된 KIS 주문 체결만으로 재구성합니다. 평균원가 방식으로 각 매도 순손익을 계산하고 국내·미국 시장 결과를 체결 확인 시각 순으로 합칩니다. KIS가 제공하는 체결 시각 자체를 저장하는 원장은 아직 별도 검증되지 않았으므로, 이 정렬은 서버가 체결을 확인한 시각을 사용합니다. 장부상 보유 수량이 현재 양 시장 KIS 잔고와 다르면, 진행 중 체결이 있으면, 어느 시장의 기준선이나 비용 설정이 없으면 매수를 차단합니다. 계좌별 비용률은 확인 후 `.env_local`에 다음 여섯 항목을 설정해야 하며 기본값은 비어 있습니다.

```dotenv
KIS_REAL_KR_BUY_COMMISSION_BPS=
KIS_REAL_KR_SELL_COMMISSION_BPS=
KIS_REAL_KR_SELL_TAX_BPS=
KIS_REAL_US_BUY_COMMISSION_BPS=
KIS_REAL_US_SELL_COMMISSION_BPS=
KIS_REAL_US_SELL_TAX_BPS=
```

각 값은 매매금액 대비 basis points(1 bps = 0.01%)로 입력합니다. 체결 단가에는 실제 체결가를 쓰고, 수수료·매도세금은 설정한 계좌별 추정률을 적용합니다. KIS 체결통보에는 실제 수수료·세금이 없어 이 결과는 계좌 명세와 대조되기 전까지 추정치이며, 손익 기능만으로 실전 전환을 승인하지 않습니다. KIS 공식 기간손익은 해외 매매일 다음 날부터 제공되므로 장중 연속 손실 계산의 대체 자료로 사용하지 않습니다. [국내 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_domstk.py), [해외 공식 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py).

REAL 주문의 누적 체결 관측은 Flyway V19 `kis_real_fill_snapshot`에 멱등 키와 체결 누적 수량별로 저장합니다. 동일 수량의 가격 정정은 최신 관측으로 갱신하고, 이전보다 낮은 누적 체결 응답은 주문 상태와 기록 모두를 후퇴시키지 않습니다. 연속 손실 계산은 최종 주문 상태와 평균 체결가를 사용하고, 해당 스냅샷은 부분체결 재시작 복구 근거로 보존합니다. 실제 KIS 주문 조회는 별도 읽기 잠금이 열리기 전까지 실행되지 않습니다.

Flyway V20은 KR·US 각 거래소 개장 후 3분 안에 REAL 계좌의 보유 종목·수량·KIS 보고 평균 매입단가를 계좌 지문과 세션별 불변 기준선으로 저장합니다. 빈 포트폴리오도 0건 기준선으로 저장해 “아직 기록 없음”과 구분합니다. 캘린더·계좌 응답이 오래됐거나 기준 데이터가 불완전하면 저장하지 않습니다. 이 초기 보유 원가는 향후 체결 원장 계산용이며, 그 자체로 당일 손익이나 거래 비용을 포함하지 않습니다. REAL 읽기 스위치가 꺼져 있으면 KIS 요청은 발생하지 않습니다. KIS는 잔고 응답에서 종목별 매입 평균가를 제공하며([공식 국내 잔고 예제](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_domstk.py)), 해외 기간손익은 매매일 익일부터 가능하므로 ([공식 해외주식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py)) 당일 거래별 손실 연속 계산에 직접 사용하지 않습니다.

REAL 국내 일일 손실 기준선은 REAL 계좌 읽기가 활성화된 경우에만 거래소 캘린더 API가 확인한 개장 후 3분 동안 최초 KIS 순자산을 한 번 저장합니다. 계좌 읽기 스위치가 꺼져 있거나 FastAPI·PostgreSQL을 사용할 수 없으면 기준선은 만들어지지 않으며 REAL 매수는 일일 손실 기준 미확인으로 차단됩니다. 기준선 누락을 해소하려고 임의로 세션 중간에 시작 잔고를 기록하지 않습니다.
REAL 주문 상태의 읽기 전용 주기 대조는 `KIS_REAL_RECONCILIATION_ENABLED=false`가 기본입니다. 활성화하려면 REAL 계좌 읽기 스위치와 자격정보도 필요합니다. 주문번호가 저장된 접수/부분체결/취소대기 주문만 조회하며, 주문번호 없는 UNKNOWN은 자동 추측·재주문하지 않습니다.

PAPER/REAL 취소 요청이 KIS에서 명시적으로 거절되면 안전 응답 코드를 주문 의도에 기록하고, 추가 취소 요청 없이 해당 주문을 읽기 전용 조회합니다. 조회가 실패하면 `CANCEL_PENDING` 상태를 유지합니다. 이후 대조에서 열린 잔량이 확인되면 상태가 `ACCEPTED`/`PARTIALLY_FILLED`로 돌아가므로 운영자가 다시 취소할 수 있습니다. 취소 API 응답이 불명확한 경우에는 자동 재전송하지 않습니다.

## 로컬 PostgreSQL 통합 테스트

저장소 루트에서 `.\scripts\test-local-postgres-integration.ps1`을 실행하면 `.env_local`의 DB 설정으로 PAPER/REAL 주문 의도 저장·재조회, 환경 격리, 멱등 키 복구 테스트를 선택 실행합니다. 테스트는 KIS API를 호출하지 않으며, 스크립트는 환경변수를 현재 프로세스에서만 적용하고 실행 후 복구합니다. 기본 `mvnw test`에서는 해당 DB 통합 테스트를 건너뜁니다.

별도 Java 프로세스 종료 후 복구까지 점검하려면 `.\scripts\test-paper-process-restart.ps1`을 실행합니다. 로컬 PostgreSQL에 고유 멱등 키의 PAPER 대기 주문을 만든 뒤 Spring JVM을 종료하고 새 JVM에서 동일 주문을 조회·재제출해 ID와 상태가 유지되는지 확인한 후 주문을 취소합니다. 이 스크립트는 KIS API를 호출하지 않습니다.

PostgreSQL 프로세스 장애를 격리된 임시 인스턴스로 점검하려면 `.\scripts\test-postgres-outage-recovery.ps1`을 실행합니다. 이 스크립트는 Windows PostgreSQL 16 바이너리를 사용해 임시 클러스터를 만들고, 해당 DB와만 연결하는 Spring API를 띄워 대기 PAPER 주문 저장, DB 중단 시 안전한 HTTP 503, DB 재시작 후 주문 복구를 확인합니다. 설치된 `localhost:5432` DB와 KIS API는 사용하지 않으며, 실전·모의 KIS 주문과 실전 계좌조회는 잠금이 확인된 경우에만 진행됩니다.

## 과거 데이터 백테스트와 학습

데이터 권한을 확인한 1분봉 CSV는 다음 열을 사용합니다: `timestamp,open,high,low,close,volume,spread_bps,symbol`. `timestamp`는 `2026-11-05T09:00:00+09:00`처럼 시간대 오프셋을 포함해야 합니다. `--market KR|US` 값에 따라 서울 또는 뉴욕 거래소 날짜로 세션을 묶으므로, 원본 시각이 한국 시간이어도 미국 세션의 날짜가 잘못 나뉘지 않습니다. 한 CSV에는 한 종목만 넣고 세션 안에서 시간순으로 정렬합니다. `spread_bps`는 bid-ask 전체 스프레드 기준입니다. 백테스트는 다음 봉의 스프레드 절반을 매수 체결가에, 청산 봉의 스프레드 절반을 매도 체결가에 반영합니다. 다음 봉 스프레드가 없거나 상한보다 크면 진입 체결을 만들지 않고, 청산 봉 스프레드가 없으면 설정된 스프레드 상한을 비용으로 사용합니다. 학습 레이블은 현재·미래 봉의 관측 스프레드 비용도 차감하며, 미래 스프레드가 없으면 해당 표본을 제외합니다. `symbol` 열을 넣으면 요청한 값과 일치하는지도 확인합니다.

스프레드 체결 모델은 백테스트 버전 2입니다. 버전 1 기준으로 승인된 구형 모델은 새 비용 가정에서 성과를 재검증하기 전까지 승인·활성 모델로 선택되지 않습니다. 왕복 비용 설정은 여전히 사용자의 실제 계좌 수수료·세금·거래소 비용 확인을 대신하지 않습니다.

백테스트 `gross_pnl`은 기준 시세 가격 차이이고, `costs`에는 추정 슬리피지·관측 또는 대체 스프레드·매수/매도 수수료를 합산합니다. `net_pnl`은 `gross_pnl - costs`입니다.

KIS에서 국내 정규 연속매매 분봉을 읽기 전용으로 확인할 때는 `GET /api/v1/market/sessions/KR/{symbol}/bars?session_date=YYYY-MM-DD`를 사용합니다. 단일 120개 페이지를 최대 6회까지 거슬러 조회하며, 한국거래소 연속매매 구간(09:00~15:20)만 반환합니다. 종가 단일가 시각(15:20~15:30)에 해당하는 체결봉은 전략 데이터에서 제외하고, 장중 거래정지로 인한 공백은 보정하지 않고 백테스트·학습에서 거부합니다. API 분봉에는 과거 호가 스프레드가 없어 해당 값은 비어 있으며, 전략은 스프레드 필터가 있는 진입을 생성하지 않습니다. 로컬 연동 확인은 저장·주문 없이 `python scripts/test-paper-kis-session.py --session-date YYYY-MM-DD`로 실행할 수 있습니다. [KIS Developers API 서비스 소개](https://apiportal.koreainvestment.com/about-open-api)는 개인 고객의 시세 정보 이용을 자기 자산 투자 목적으로 한정하고 제3자 제공을 금지하므로, 데이터와 파생 파일은 로컬 개인 용도에 한정합니다.

PostgreSQL에 저장된 특정 거래일 분봉의 완전성과 스프레드 포함률은 읽기 전용 감사기로 확인합니다. 예를 들어 미국 AAPL 2026-11-06 세션은 `.\services\market-ai\.venv\Scripts\python.exe .\scripts\audit-market-session.py --market US --symbol AAPL --session-date 2026-11-06`을 실행합니다. 국내는 `--market KR --symbol 005930`으로 지정합니다. `PRE_OPEN`/`COLLECTING`은 아직 정규장이 끝나지 않은 상태이며, 폐장 후 `COMPLETE`만 예상 연속매매 분(미국 390분, 국내 380분)이 중복·누락·시간 격자 이탈 없이 저장됐다는 뜻입니다. `--require-complete`를 붙이면 완전한 전체 세션이 아닐 때 종료 코드 2를 반환합니다. 스프레드 기준이 필요한 분석은 `--minimum-spread-coverage-percent 95`처럼 최소 포함률을 지정할 수 있으며, 기준 미달 또는 확인할 자료가 없으면 같은 종료 코드 2로 닫힙니다. 이 도구는 조회만 하며 누락 봉을 보정하거나 DB를 수정하지 않습니다. 완전성은 백테스트·학습 적격성의 한 조건일 뿐이며, 임계치는 사용할 전략의 비용 가정에 맞춰 선택한 뒤 CSV export와 rights manifest 검토 절차를 진행합니다.

같은 감사 결과는 Python API `GET /api/v1/market/session-quality/{market}/{symbol}?session_date=YYYY-MM-DD`에서도 조회할 수 있습니다. 이 경로는 PostgreSQL의 읽기 전용 분봉만 확인하며, 저장 기능이 꺼져 있으면 `503`으로 닫힙니다. 거래소 비영업일은 DB를 조회하지 않고 `NON_SESSION`으로 반환합니다.

감시 중인 시장의 여러 종목을 한 번에 감사하려면 `GET /api/v1/market/session-quality/{market}?symbols=005930&symbols=069500&session_date=YYYY-MM-DD`를 사용합니다. 세션 날짜를 직접 지정하지 않고 `latest_closed=true`를 주면 거래소 현지 날짜 기준으로 아직 폐장하지 않은 세션을 건너뛰고 최근 마감 세션을 선택합니다. 최근 30일 안에서 거래일을 확인할 수 없으면 `503`으로 닫힙니다. 이 API는 종목 수를 최대 50개로 제한하고 단일 읽기 전용 DB 쿼리로 결과를 묶습니다. 운영 대시보드는 현재 구독 종목의 분봉 완전성·누락 수·스프레드 포함률과 선택된 거래일을 보여 주며, API나 저장소가 응답하지 않으면 품질을 확인 불가로 표시합니다.

```powershell
.\services\market-ai\.venv\Scripts\python.exe .\scripts\backtest-quant.py .\data\licensed-bars.csv --market KR --symbol 005930 --commission-bps 5 --slippage-bps 5
.\services\market-ai\.venv\Scripts\python.exe .\scripts\train-ai-models.py .\data\licensed-bars.csv --market KR --symbol 005930 --commission-bps 5 --slippage-bps 5
```

AI 학습 레이블의 기본 왕복 비용 가정은 20 bps이고, 백테스트 체결당 수수료와 슬리피지는 각각 5 bps입니다. 이는 예시 초기값이며 계좌별 실제 요율을 확인한 뒤 조정하세요. 모델 manifest에는 종목, label horizon, 비용과 유동성·손절 설정을 저장합니다. 승인에는 5개 이상의 서로 다른 held-out 세션과 5개 이상의 AI+퀀트 체결이 필요하고, 해당 AI+퀀트 순손익은 양수이며 같은 기간의 퀀트 기준선보다 커야 합니다.

로컬 PostgreSQL의 새 데이터를 안전하게 다시 학습 후보로 만들려면 아래 실행기를 사용합니다. 날짜는 시장 현지 날짜이며, CSV와 보고서는 고유한 실행 ID로 로컬 전용 `data/model-candidates/`와 `.local/models/retraining/`에 생성됩니다. 학습은 세션 완전성과 권리 manifest를 다시 확인하고, 중복 실행이 기존 CSV를 덮어쓰지 않습니다. 생성 후보는 항상 미승인 상태이며, 별도 held-out 성능 검토와 모델 거버넌스 승인이 없으면 운영에 선택되지 않습니다.

```powershell
.\scripts\run-model-retraining.ps1 -Market US -Symbol AAPL -FromDate 2026-11-02 -ToDate 2026-11-06
```

실행 보고서에서 `candidate_created_review_required`가 아니면 후보가 만들어졌다고 간주하지 마세요. 실패 보고서는 실행 전후의 모델 manifest와 artifact 파일을 비교해 이번 실행에서 새로 생긴 후보와 고아 파일을 표시합니다. 실패 뒤 새 artifact가 남으면 그 파일을 검토·정리하기 전까지 승인이나 활성화를 진행하지 마세요.

사전에 세션 자료가 3개 이상 모일 것으로 예상되는 범위만 자동 후보 학습 작업으로 등록합니다. `register-model-candidate-retraining-task.ps1`는 거래소 캘린더에서 3개 이상 거래일인지 확인하고 `.env_local`의 PAPER 모드·주문 잠금을 확인합니다. 예약 실행 시에도 재학습·홀드아웃 스크립트가 `.env_local`에서 안전 스위치만 다시 읽어 PAPER 모드, PAPER/REAL 주문 비활성, PAPER 자동매매 비활성을 확인합니다. 하나라도 빠졌거나 켜져 있으면 데이터 내보내기 전에 종료합니다. 다음 날 08:15 KST부터 실행하지만, 실제 학습은 내보내기 단계에서 권리 manifest와 완전한 세션 데이터가 다시 확인된 경우에만 진행됩니다. 예시 작업은 2026-11-09~11-11 데이터를 대상으로 11월 12일 아침에 KR 005930과 US AAPL 후보 생성을 각각 시도합니다. 이 작업은 후보를 자동 승인하거나 활성화하지 않으며, 주문 API를 호출하지 않습니다. Windows 사용자가 로그인하고 PostgreSQL이 실행 중이어야 합니다.

퀀트 결과는 세션별 JSON과 CSV SHA-256을 출력하며 주문 실행 권한이 없습니다. AI 학습도 입력 CSV SHA-256을 각 모델 manifest에 기록합니다. 각 백테스트 세션은 1주 수량으로 독립 계산하고, 포트폴리오 자본 복리·계좌별 세금·거래소 비용·시장충격은 별도 검증해야 합니다. 조정주가, 종목코드 변경, 분봉 누락, 데이터 이용·저장·재배포 권한은 CSV 제공자가 확인해야 합니다.

`test-postgres-outage-recovery.ps1`는 `.env_local`을 로드한 테스트 프로세스의 기존 환경 변수 전체를 보존하고, 새로 만든 변수까지 종료 시 제거합니다. 실행 실패 중에도 격리 PostgreSQL과 Spring 프로세스를 정리하고 환경을 복원합니다.

## 2026-11-07 KIS 계좌 읽기 연동 확인

PostgreSQL 16.4에 V18–V20 migration을 적용했습니다. KIS PAPER 국내·미국 잔고와 매수가능액 GET 4건, REAL 국내·미국 잔고 GET 2건이 통과했습니다. PAPER에서는 KRW 70,000 기준의 매수가능액이 충족됐고 USD 11 기준은 충족되지 않았습니다. 계좌 잔고 금액, 계좌번호, 키는 로그와 문서에 기록하지 않았습니다. 모든 검증에서 주문·취소는 호출하지 않았으며, 이는 조회 API 연결 증거로만 봅니다.
