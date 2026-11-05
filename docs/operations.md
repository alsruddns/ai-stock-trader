# Windows 운영

## 로컬 실행

1. PostgreSQL 서비스가 `localhost:5432`에서 실행 중인지 확인합니다.
2. 저장소 루트의 Git 제외 `.env`에 DB 비밀번호와 필요한 KIS 환경별 설정을 입력합니다.
3. Spring Boot를 실행합니다: `.\scripts\start-local.ps1`
4. Python API는 `services/market-ai` 가상환경에서 실행합니다. API는 `127.0.0.1`에만 바인딩합니다.

Java 빌드는 Gradle이 아닌 프로젝트 포함 Maven Wrapper를 사용합니다: `.\backend\trading-api\mvnw.cmd test`.

paper ledger 비용 추정은 `PAPER_COST_MODEL_ENABLED=false`가 기본입니다. 실제 적용 요율을 확인한 뒤 KR/US 매수·매도 수수료율을 basis points로 설정하고 `PAPER_COST_MODEL_ENABLED=true`로 켭니다. 한국·미국 계좌별 우대 수수료, 거래소 및 규제 비용은 다를 수 있으므로 예제의 0 값은 실거래 요율을 의미하지 않습니다. 비활성 상태에서는 P&L을 총액으로만 해석합니다.

## KIS 자격증명

- 모의: `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NUMBER`
- 실전: `KIS_REAL_APP_KEY`, `KIS_REAL_APP_SECRET`, `KIS_REAL_ACCOUNT_NUMBER`
- 계좌번호는 KIS 계좌 앞 8자리와 상품 코드 뒤 2자리를 붙인 형식입니다.
- Spring KIS 계좌조회는 전체 스위치 `KIS_ACCOUNT_READ_ENABLED=true`와 대상별 스위치가 모두 필요합니다. PAPER는 `KIS_PAPER_ACCOUNT_READ_ENABLED=true`, REAL은 `KIS_REAL_ACCOUNT_READ_ENABLED=true`입니다. REAL 스위치는 기본 false입니다.
- Python 실시간 시세 릴레이는 `KIS_WEBSOCKET_ENABLED=true`를 요구합니다. 기본값은 `false`이며 Paper 환경에서 먼저 확인합니다.
- Paper 자동 운영 스위치는 `PAPER_AUTOTRADING_ENABLED=false`가 기본값입니다. 달력·실시간 피드·모델 승인·브로커 상태와 리스크 게이트 검증 전에는 켜지 마세요. Live 모드에서는 Python paper 자동화도 강제로 비활성화됩니다.
- 저장소에는 `.env`를 커밋하지 않습니다. `.env.example`에는 실제 값을 입력하지 않습니다.
- 모델 승인·활성화·롤백에는 `.env`의 `MODEL_GOVERNANCE_TOKEN`에 32자 이상의 무작위 값을 설정하고 요청의 `X-Model-Governance-Token` 헤더로 전달해야 합니다. PowerShell에서는 `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`로 64자리 값을 생성할 수 있습니다. 값이 비어 있거나 32자보다 짧으면 변경 API는 잠긴 상태입니다.
- 모의 계좌 연결 테스트는 의도적으로 네트워크를 호출하는 별도 작업입니다: `.\scripts\test-paper-kis.ps1`. 이 스크립트는 paper 자격증명과 전체 및 PAPER 계좌 읽기 스위치를 확인한 다음 국내·미국 잔고 GET만 실행합니다.

## 실전 주문 게이트

기본은 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`입니다. REAL 내부 오케스트레이터는 REAL 자격정보와 전체/REAL 계좌 읽기 스위치, 세 주문 잠금, 해당 주문과 3초 이내에 결합된 서버 내부 위험 승인이 있어야만 주문 요청을 보냅니다. 주문 의도를 먼저 DB에 기록하고, 부분체결 취소에는 잔량을 쓰며, 불명 결과는 자동 재전송하지 않습니다. 주기적 실계좌 체결 조회는 별도 잠금으로 기본 비활성입니다. UNKNOWN 중 주문번호가 없는 건은 자동 추측·재주문하지 않습니다. P6의 paper 세션·장애 검증 전까지 세 주문 잠금을 열지 않습니다.
REAL 주문 상태의 읽기 전용 주기 대조는 `KIS_REAL_RECONCILIATION_ENABLED=false`가 기본입니다. 활성화하려면 REAL 계좌 읽기 스위치와 자격정보도 필요합니다. 주문번호가 저장된 접수/부분체결/취소대기 주문만 조회하며, 주문번호 없는 UNKNOWN은 자동 추측·재주문하지 않습니다.

## 로컬 PostgreSQL 통합 테스트

저장소 루트에서 `.\scripts\test-local-postgres-integration.ps1`을 실행하면 `.env`의 DB 설정으로 PAPER/REAL 주문 의도 저장·재조회, 환경 격리, 멱등 키 복구 테스트를 선택 실행합니다. 테스트는 KIS API를 호출하지 않으며, 스크립트는 환경변수를 현재 프로세스에서만 적용하고 실행 후 복구합니다. 기본 `mvnw test`에서는 해당 DB 통합 테스트를 건너뜁니다.

별도 Java 프로세스 종료 후 복구까지 점검하려면 `.\scripts\test-paper-process-restart.ps1`을 실행합니다. 로컬 PostgreSQL에 고유 멱등 키의 PAPER 대기 주문을 만든 뒤 Spring JVM을 종료하고 새 JVM에서 동일 주문을 조회·재제출해 ID와 상태가 유지되는지 확인한 후 주문을 취소합니다. 이 스크립트는 KIS API를 호출하지 않습니다.
