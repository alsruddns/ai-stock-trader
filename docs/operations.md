# Windows 운영

## 로컬 실행

1. PostgreSQL 서비스가 `localhost:5432`에서 실행 중인지 확인합니다.
2. 저장소 루트의 Git 제외 `.env_local`에 DB 비밀번호와 필요한 KIS 환경별 설정을 입력합니다.
3. Spring Boot를 실행합니다: `.\scripts\start-local.ps1`
4. Python API는 `services/market-ai` 가상환경에서 실행합니다. API는 `127.0.0.1`에만 바인딩합니다.

Java 빌드는 Gradle이 아닌 프로젝트 포함 Maven Wrapper를 사용합니다: `.\backend\trading-api\mvnw.cmd test`.

paper ledger 비용 추정은 `PAPER_COST_MODEL_ENABLED=false`가 기본입니다. 실제 적용 요율을 확인한 뒤 KR/US 매수·매도 수수료율을 basis points로 설정하고 `PAPER_COST_MODEL_ENABLED=true`로 켭니다. 한국·미국 계좌별 우대 수수료, 거래소 및 규제 비용은 다를 수 있으므로 예제의 0 값은 실거래 요율을 의미하지 않습니다. 비활성 상태에서는 P&L을 총액으로만 해석합니다.

## KIS 자격증명

- 모의: `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NUMBER`
- 실전: `KIS_REAL_APP_KEY`, `KIS_REAL_APP_SECRET`, `KIS_REAL_ACCOUNT_NUMBER`
- 계좌번호는 KIS 계좌 앞 8자리와 상품 코드 뒤 2자리를 붙인 형식입니다.
- Spring과 Python KIS 계좌조회는 전체 스위치 `KIS_ACCOUNT_READ_ENABLED=true`와 대상별 스위치가 모두 필요합니다. PAPER는 `KIS_PAPER_ACCOUNT_READ_ENABLED=true`, REAL은 `KIS_REAL_ACCOUNT_READ_ENABLED=true`입니다. REAL 스위치는 기본 false입니다.
- Python 실시간 시세 릴레이는 `KIS_WEBSOCKET_ENABLED=true`를 요구합니다. 국내 paper feed는 PAPER 자격정보를 사용합니다. 미국 시세 구독을 PAPER ledger에 공급하려면 `KIS_REAL_WEBSOCKET_MARKET_DATA_ENABLED=true`와 REAL 시세 자격정보가 추가로 필요합니다. 이 별도 REAL 연결은 미국 시세만 읽으며 모든 주문은 로컬 PAPER ledger로만 갑니다. 계좌 조회 및 REAL 주문 스위치와 독립이며 기본값은 `false`입니다.
- Paper 자동 운영 스위치는 `PAPER_AUTOTRADING_ENABLED=false`가 기본값입니다. 이를 켤 때는 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`도 설정해야 하며, 현재 거래일 분봉 저장·복원에 실패하면 monitor 시작을 차단합니다. 이 기록이 없으면 재시작 후 ORB 기준 구간을 잘못 계산할 수 있습니다. 달력·실시간 피드·모델 승인·브로커 상태와 리스크 게이트 검증 전에는 켜지 마세요. Live 모드에서는 Python paper 자동화도 강제로 비활성화됩니다.
- 신규 진입에는 시세가 최신이어야 하고 신호 봉은 기본 120초 이내여야 합니다. WebSocket 재연결이나 작업 대기 때문에 오래된 봉이 늦게 도착하면 신규 진입을 차단합니다. 한도는 `PAPER_MAX_SIGNAL_BAR_AGE_SECONDS`로 설정합니다.
- 저장소에는 `.env_local`을 커밋하지 않습니다. `.env.example`에는 실제 값을 입력하지 않습니다.
- 모델 승인·활성화·롤백에는 `.env_local`의 `MODEL_GOVERNANCE_TOKEN`에 32자 이상의 무작위 값을 설정하고 요청의 `X-Model-Governance-Token` 헤더로 전달해야 합니다. PowerShell에서는 `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`로 64자리 값을 생성할 수 있습니다. 값이 비어 있거나 32자보다 짧으면 변경 API는 잠긴 상태입니다.
- 모의 계좌 연결 테스트는 의도적으로 네트워크를 호출하는 별도 작업입니다: `.\scripts\test-paper-kis.ps1`. 이 스크립트는 paper 자격증명과 전체 및 PAPER 계좌 읽기 스위치를 확인한 뒤 Spring 애플리케이션 컨텍스트와 PostgreSQL 호출 제한기를 사용해 국내·미국 잔고 GET만 실행합니다. 테스트 전 모의/실전 주문, 실전 자동매매, 실전 주문 대조가 모두 잠겼는지 확인합니다. DB 연결이나 슬롯 예약에 실패하면 KIS 요청도 차단합니다.
- Python KIS 읽기 전용 스모크는 `.\scripts\test-paper-kis-python.ps1`로 실행합니다. PAPER 키·시장 시세 스위치·전체/PAPER 계좌 읽기 게이트를 검사한 뒤 국내·미국 REST 시세 및 계좌 GET 네 건만 요청하며, 응답에 키·시크릿·계좌번호가 없는지도 확인합니다. 주문·자동매매 잠금이 꺼져 있으면 요청하지 않습니다.
- 실계좌 잔고의 읽기 전용 확인은 `.\scripts\test-real-kis-account-readonly.ps1`로 별도 실행합니다. 전체/REAL 계좌 읽기 허용과 PostgreSQL, 계좌번호 형식을 검사하며 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`, `KIS_REAL_RECONCILIATION_ENABLED=false` 상태에서 국내·미국 잔고 GET만 요청합니다. 자격정보 값은 출력하지 않습니다.

