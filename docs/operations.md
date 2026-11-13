# Windows 운영

> **현재 시장 범위:** 해외 기능 코드는 계속 개발하지만, 실행하는 기능 테스트와 KIS 계좌·시세 연결, 세션 데이터 검증, PAPER/REAL 실행, 백테스트·학습 후보 운영은 국내(KR)만 대상으로 합니다. `MARKET_OPERATIONAL_SCOPE=KR`가 기본값이며 Spring/FastAPI API와 Python 직접 실행형 데이터 감사·내보내기·백테스트·학습·LEAN 준비 명령이 해외 시장 범위 밖의 작업을 거부합니다. 해외 계좌나 시장 세션에 연결하는 테스트·운영 명령은 실행하지 않습니다. 해외 기능의 구조와 코드는 유지합니다.

## 로컬 실행

1. PostgreSQL 서비스가 `localhost:5432`에서 실행 중인지 확인합니다. `postgresql.conf`의 `listen_addresses`는 `localhost`여야 합니다. `start-local.ps1`은 지정된 포트의 listener가 loopback 전용이 아니면 Spring을 실행하지 않고, `restart-local-stack-task.ps1`도 같은 주소 검사를 마친 뒤에만 기존 API를 중지합니다. 설정을 바꾸려면 관리자 권한 PowerShell에서 저장소 루트로 이동해 `powershell.exe -ExecutionPolicy Bypass -File scripts/configure-postgres-loopback.ps1`을 실행합니다. 스크립트는 현재 설정을 `C:\ProgramData\AIStockTrader\backups`에 백업하고 localhost로 바꾼 뒤 서비스를 재시작해 모든 listener가 loopback인지와 `pg_isready`가 연결을 받아들이는지 확인합니다. 검증에 실패하면 원본 설정 파일을 해시로 대조 복구하고, 서비스와 로컬 DB 연결이 복구됐는지 확인합니다. 실행 전 확인 메시지가 표시되며 PostgreSQL을 사용하는 로컬 앱의 연결이 잠시 끊깁니다. 고정된 비루프백 설정 오류는 실행기가 재시도를 반복하지 않고 `BLOCKED`로 종료합니다. 설정 성공 후 현재 사용자 PowerShell에서 `.\scripts\activate-local-stack.ps1 -Confirm:$false`를 실행합니다. 이 명령은 loopback, PAPER 주문·자동매매 잠금, 작업 대상 경로를 검증한 후 비활성 작업을 켜고 Spring/FastAPI의 로컬 health 및 거래 잠금을 확인합니다.
2. 저장소 루트의 Git 제외 `.env_local`에 DB 비밀번호와 필요한 KIS 환경별 설정을 입력합니다.
3. Spring Boot를 실행합니다: `.\scripts\start-local.ps1`
4. Python API는 `services/market-ai` 가상환경에서 실행합니다. API는 `127.0.0.1`에만 바인딩합니다.

### Windows 로그인 시 Dashboard 자동 시작

`AIStockTrader-Dashboard` 작업은 현재 Windows 사용자의 대화형 로그인 때 Dashboard를 시작합니다. 먼저 로컬 스택과 Dashboard production build가 준비되어 있어야 합니다. 설치·시작은 `.\scripts\install-dashboard-task.ps1`, 제거는 `.\scripts\remove-dashboard-task.ps1`입니다. 포트 13000을 점유한 프로세스가 저장된 작업 상태와 일치하는 저장소의 Next.js loopback 프로세스이고 이전 감시 프로세스가 종료된 경우에는 새 작업이 그 프로세스를 인계해 감시를 복구합니다. 그 밖의 포트 점유는 그대로 두고 시작을 거부합니다. Spring/FastAPI의 상태와 PAPER·주문 잠금을 확인할 수 없거나 예상한 값이 아니면 Dashboard를 시작하지 않습니다. 실제 주문과 PAPER 자동매매를 활성화하는 기능은 없습니다. 작업은 관리자 권한 없이 현재 사용자 권한으로 실행되며, 로그는 `.local\logs\dashboard-task.log`에 남습니다.

Java 빌드는 Gradle이 아닌 프로젝트 포함 Maven Wrapper를 사용합니다: `.\backend\trading-api\mvnw.cmd test`.

## GitHub Actions 검증

기능 변경 중에는 변경된 기능에 대응하는 집중 테스트와 필요한 안전 검증만 실행합니다. `Final integration quality` 전체 Python·Spring/PostgreSQL·대시보드 검증은 최종 `main` 통합 pull request 또는 수동 `workflow_dispatch`에서 실행하며 `develop` push마다 실행하지 않습니다. Spring CI에는 KIS 자격정보를 넣지 않으며 KIS 실계좌/PAPER 통합 플래그는 켜지지 않습니다. 별도 Windows PAPER 사전검증은 PowerShell/Python 운영 스크립트가 바뀐 경우에만 실행합니다.

paper ledger 비용 추정은 `PAPER_COST_MODEL_ENABLED=false`가 기본입니다. 실제 적용 요율을 확인한 뒤 KR/US 매수·매도 수수료율을 basis points로 설정하고 `PAPER_COST_MODEL_ENABLED=true`로 켭니다. 한국·미국 계좌별 우대 수수료, 거래소 및 규제 비용은 다를 수 있으므로 예제의 0 값은 실거래 요율을 의미하지 않습니다. 비활성 상태에서는 P&L을 총액으로만 해석합니다.

P3/P4 재현용 비용 가정은 `.env_local`의 시장별 `BACKTEST_*` 키 다섯 개로 별도 관리합니다. `/api/v1/health`와 대시보드는 KR/US별 설정 가능 여부와 누락·범위 오류가 있는 환경변수 이름만 보여 주며 수수료 값이나 비밀정보를 노출하지 않습니다. 표시가 `입력 완료`여도 실제 수수료표를 확인했다는 뜻은 아니므로 사용자가 증권사 계좌에 적용된 요율을 검토해야 합니다.