## 실전 주문 게이트

기본은 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`입니다. REAL 내부 오케스트레이터는 REAL 자격정보와 전체/REAL 계좌 읽기 스위치, 세 주문 잠금, 해당 주문과 3초 이내에 결합된 서버 내부 위험 승인이 있어야만 주문 요청을 보냅니다. 주문 의도를 먼저 DB에 기록하고, 부분체결 취소에는 잔량을 쓰며, 불명 결과는 자동 재전송하지 않습니다. 주기적 실계좌 체결 조회는 별도 잠금으로 기본 비활성입니다. UNKNOWN 중 주문번호가 없는 건은 자동 추측·재주문하지 않습니다. P6의 paper 세션·장애 검증 전까지 세 주문 잠금을 열지 않습니다.
REAL 주문 상태의 읽기 전용 주기 대조는 `KIS_REAL_RECONCILIATION_ENABLED=false`가 기본입니다. 활성화하려면 REAL 계좌 읽기 스위치와 자격정보도 필요합니다. 주문번호가 저장된 접수/부분체결/취소대기 주문만 조회하며, 주문번호 없는 UNKNOWN은 자동 추측·재주문하지 않습니다.

## 로컬 PostgreSQL 통합 테스트

저장소 루트에서 `.\scripts\test-local-postgres-integration.ps1`을 실행하면 `.env_local`의 DB 설정으로 PAPER/REAL 주문 의도 저장·재조회, 환경 격리, 멱등 키 복구 테스트를 선택 실행합니다. 테스트는 KIS API를 호출하지 않으며, 스크립트는 환경변수를 현재 프로세스에서만 적용하고 실행 후 복구합니다. 기본 `mvnw test`에서는 해당 DB 통합 테스트를 건너뜁니다.

별도 Java 프로세스 종료 후 복구까지 점검하려면 `.\scripts\test-paper-process-restart.ps1`을 실행합니다. 로컬 PostgreSQL에 고유 멱등 키의 PAPER 대기 주문을 만든 뒤 Spring JVM을 종료하고 새 JVM에서 동일 주문을 조회·재제출해 ID와 상태가 유지되는지 확인한 후 주문을 취소합니다. 이 스크립트는 KIS API를 호출하지 않습니다.

PostgreSQL 프로세스 장애를 격리된 임시 인스턴스로 점검하려면 `.\scripts\test-postgres-outage-recovery.ps1`을 실행합니다. 이 스크립트는 Windows PostgreSQL 16 바이너리를 사용해 임시 클러스터를 만들고, 해당 DB와만 연결하는 Spring API를 띄워 대기 PAPER 주문 저장, DB 중단 시 안전한 HTTP 503, DB 재시작 후 주문 복구를 확인합니다. 설치된 `localhost:5432` DB와 KIS API는 사용하지 않으며, 실전·모의 KIS 주문과 실전 계좌조회는 잠금이 확인된 경우에만 진행됩니다.

## 과거 데이터 백테스트와 학습

데이터 권한을 확인한 1분봉 CSV는 다음 열을 사용합니다: `timestamp,open,high,low,close,volume,spread_bps,symbol`. `timestamp`는 `2026-11-05T09:00:00+09:00`처럼 시간대 오프셋을 포함해야 하며, 한 CSV에는 한 종목만 넣고 세션 안에서 시간순으로 정렬합니다. `spread_bps`가 없으면 해당 봉은 전략의 스프레드 필터를 통과하지 않습니다. `symbol` 열을 넣으면 요청한 종목과 값이 일치하는지도 확인합니다.

```powershell
.\services\market-ai\.venv\Scripts\python.exe .\scripts\backtest-quant.py .\data\licensed-bars.csv --market KR --symbol 005930 --commission-bps 5 --slippage-bps 5
.\services\market-ai\.venv\Scripts\python.exe .\scripts\train-ai-models.py .\data\licensed-bars.csv --market KR
```

퀀트 결과는 세션별 JSON과 CSV SHA-256을 출력하며 주문 실행 권한이 없습니다. AI 학습도 입력 CSV SHA-256을 각 모델 manifest에 기록합니다. 각 백테스트 세션은 1주 수량으로 독립 계산하고, 포트폴리오 자본 복리·계좌별 세금·거래소 비용·시장충격은 별도 검증해야 합니다. 조정주가, 종목코드 변경, 분봉 누락, 데이터 이용·저장·재배포 권한은 CSV 제공자가 확인해야 합니다.