2026-11-09 확인한 [KIS Open API 목록](https://apiportal.koreainvestment.com/apiservice-summary)과 [공식 샘플 저장소](https://github.com/koreainvestment/open-trading-api)에서는 계좌별 적용 수수료율을 반환하는 조회 API를 찾지 못했습니다. 이는 문서 목록을 기준으로 한 확인 결과이며, API 제공 여부가 바뀔 수 있습니다. 계좌별 우대 수수료는 KIS 앱/홈페이지의 계좌별 수수료 안내 또는 체결 후 손익·비용 명세에서 확인한 뒤 설정해야 합니다. 요율을 확인할 수 없으면 임의의 0이나 일반 요율을 넣지 말고 비용 가정을 미완료로 둡니다.

[KIS 공식 FAQ](https://securities.koreainvestment.com/main/customer/support/_static/TF04fg000000.jsp?mctgr=03&sctgr=%25)는 국내주식 온라인 수수료가 계좌관리점에 따라 달라질 수 있다고 안내합니다. 한국투자앱에서 국내는 `메뉴 > 자산 > 나의 총 자산 > 해당 계좌 우측 점 세 개 > 계좌정보`, 해외는 `메뉴 > 고객지원 > 고객센터 > 수수료 안내 > 온라인매매 수수료 > 영업점 주식 > 해외주식` 경로로 확인합니다. 홈페이지에서는 `뱅킹/청약/대출 > 조회 > 자산현황/잔고 > 자산현황 > 유잔고 > 전체 > 관리점`에서 관리점을 확인하고 `이용안내 > 업무 이용안내 > 수수료안내 > 온라인매매(수수료)`의 요율표와 대조합니다. FAQ는 이벤트 참여 등으로 실제 온라인 수수료가 달라질 수 있으므로 관리점에 확인하라고 안내합니다. 화면명은 앱 버전에 따라 바뀔 수 있습니다.

현재 공개 KIS 요율표(2025-10-27 기준)는 영업점 계좌의 국내 온라인 KRX 기본 수수료를 0.147%(14.7 bps), BanKIS 계좌를 0.0140527%로 구분합니다. 2026년 국내 KRX 매도 세금은 KOSPI 합계 0.20%(증권거래세 0.05% + 농어촌특별세 0.15%), KOSDAQ 0.20%입니다. 로컬 `.env_local`의 KR 백테스트 설정은 매수 14.7, 매도 14.7, 매도세 20, 편도 슬리피지 10 bps, 왕복 69.4 bps이며 산술식은 일치합니다. 다만 14.7 bps는 공개 영업점 기본요율을 대입한 값이라 사용자의 `위탁(국내수수료우대)` 계좌 적용 수수료로 확인된 값이 아닙니다. 슬리피지 10 bps/편도 역시 실제 체결 관측값이 아닌 초기 가정입니다. 따라서 설정 상태는 계산 가능한 입력을 뜻할 뿐 실제 비용 검증이나 P3 성과 승인 근거가 아닙니다. [KIS 온라인 수수료·세금표](https://securities.koreainvestment.com/main/customer/guide/_static/TF04ae010000.jsp?tab=2), [KIS 계좌별 수수료 FAQ](https://securities.koreainvestment.com/main/customer/support/_static/TF04fg000000.jsp?mctgr=03), [KRX 공시의 2026년 거래세율 안내](https://kind.krx.co.kr/external/2026/01/15/000535/20260115001373/10002.htm)

백테스트 키는 시장별로 `BACKTEST_{KR|US}_BUY_COMMISSION_BPS`, `BACKTEST_{KR|US}_SELL_COMMISSION_BPS`, `BACKTEST_{KR|US}_SELL_TAX_BPS`, `BACKTEST_{KR|US}_SLIPPAGE_BPS`, `BACKTEST_{KR|US}_ROUND_TRIP_COST_BPS`입니다. 1 basis point는 0.01%입니다. 매수·매도 수수료, 매도세, 편도 슬리피지를 입력하고, `ROUND_TRIP_COST_BPS`에는 AI 학습 레이블에 사용할 왕복 총비용을 입력합니다. 왕복 총비용은 매수·매도 수수료와 매도세 및 왕복 슬리피지 합보다 낮을 수 없고, 이 값을 다른 거래비용에 다시 더하지 않습니다. PAPER ledger의 `PAPER_*_BPS` 키와도 별도입니다. 실제 계좌 비용이 확인되기 전에는 비용 포함 손익·모델 성능·실전 준비 판정을 완료로 보지 않습니다.

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
- Python 실시간 시세 릴레이는 `KIS_WEBSOCKET_ENABLED=true`를 요구합니다. 국내 paper feed는 PAPER 자격정보를 사용합니다. 미국 시세 feed 구현은 유지하지만 현 운영 범위에서 미국 REST/WebSocket을 활성화하거나 PAPER ledger에 연결하지 않습니다. 관련 환경변수는 기본 `false`로 둡니다.
- 미국 REAL WebSocket 구현은 유지하지만 현재 테스트·운영 검증 범위가 국내로 한정되어 있어 `scripts/probe-kis-real-us-websocket.py`는 실행을 차단합니다. 해외 기능은 코드 개발만 진행하고, 검증 실행은 국내 범위에서 수행합니다.
- 국내 REAL 체결가 WebSocket 구독 경로도 확인하려면 `scripts/probe-kis-real-kr-websocket.py --symbol 005930`을 사용합니다. KIS 공식 예제 기준 국내 실시간 체결 TR은 실전에서 `H0STCNT0`이며, 동일한 안전 잠금·환경 격리·중복 REAL 소켓 차단을 적용합니다. 기본 모드는 구독 ACK만 확인하고 장중 tick 검증은 `--wait-for-tick-seconds 60`처럼 지정합니다. probe는 REAL 시세 자격정보만 사용하고 REAL/PAPER 주문, 계좌 읽기, 시그널·자동 보호매도 설정을 모두 제거하거나 잠급니다. 네트워크 응답과 원시 시세는 기록하지 않으며, 단독 probe는 관리형 REAL feed가 멈춰 있을 때만 허용됩니다. [KIS 공식 국내 WebSocket 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_user/domestic_stock/domestic_stock_functions_ws.py)
- 국내 REAL REST 시세 스냅샷을 별도 확인하려면 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-real-kis-us-market-data.ps1 -EnableReadOnlyGetForThisRun -Market KR`을 사용합니다. 기본 시장은 KR이며 현재 US 또는 ALL 범위는 요청 전에 차단합니다. 이 1회성 opt-in은 현재 프로세스에서만 적용되며, 실행 모드는 PAPER로 고정하고 실주문·모의주문·자동매매·REAL 계좌조회·REAL 주문대조가 모두 false여야 합니다. KIS REAL 앱 키/시크릿 외 계좌번호와 PAPER 키·계좌번호는 자식 프로세스에서 제거하고, PostgreSQL 공유 호출 제한을 사용합니다. 로컬 `.env_local`은 바꾸지 않으며 출력은 성공 여부·종목/거래소·통화·조회시각만 포함하고 실제 가격, 원시 응답, 키, 토큰, 계좌정보는 출력하지 않습니다. 주문 API와 계좌 API는 이 도구에 연결되어 있지 않습니다.
- REAL 신호 감시기는 `TRADING_MODE=real`, `REAL_SIGNAL_MONITOR_ENABLED=true`, `KIS_WEBSOCKET_ENABLED=true`, `PAPER_WATCHLIST`, REAL 시세 자격정보를 요구합니다. 이때 한국·미국 시세 모두 REAL WebSocket에서 읽고 세션 분봉 저장이 활성화돼야 합니다. Spring이 별도로 확인하는 최신 REST 시세를 위해 `KIS_ENVIRONMENT=real`과 `KIS_MARKET_DATA_ENABLED=true`도 필요합니다. 실제 Spring 신호 POST는 `REAL_SIGNAL_SUBMISSION_ENABLED`와 live 주문·계좌읽기·보호청산·주문대조 게이트가 모두 열려 있을 때만 생성됩니다. 기본값은 false입니다. 전송 결과가 불명확하거나 대기 큐가 넘치면 현재 처리 중인 신호를 제외한 미전송 대기 후보를 버리고 해당 프로세스에서 신규 REAL 신호 전달을 중지합니다. 불명확한 결과는 멱등키를 운영 상태에 남기며 Spring 주문 의도와 증권사 체결 대조 전까지 수동 확인이 필요합니다. UNKNOWN 결과가 남은 상태에서 프로세스를 재시작할 때도 주문 의도를 먼저 대조해야 합니다.
- REAL 대기열 포화가 현재 Spring 요청 처리 중 발생해도 응답 수신 후 `BLOCKED_QUEUE_FULL` 잠금 상태가 유지됩니다. 완료된 in-flight 요청은 최근 신호 상태에 따로 표시되며 대기 중 후보는 재개하지 않습니다.
- Paper 자동 운영 스위치는 `PAPER_AUTOTRADING_ENABLED=false`가 기본값입니다. 이를 켤 때는 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`도 설정해야 하며, 현재 거래일 분봉 저장·복원에 실패하면 monitor 시작을 차단합니다. 이 기록이 없으면 재시작 후 ORB 기준 구간을 잘못 계산할 수 있습니다. 달력·실시간 피드·모델 승인·브로커 상태와 리스크 게이트 검증 전에는 켜지 마세요. Live 모드에서는 Python paper 자동화도 강제로 비활성화됩니다.
- PAPER 자동 주문 생성·체결 기록 결과가 UNKNOWN이면 실행 중인 monitor는 멱등키를 상태와 대시보드에 남기고 신규 진입을 중지합니다. 보호청산 점검은 계속됩니다. Spring에서 해당 키의 주문 상태를 확인하고 원인을 해소한 뒤 local stack을 재시작해야 신규 PAPER 진입 잠금이 초기화됩니다.
- 신규 진입에는 시세가 최신이어야 하고 신호 봉은 기본 120초 이내여야 합니다. WebSocket 재연결이나 작업 대기 때문에 오래된 봉이 늦게 도착하면 신규 진입을 차단합니다. 한도는 `PAPER_MAX_SIGNAL_BAR_AGE_SECONDS`로 설정합니다.
- 저장소에는 `.env_local`을 커밋하지 않습니다. `.env.example`에는 실제 값을 입력하지 않습니다.
- 모델 승인·활성화·롤백에는 `.env_local`의 `MODEL_GOVERNANCE_TOKEN`에 32자 이상의 무작위 값을 설정하고 요청의 `X-Model-Governance-Token` 헤더로 전달해야 합니다. PowerShell에서는 `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`로 64자리 값을 생성할 수 있습니다. 값이 비어 있거나 32자보다 짧으면 변경 API는 잠긴 상태입니다.
- 모의 계좌 연결 테스트는 의도적으로 네트워크를 호출하는 별도 작업입니다: `.\scripts\test-paper-kis.ps1`. 이 스크립트는 paper 자격증명과 전체 및 PAPER 계좌 읽기 스위치를 확인한 뒤 Spring 애플리케이션 컨텍스트와 PostgreSQL 호출 제한기를 사용해 기본적으로 국내 잔고 GET만 실행합니다. 해외 계좌 조회는 현재 운영 범위에서 차단됩니다. 테스트 전 모의/실전 주문, 실전 자동매매, 실전 주문 대조가 모두 잠겼는지 확인합니다. DB 연결이나 슬롯 예약에 실패하면 KIS 요청도 차단합니다.
- Python KIS 읽기 전용 스모크는 `.\scripts\test-paper-kis-python.ps1`로 실행합니다. PAPER 키·시장 시세 스위치·전체/PAPER 계좌 읽기 게이트를 검사한 뒤 기본적으로 국내 REST 시세 및 계좌 GET 두 건만 요청하며, 응답에 키·시크릿·계좌번호가 없는지도 확인합니다. 해외 REST 조회는 현재 운영 범위에서 차단됩니다. 주문·자동매매 잠금이 꺼져 있으면 요청하지 않습니다.
- 실계좌 잔고의 읽기 전용 확인은 별도 opt-in 스크립트로 구현돼 있습니다. 현재 범위에서 실행할 경우 국내 잔고 GET만 사용하며 미국 REAL 계좌 조회는 끕니다. 이 검사는 주문을 전송하지 않고 `.env_local`이나 기본 잠금을 바꾸지 않습니다.

### PAPER WebSocket 연결 사전점검

다음 명령은 PAPER WebSocket 승인 키와 국내 종목 구독을 확인합니다. `.env_local`에 `KIS_WEBSOCKET_ENABLED=true`를 설정해야 하며, `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, PAPER/REAL 주문 잠금, PAPER 자동매매 잠금 및 PostgreSQL 공유 호출 제한을 확인합니다. probe 프로세스는 실행 전에 자신의 프로세스 환경에서 모든 `KIS_REAL_*`, `REAL_SIGNAL_*`, `REAL_PROTECTIVE_*` 값과 자격정보를 제거합니다. 따라서 별도의 읽기 전용 REAL 시세 설정을 가진 `.env_local`을 수정하지 않습니다. 국내 장중 tick 확인은 아래처럼 `--wait-for-tick-seconds`를 지정합니다. 이 PAPER probe는 국내 구독만 지원합니다. 미국 시세 스트림은 PAPER 환경이 아닌 별도의 읽기 전용 REAL 시세 경로를 사용합니다.

```powershell
.\services\market-ai\.venv\Scripts\python.exe .\scripts\probe-kis-paper-websocket.py --market KR --symbol 005930 --wait-for-tick-seconds 120
```

`acknowledged_no_tick`은 KIS 구독 승인만 확인된 상태이고 장중 시세 수신을 뜻하지 않습니다. tick 대기를 지정한 실행은 실제 tick이 오지 않으면 실패 코드로 끝납니다. 출력은 상태와 안전 응답코드만 포함하며 승인 키와 원시 시세는 출력하거나 저장하지 않습니다. 구독 해제 요청을 보낸 뒤 연결을 닫고 주문 API는 호출하지 않습니다.

### P3 종목 후보 발굴

현재 후보 순위 조회는 국내 정규 연속매매 중 `GET /api/v1/market/candidates/KR?minimum_turnover=1`만 대상으로 합니다. 미국 후보 API 구현은 유지하지만 호출·연결 검증은 현재 범위에 없으므로 US 경로를 실행하지 않습니다. 국내 후보마다 현재가·거래량·거래대금·호가 스프레드를 확인하고, 호가가 없거나 매수·매도 가격이 교차하면 필터 통과로 표시하지 않습니다. 예시의 1은 호출 형식만 보여 주는 값이며 권장 투자 기준이 아닙니다. `minimum_turnover`는 KRW 단위로 지정합니다.

국내 후보 API에서 `freshness_verified`와 `executable`은 항상 `false`입니다. 거래대금·스프레드는 1차 필터 정보이며, 종목 구독·AI 신호·주문을 생성하지 않습니다. KRX 종가 단일가 구간과 거래소 휴장 중에는 조회를 거부합니다. 미국 후보 구현에 필요한 REAL 시세 설정은 현재 활성화하지 않습니다.

실행 중인 PAPER 시세 감시기에 국내 필터 통과 종목을 추가하는 구현 경로는 `POST /api/v1/market/candidates/KR/subscribe?minimum_turnover=1000000000`입니다. 현재 설정과 사용자 범위를 고려해 별도 잠금 `PAPER_CANDIDATE_SUBSCRIPTIONS_ENABLED`를 켜지 않았으며, 미국 구독 경로는 실행하지 않습니다. 모니터와 국내 WebSocket이 연결된 연속매매 시간에만 동작하며 PAPER 모드와 실전 거래 비활성도 다시 검사합니다. 등록 요청은 KIS 승인이나 신호·주문 가능성을 보증하지 않습니다. 감시기 추가는 실시간 시세 취득만 수행하고 기본 잠금은 `false`입니다.

후보 자동 갱신 코드는 두 시장을 지원하지만 현재 자동 구독·갱신은 국내만 개발·검증 대상으로 남겨 두고 실제 스위치는 끕니다. 미국 feed나 `PAPER_CANDIDATE_US_EXCHANGE`를 활성화하지 않습니다. `PAPER_CANDIDATE_LIMIT`은 1~20이며 기본 10입니다. 앱이 국내 장 시작 뒤 켜졌거나 장 전에 feed가 연결되지 않으면 해당 세션은 건너뜁니다. PAPER 자동매매가 켜져 있거나 PAPER 모드·실거래 잠금 조건이 어긋나면 갱신하지 않습니다. 순위는 감시 종목 선택에만 쓰며, 장전 스프레드가 없더라도 양수 가격·거래량과 거래대금 기준으로 구독할 수 있습니다. 실제 PAPER 매수 후보 생성에는 별도의 장중 신선 시세·스프레드·전략·승인 모델·위험 검사가 계속 적용됩니다.

국내 후보 목록 교체 구현은 `POST /api/v1/market/candidates/KR/refresh-subscriptions?minimum_turnover=1000000000`입니다. 미국 경로는 현재 실행하지 않습니다. 교체 전 Spring PAPER 운영 요약의 보유 종목·미체결 주문을 확인합니다. 보유 종목 구독은 유지하고, 미체결 주문이 하나라도 있으면 이전 후보 구독 전체를 보존합니다. Spring 응답 누락·오류·형식 불일치는 교체 작업을 중단합니다. 제거 대상은 이 API로 추가된 후보 구독으로 한정하며 `.env_local`의 수동 `PAPER_WATCHLIST` 종목은 유지합니다. 실시간 해제 요청의 KIS 승인 응답은 아직 검증되지 않았습니다.

### 국내 PAPER 취소 사전조회 점검

KIS가 PAPER 계좌에 취소가능수량 사전조회 서비스를 제공하는지 확인할 때만 아래 읽기 전용 명령을 사용합니다. 입력으로는 현재 공식 샘플의 `TTTC0084R`과 공식 레거시 샘플의 `TTTC8036R`만 허용합니다. KIS 공식 `kis_auth.py`는 PAPER에서 `T`로 시작하는 TR ID를 `V`로 바꾸므로 이 probe는 실제 요청에 `VTTC0084R` 또는 `VTTC8036R`을 사용합니다. 현재 공식 샘플은 조회구분 `0`(주문순서)과 `1`(종목순서)을 지원하며 기본값은 종목순서입니다. 레거시 샘플은 `psbl_qty`를 문서화하지 않으므로 조회 성공만으로 취소를 활성화할 수 없습니다. 모의 API가 `VTTC0084R`를 지원하는지는 별도로 확인해야 합니다. `KIS_ACCOUNT_READ_ENABLED=true`, `KIS_PAPER_ACCOUNT_READ_ENABLED=true`가 필요하며 PAPER/REAL 주문, REAL 계좌조회, REAL 주문대조 스위치는 `false`여야 합니다. 이 도구는 PostgreSQL 공유 호출 제한을 지키며 GET만 보냅니다. [KIS 공식 현재 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py), [KIS 공식 PAPER TR 변환](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/kis_auth.py#L2487-L2508)

```powershell
.\services\market-ai\.venv\Scripts\python.exe .\scripts\probe-kis-paper-cancel-preflight.py --transaction-id TTTC0084R
```

현재 샘플에서 주문순서 조회를 별도로 확인하려면 `--query-kind order_sequence`를 지정합니다. 종목순서는 `--query-kind item_sequence`이며, 기본 호출과 같습니다.

공식 레거시 샘플의 PAPER 변환 경로를 점검하려면 `--transaction-id TTTC8036R`을 지정합니다. 실제 요청에는 `VTTC8036R`이 사용됩니다.

`valid_preflight_response`는 응답 행에 양수 `psbl_qty`가 있다는 뜻일 뿐, 특정 주문 취소를 허용하거나 주문번호와 일치한다는 뜻이 아닙니다. `service_unavailable`(KIS `OPSQ0002`), `no_cancelable_quantity`, `response_incomplete`, `broker_error_unclassified`, `temporary_server_error`는 취소 사전조건을 확인하지 못한 상태입니다. 실제 취소 전에는 해당 주문번호·종목·계좌와 일치하는 수량을 확인하고 로컬 취소 제한 검증을 통과해야 합니다. 이 점검 명령은 주문·취소를 보내지 않습니다.

### P5 거래 세션 시세 감시

현재 사용 중인 국내 KRX 세션 감시 작업은 `WakeToRun` 및 `StartWhenAvailable`을 설정합니다. Windows 절전 해제를 허용하고 지정 시각을 놓치면 이후 실행을 시도하지만, 현재 사용자가 로그인되어 있어야 하고 배터리 시작·중지 제한과 Windows 전원 정책을 따릅니다. 감시는 지연 실행 때도 국내 장중 여부와 저장된 세션 품질을 확인하며 주문을 보내지 않습니다. 미국 세션·PAPER lifecycle·해외 P4 예약은 실행하거나 등록하지 않습니다.

`AIStockTrader-LocalStack` supervisor는 15초마다 Python `18000`·Spring `18080` 리스너가 각각 하나씩 `127.0.0.1`에만 열려 있는지 확인하고, 30초마다 두 API의 health/operations 응답과 거래 잠금 상태를 점검합니다. 리스너 누락·외부 바인딩은 즉시 두 서비스를 다시 시작하며, API 준비 점검이 두 번 연속 실패해도 재시작합니다. 감시기는 프로세스 생존만으로 서비스를 정상 판정하지 않습니다. 국내 KRX 폐장 확인 후 적용한 이 변경은 로컬 집중검사 4건과 GitHub Actions의 KRX 안전성·Windows PAPER 점검을 통과했고, 재기동 뒤 두 루프백 API와 모든 PAPER/REAL 주문 잠금, PAPER 자동매매 비활성을 확인했습니다. 실제 장애를 고의로 발생시켜 복구를 시험한 것은 아닙니다.

세션 성공 판정은 tick·봉 카운터만으로 하지 않습니다. 장전 마지막 카운트를 기준으로 개장 첫 봉을 포함하고, 세션 종료 후 국내 감시 종목의 저장 자료에서 정확한 정규장 분봉 수, 누락·중복·격자 이탈 0건, 모든 분봉의 스프레드, `KIS_PAPER_WEBSOCKET` 출처를 다시 검사합니다. 국내 종목은 공식 체결 `H0STCNT0`과 실시간 호가 `H0STASP0`를 함께 구독하며 두 응답이 모두 확인되기 전에는 feed 상태를 `RUNNING`으로 보고하지 않습니다. 분봉에는 해당 분에 수신한 호가만 적용하고 이전 분의 호가는 재사용하지 않습니다. REST 분봉과 혼합되거나 출처 정보가 없으면 실패 처리합니다. 저장 API 오류나 종목 결과 누락도 실패로 처리합니다. 해외 feed 구현 설명은 향후 개발 참고용이며 현재 운영 검증 대상이 아닙니다.

현재 실행하는 감시는 국내 `-Feed paper`만 사용합니다. 해외 `-Feed real` 또는 양쪽 feed를 함께 확인하는 검증은 수행하지 않습니다.

`.\scripts\test-paper-websocket-monitor.ps1`은 기본 60초, 최대 10시간 동안 국내 로컬 PAPER feed를 읽기 전용으로 점검합니다. 전체 KRX 세션 확인은 개장 전 준비를 포함해 08:45 KST에 시작하고 15:30 KST 장 종료 후 품질 확인 시간을 포함해 25,200초 실행하며, 개장 전 08:45~09:00과 종가 동시호가 15:20~15:30의 무수신은 장애 한도에서 제외합니다. 국내 전체 세션은 `-DurationSeconds 25200 -PollIntervalSeconds 15 -MaxNoTickSeconds 300 -Feed paper -MinimumBarsDelta 380 -RequireSessionClose` 기준으로 확인합니다. 미국 감시 스크립트와 세션 등록 코드는 남아 있지만 현재 실행하지 않습니다. 모든 미국 세션 감시 예약은 `Disabled` 상태로 유지하며, 미국 feed·작업을 등록하는 명령은 사용하지 마세요. 국내 일회성 일정은 `register-market-data-session-monitor.ps1 -Feed paper -SessionDate YYYY-MM-DD`로 등록할 수 있고, 등록기는 거래소 캘린더를 확인하되 KIS 계좌·시세 API를 호출하거나 주문을 보내지 않습니다. 감시 실행에는 Windows 사용자가 로그인해 있고 로컬 API가 실행 중이어야 합니다. monitor는 종료 시각·틱 신선도·전체 국내 세션 380분봉 및 주문/자동매매 잠금을 확인하며 주문을 전송하지 않습니다. 틱 수신이 5분 넘게 끊기거나 완료 분봉 수가 최소치에 못 미치면 실패합니다. 결과는 민감정보 없이 `.local/validation/paper-websocket-samples.jsonl`에 저장되며 `.local/`은 Git에서 제외됩니다.

감시기는 feed별 연결과 틱 상태를 분리해 기록합니다. 현재 허용된 운영 표본은 국내 `paper` feed뿐이며, `real` 또는 양쪽 feed를 지정한 해외 검증은 실행하지 않습니다. PAPER 주문 잠금과 자동매매 비활성 조건은 계속 유지합니다. `.progress.jsonl`에는 마지막 WebSocket 실패의 허용 구간·오류 분류 코드와 UTC 시각을 기록하며, 원시 예외·응답 내용은 기록하지 않습니다. 복구된 직후에도 해당 진단을 다음 실패 또는 서비스 재시작까지 보존해 감시 로그에서 원인을 확인할 수 있습니다. feed 진단에는 수신 체결·호가 프레임 수와 수집기에 실제 반영된 호가 업데이트 수 및 표본 구간 증가량을 별도 기록해, 호가 프레임 수신과 분봉 스프레드 입력 여부를 구분합니다.

한국거래소 정규장 연속매매는 09:00~15:20이고 15:20~15:30은 종가 동시호가입니다([KRX 매매거래시간](https://global.krx.co.kr/contents/GLB/06/0602/0602010204/GLB0602020204T1.jsp)). `-Feed paper` 감시는 이 동시호가 동안 무수신을 별도 누적하고 5분 장애 기준에서는 제외하며, 15:30에 표본을 마칩니다. 마지막 연속매매 틱이 동시호가 직전에 온 경우는 feed 장애로 판정하지 않습니다. 이 구간의 실제 종가 체결 틱 수신은 별도 검증 범위입니다. 2026-09-14 이후 KIS `MARKET_CLS_CODE`가 체결·호가 프레임에 제공되므로, 파서는 정규장 코드 `2`만 전략 분봉과 호가에 사용하고 프리장(`1`), 애프터마켓(`3`), 종가(`5`) 및 비어 있거나 알 수 없는 값은 제외합니다. KRX 애프터마켓(16:00~20:00), NXT 프리마켓/정규장 및 통합시세는 현재 구독·주문 전략 범위가 아닙니다.

### KIS PAPER 주문 수명주기 통합 확인

`KIS_PAPER_RECONCILIATION_ENABLED=true`는 PAPER 계좌 읽기가 허용된 경우에만 동작하는 읽기 전용 주문 상태 poller입니다. `ACCEPTED`, `PARTIALLY_FILLED`, `CANCEL_PENDING`, `UNKNOWN` intent를 국내 운영 범위에서 페이지별 최대 50건씩 순환 조회합니다. 페이지는 `updatedAt`, `id` 순으로 정렬되어 첫 50건 이후의 주문도 다음 poll에서 차례로 확인합니다. 알려진 브로커 주문번호가 있는 주문만 KIS 일별 체결조회로 다시 확인합니다. 이 작업은 `KIS_PAPER_ORDERS_ENABLED`가 꺼져 있어도 조회 스위치 두 개, PAPER 자격정보, PAPER 실행 모드가 맞으면 실행할 수 있지만 주문·취소 POST는 하지 않습니다. 주문번호가 없는 UNKNOWN 주문은 증권사 기록과의 유일한 연결 키가 없으므로 자동 조회·재전송하지 않고 수동 확인 대상으로 남깁니다. 기본값은 `false`이며 poll 간격 기본값은 30초입니다.

KIS PAPER 주문·취소 lifecycle 스크립트는 구현되어 있지만, 현재 허용된 브로커 검증은 국내 읽기 전용 GET뿐입니다. KIS 공식 저장소의 모의투자 API 지원표는 국내 `주식정정취소가능주문조회`를 모의투자 미지원으로 표시합니다. 같은 공식 API 예제는 정정·취소 POST 전에 이 조회를 호출해 `psbl_qty`를 확인하라고 요구하므로, 취소 가능수량을 다른 API 값으로 추정하거나 우회하지 않습니다. 따라서 `KR -ConfirmPaperOrderAndCancel`은 네트워크 요청 전에 거부되며, KIS가 PAPER 지원을 공식적으로 추가하기 전까지 이 lifecycle 검증은 막힌 상태입니다. [KIS 공식 모의투자 API 지원표](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/README.md#3-api-%EC%A0%9C%EA%B3%B5-%EB%AA%A9%EB%A1%9D) · [KIS 공식 취소가능수량 조회 샘플](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py). 미국 주문·취소 검증은 현재 수행하지 않습니다. `-PreflightOnly`도 사용자가 별도 국내 확인을 요청한 경우에만 읽기 전용으로 실행합니다.

주문 제출 모드는 Python 거래소 캘린더를 확인하지만, 현재 KIS PAPER lifecycle 검증 범위에서는 주문 제출을 실행하지 않습니다. 읽기 전용 국내 `-PreflightOnly`는 장외에서도 실행할 수 있습니다.

```powershell
.\scripts\test-kis-paper-order-lifecycle-isolated.ps1 -Market KR -Symbol 069500 -LimitPrice 75000 -PreflightOnly
```

미국 PAPER lifecycle 예약 작업 `AIStockTrader-US-Paper-Lifecycle-Smoke-20261109`은 2026-11-09에 `Disabled` 상태로 확인했으며 실행된 적이 없습니다. 미국 세션 감시와 P4 작업도 모두 `Disabled`입니다. 이 작업을 다시 등록하거나 실행하지 마세요. 이후 단계에서 해외 운영 검증 범위가 바뀌면 별도 계획과 승인에 따라 재검토합니다.

PAPER lifecycle 스크립트의 1주 제한·가격 신선도·주문가능액 검사는 구현상 안전장치일 뿐 현재 테스트 허용을 뜻하지 않습니다. 실제 KIS PAPER 주문은 현 단계에서 전송하지 않습니다.

해당 lifecycle 구현은 1주 수량·가격 신선도·주문가능액·멱등 대조·취소를 검사하지만, 이 장벽을 우회해 주문 테스트를 실행하지 않습니다. 실제 KIS PAPER 주문은 현 단계에서 전송하지 않습니다.

주문을 보내지 않고 현재 PAPER 시세와 종목별 주문가능금액만 읽기 전용으로 확인하려면 `.env_local`의 주문 잠금을 그대로 둔 채 `scripts/test-kis-paper-order-lifecycle.ps1`에 `-PreflightOnly`를 전달합니다. 예: `-Market KR -Symbol 069500 -LimitPrice 75000 -PreflightOnly`. 이 모드는 주문 잠금이 꺼져 있는 Spring 설정만 허용하고, 시세와 계좌 확인이 모두 최신·충분할 때만 통과합니다. 마지막 실행 결과는 Git 무시 경로 `.local/validation/paper-preflight-latest.json`에 원자적으로 기록하며 결과·실패 단계·시세 freshness 및 주문가능금액 검사 통과 여부, 주문·취소 미전송 여부만 포함합니다. 실제 가격, 주문가능 잔액, 계좌번호, API 키·시크릿, 오류 원문은 저장하지 않습니다. 실패한 사전점검도 같은 파일을 최신 실패 결과로 교체합니다.

현재 국내 세션 검증에 사용하는 집중 테스트는 `Invoke-Pester -Path scripts/tests/krx-session-monitor-domestic.Tests.ps1`입니다. 해외 fixture를 포함한 공용 주문 preflight·세션 테스트 묶음은 현재 실행하지 않습니다.

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

데이터 권한을 확인한 국내 1분봉 CSV만 현재 백테스트·학습 검증에 사용합니다. CSV 열은 `timestamp,open,high,low,close,volume,spread_bps,symbol`이며 `timestamp`는 `2026-11-05T09:00:00+09:00`처럼 시간대 오프셋을 포함합니다. 해외 날짜 분할 코드는 유지하지만 현재 해외 CSV를 처리하지 않습니다. 한 CSV에는 한 종목만 넣고 세션 안에서 시간순으로 정렬합니다. `spread_bps`는 bid-ask 전체 스프레드 기준입니다. 백테스트는 다음 봉의 스프레드 절반을 매수 체결가에, 청산 봉의 스프레드 절반을 매도 체결가에 반영합니다. 다음 봉 스프레드가 없거나 상한보다 크면 진입 체결을 만들지 않고, 청산 봉 스프레드가 없으면 설정된 스프레드 상한을 비용으로 사용합니다. 학습 레이블은 현재·미래 봉의 관측 스프레드 비용도 차감하며, 미래 스프레드가 없으면 해당 표본을 제외합니다. `symbol` 열을 넣으면 요청한 값과 일치하는지도 확인합니다. `scripts/export-market-data.py --dry-run`과 생성되는 권리 sidecar의 세션별 `source_counts`는 출처별 분봉 수를 기록하며, 전체 봉 수만으로 승인된 WebSocket 세션으로 판정하지 않습니다.

스프레드 체결 모델은 백테스트 버전 2입니다. 버전 1 기준으로 승인된 구형 모델은 새 비용 가정에서 성과를 재검증하기 전까지 승인·활성 모델로 선택되지 않습니다. 왕복 비용 설정은 여전히 사용자의 실제 계좌 수수료·세금·거래소 비용 확인을 대신하지 않습니다.

백테스트 `gross_pnl`은 기준 시세 가격 차이이고, `costs`에는 추정 슬리피지·관측 또는 대체 스프레드·매수/매도 수수료를 합산합니다. `net_pnl`은 `gross_pnl - costs`입니다.

KIS에서 국내 정규 연속매매 분봉을 읽기 전용으로 확인할 때는 `GET /api/v1/market/sessions/KR/{symbol}/bars?session_date=YYYY-MM-DD`를 사용합니다. 단일 120개 페이지를 최대 6회까지 거슬러 조회하며, 한국거래소 연속매매 구간(09:00~15:20)만 반환합니다. 종가 단일가 시각(15:20~15:30)에 해당하는 체결봉은 전략 데이터에서 제외하고, 장중 거래정지로 인한 공백은 보정하지 않고 백테스트·학습에서 거부합니다. API 분봉에는 과거 호가 스프레드가 없어 해당 값은 비어 있으며, 전략은 스프레드 필터가 있는 진입을 생성하지 않습니다. 로컬 연동 확인은 저장·주문 없이 `python scripts/test-paper-kis-session.py --session-date YYYY-MM-DD`로 실행할 수 있습니다. [KIS Developers API 서비스 소개](https://apiportal.koreainvestment.com/about-open-api)는 개인 고객의 시세 정보 이용을 자기 자산 투자 목적으로 한정하고 제3자 제공을 금지하므로, 데이터와 파생 파일은 로컬 개인 용도에 한정합니다.

PostgreSQL 저장 분봉의 현재 감사·백테스트·학습 검증은 국내 종목만 대상으로 합니다. 미국 AAPL 2026-11-06 예시는 과거 기록이며 재실행하지 않습니다. 국내 삼성전자 감사 명령은 `.\services\market-ai\.venv\Scripts\python.exe .\scripts\audit-market-session.py --market KR --symbol 005930 --session-date YYYY-MM-DD`입니다. `PRE_OPEN`/`COLLECTING`은 아직 정규장이 끝나지 않은 상태이며, 폐장 후 `COMPLETE`는 예상 국내 연속매매 380분이 중복·누락·시간 격자 이탈 없이 저장됐다는 뜻입니다. `source_counts`는 분봉 출처별 개수를 보여주므로 `full_session_complete=true`만으로 PAPER WebSocket 전체 세션이라고 판단하지 않습니다. PAPER WebSocket 세션은 `KIS_PAPER_WEBSOCKET` 출처가 380개인지 별도로 확인해야 합니다. `--require-complete`를 붙이면 완전한 전체 세션이 아닐 때 종료 코드 2를 반환합니다. 스프레드 기준 분석은 `--minimum-spread-coverage-percent 95`처럼 최소 포함률을 지정할 수 있으며 기준 미달이면 닫힙니다. 이 도구는 조회만 하며 누락 봉을 보정하거나 DB를 수정하지 않습니다.

운영 대시보드의 P3 분봉 품질 정상 판정도 완전한 세션과 100% 스프레드뿐 아니라 전 세션 봉이 승인된 WebSocket 출처에서 왔는지 확인합니다. 출처별 개수는 세션 상세에 표시하고 REST 보완 봉이 있으면 정상으로 처리하지 않습니다. 이는 데이터 품질 상태만 표시하며 매매 신호나 주문을 만들지 않습니다.

같은 감사 결과는 Python API `GET /api/v1/market/session-quality/{market}/{symbol}?session_date=YYYY-MM-DD`에서도 조회할 수 있습니다. 이 경로는 PostgreSQL의 읽기 전용 분봉만 확인하며, 저장 기능이 꺼져 있으면 `503`으로 닫힙니다. 거래소 비영업일은 DB를 조회하지 않고 `NON_SESSION`으로 반환합니다.

감시 중인 시장의 여러 종목을 한 번에 감사하려면 `GET /api/v1/market/session-quality/{market}?symbols=005930&symbols=069500&session_date=YYYY-MM-DD`를 사용합니다. 세션 날짜를 직접 지정하지 않고 `latest_closed=true`를 주면 거래소 현지 날짜 기준으로 아직 폐장하지 않은 세션을 건너뛰고 최근 마감 세션을 선택합니다. 최근 30일 안에서 거래일을 확인할 수 없으면 `503`으로 닫힙니다. 이 API는 종목 수를 최대 50개로 제한하고 단일 읽기 전용 DB 쿼리로 결과를 묶습니다. 각 결과에는 누락된 예상 분봉 시각과 저장된 분봉 중 스프레드가 없는 시각이 포함되며, 운영 대시보드는 처음 세 시각까지 각 거래소 현지 시간으로 표시합니다. 이 진단은 저장 자료를 수정하거나 누락 데이터를 만들어 내지 않습니다. 대시보드는 현재 구독 종목의 분봉 완전성·누락 수·스프레드 포함률과 선택된 거래일도 보여 주며, API나 저장소가 응답하지 않으면 품질을 확인 불가로 표시합니다.

```powershell
$buyCommissionBps = [decimal](Read-Host 'Verified buy commission in basis points')
$sellCommissionBps = [decimal](Read-Host 'Verified sell commission in basis points')
$sellTaxBps = [decimal](Read-Host 'Verified sell tax/levy in basis points; enter zero only if verified')
$slippageBps = [decimal](Read-Host 'Reviewed per-fill slippage in basis points')
$roundTripCostBps = [decimal](Read-Host 'Reviewed total round-trip label cost in basis points')
.\services\market-ai\.venv\Scripts\python.exe .\scripts\backtest-quant.py .\data\licensed-bars.csv --market KR --symbol 005930 --buy-commission-bps $buyCommissionBps --sell-commission-bps $sellCommissionBps --sell-tax-bps $sellTaxBps --slippage-bps $slippageBps
.\services\market-ai\.venv\Scripts\python.exe .\scripts\train-ai-models.py .\data\licensed-bars.csv --market KR --symbol 005930 --buy-commission-bps $buyCommissionBps --sell-commission-bps $sellCommissionBps --sell-tax-bps $sellTaxBps --slippage-bps $slippageBps --round-trip-cost-bps $roundTripCostBps
```

Backtest and training commands require explicit cost assumptions; there are no command-line defaults for buy/sell commission, sell tax, slippage, or label round-trip cost. Use the KIS account's verified commission schedule and an explicitly reviewed conservative slippage estimate. The label round-trip cost must cover buy commission + sell commission + sell tax + two slippage fills; include applicable exchange fees and market impact in the reviewed assumptions. Do not treat an unverified example value as the account's actual rate. Model manifests record each supplied assumption. Approval still requires at least five distinct held-out sessions and five AI+quant fills, positive AI+quant net P&L, and performance above the quant baseline over the same period.

단일 종목의 위험기반 백테스트는 다음 선택 인수로 거래당 손절 위험 예산(최대 0.25%), 종목 배정(최대 10%), 주문 명목 한도 중 가장 낮은 수량을 계산할 수 있습니다. `--sizing-equity`는 읽기 전용 로컬 시나리오 자본이며 계좌 잔고를 조회하지 않습니다. 종목별 실행은 자본을 공유하지 않으므로 결합 포트폴리오의 세 종목 동시 한도·일손실 1% 차단을 검증하지 않습니다. 크기 계산은 ATR 손절폭과 추정 거래비용을 사용하며 실제 체결 손실 상한을 보장하지 않습니다.

```powershell
.\services\market-ai\.venv\Scripts\python.exe .\scripts\backtest-quant.py .\data\licensed-bars.csv --market KR --symbol 005930 --buy-commission-bps $buyCommissionBps --sell-commission-bps $sellCommissionBps --sell-tax-bps $sellTaxBps --slippage-bps $slippageBps --sizing-equity 10000000 --max-position-notional 1000000
```

KIS's [published online fee page](https://securities.koreainvestment.com/main/customer/guide/_static/TF04ae010000.jsp) separates branch-managed accounts from BanKIS and distinguishes markets and order routes; it also allows negotiated rates. Confirm this account's current domestic and US buy/sell rates in the KIS app under `고객지원 > 고객센터 > 수수료 안내 > 온라인매매 수수료` ([KIS help page](https://securities.koreainvestment.com/main/customer/support/_static/TF04fg000000.jsp?mctgr=03)) or directly with KIS. Do not copy the public standard table or an old promotion into `.env_local` as the account's rate. Convert a verified percentage rate to basis points with `bps = percent × 100`; enter sell taxes/levies and reviewed slippage separately, and avoid counting an item twice if KIS's displayed commission already includes it. Keep the model and PAPER cost gates closed until the account-specific rates and assumptions are reviewed.

로컬 PostgreSQL의 새 데이터를 안전하게 다시 학습 후보로 만들려면 `.env_local`의 해당 시장 `BACKTEST_*` 매수수수료·매도수수료·매도세금·슬리피지·왕복비용 설정을 먼저 채운 뒤 아래 실행기를 사용합니다. 예시 범위는 현재 예약된 12월 2일 학습과 맞춘 것으로, 12월 1일 장 마감 뒤 실행합니다. 실행 시점에 13개 적격 세션을 확인하지 못하면 진행하지 마세요. 비용 설정이 없거나 label 왕복 비용이 매수수수료+매도수수료+매도세금+양쪽 슬리피지 합보다 낮으면 DB 내보내기 전에 중단합니다. 날짜는 시장 현지 날짜이며, CSV와 보고서는 고유한 실행 ID로 로컬 전용 `data/model-candidates/`와 `.local/models/retraining/`에 생성됩니다. 학습은 세션 완전성과 권리 manifest를 다시 확인하고, 중복 실행이 기존 CSV를 덮어쓰지 않습니다. 생성 후보는 항상 미승인 상태이며, 별도 held-out 성능 검토와 모델 거버넌스 승인이 없으면 운영에 선택되지 않습니다.

```powershell
.\scripts\run-model-retraining.ps1 -Market KR -Symbol 005930 -FromDate 2026-11-09 -ToDate 2026-12-01
```

실행 보고서에서 `candidate_created_review_required`가 아니면 후보가 만들어졌다고 간주하지 마세요. 비용 설정 누락·오류는 `configuration_failed`로 기록하고 DB 내보내기 전에 멈춥니다. 이후 실패 보고서는 실행 전후의 모델 manifest와 artifact 파일을 비교해 이번 실행에서 새로 생긴 후보와 고아 파일을 표시합니다. 실패 뒤 새 artifact가 남으면 그 파일을 검토·정리하기 전까지 승인이나 활성화를 진행하지 마세요.

자동 후보 학습에는 종목별 최소 13개 적격 세션이 필요합니다. `register-model-candidate-retraining-task.ps1`는 지정 날짜 범위에 거래소 캘린더상 13개 이상 거래일이 있는지, `.env_local`의 PAPER 모드·주문 잠금 및 시장별 비용 설정이 유효한지 검사합니다. 이 캘린더 검사는 실제 데이터가 준비됐다는 뜻이 아닙니다. 실행 시 내보내기 단계가 PostgreSQL의 1분봉 완전성, 스프레드 관측 시각, 승인된 WebSocket 출처와 데이터 권리를 다시 확인하며, 기준 미달이면 학습 전에 실패합니다. 예약은 이 검증이 끝난 거래일 이후로 잡아야 합니다. 독립 홀드아웃 예약 시 `-TrainingToDate`에 학습 데이터 cutoff를 전달해야 하며, 등록기는 홀드아웃 시작 날짜가 cutoff 다음 날 이후인지 검사합니다. 홀드아웃 평가는 학습 이후 서로 다른 적격 세션이 최소 5개 필요합니다. 실제 데이터 결손에 대비해 예약 날짜 범위는 6개 이상 거래 세션을 포함하도록 여유를 두고, 실행 시 5개 미만이면 평가가 실패합니다. 이미 평가에 사용한 세션은 거부됩니다. 예약 작업은 실행 당일 `.env_local` 비용 가정을 읽으므로 이미 등록된 일정도 새 실행 코드와 호환됩니다. 등록·실행 전에 매수/매도 수수료, 매도세금, 보수적 슬리피지와 왕복 비용을 검토하세요. 재학습·홀드아웃 스크립트는 실행 전 `.env_local`에서 PAPER 모드, PAPER/REAL 주문 비활성, PAPER 자동매매 비활성을 확인하며, 하나라도 빠졌거나 켜져 있으면 데이터 내보내기 전에 종료합니다. 학습은 후보를 자동 승인하거나 활성화하지 않으며 주문 API를 호출하지 않습니다. Windows 사용자가 로그인하고 PostgreSQL이 실행 중이어야 합니다.

퀀트 결과는 세션별 JSON과 CSV SHA-256을 출력하며 주문 실행 권한이 없습니다. AI 학습도 입력 CSV SHA-256을 각 모델 manifest에 기록합니다. 각 백테스트 세션은 1주 수량으로 독립 계산하고, 포트폴리오 자본 복리·계좌별 세금·거래소 비용·시장충격은 별도 검증해야 합니다. 조정주가, 종목코드 변경, 분봉 누락, 데이터 이용·저장·재배포 권한은 CSV 제공자가 확인해야 합니다.

`test-postgres-outage-recovery.ps1`는 `.env_local`을 로드한 테스트 프로세스의 기존 환경 변수 전체를 보존하고, 새로 만든 변수까지 종료 시 제거합니다. 실행 실패 중에도 격리 PostgreSQL과 Spring 프로세스를 정리하고 환경을 복원합니다.

## 2026-11-07 KIS 계좌 읽기 연동 확인

PostgreSQL 16.4에 V18–V20 migration을 적용했습니다. KIS PAPER 국내·미국 잔고와 매수가능액 GET 4건, REAL 국내·미국 잔고 GET 2건이 통과했습니다. PAPER에서는 KRW 70,000 기준의 매수가능액이 충족됐고 USD 11 기준은 충족되지 않았습니다. 계좌 잔고 금액, 계좌번호, 키는 로그와 문서에 기록하지 않았습니다. 모든 검증에서 주문·취소는 호출하지 않았으며, 이는 조회 API 연결 증거로만 봅니다.
