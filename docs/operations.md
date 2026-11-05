# Windows 운영

## 로컬 실행

1. PostgreSQL 서비스가 `localhost:5432`에서 실행 중인지 확인합니다.
2. 저장소 루트의 Git 제외 `.env`에 DB 비밀번호와 필요한 KIS 환경별 설정을 입력합니다.
3. Spring Boot를 실행합니다: `.\scripts\start-local.ps1`
4. Python API는 `services/market-ai` 가상환경에서 실행합니다. API는 `127.0.0.1`에만 바인딩합니다.

Java 빌드는 Gradle이 아닌 프로젝트 포함 Maven Wrapper를 사용합니다: `.\backend\trading-api\mvnw.cmd test`.

## KIS 자격증명

- 모의: `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NUMBER`
- 실전: `KIS_REAL_APP_KEY`, `KIS_REAL_APP_SECRET`, `KIS_REAL_ACCOUNT_NUMBER`
- 계좌번호는 KIS 계좌 앞 8자리와 상품 코드 뒤 2자리를 붙인 형식입니다.
- Spring KIS 계좌조회는 전체 스위치 `KIS_ACCOUNT_READ_ENABLED=true`와 대상별 스위치가 모두 필요합니다. PAPER는 `KIS_PAPER_ACCOUNT_READ_ENABLED=true`, REAL은 `KIS_REAL_ACCOUNT_READ_ENABLED=true`입니다. REAL 스위치는 기본 false입니다.
- Python 실시간 시세 릴레이는 `KIS_WEBSOCKET_ENABLED=true`를 요구합니다. 기본값은 `false`이며 Paper 환경에서 먼저 확인합니다.
- Paper 자동 운영 스위치는 `PAPER_AUTOTRADING_ENABLED=false`가 기본값입니다. 달력·실시간 피드·모델 승인·브로커 상태와 리스크 게이트 검증 전에는 켜지 마세요. Live 모드에서는 Python paper 자동화도 강제로 비활성화됩니다.
- 저장소에는 `.env`를 커밋하지 않습니다. `.env.example`에는 실제 값을 입력하지 않습니다.
- 모의 계좌 연결 테스트는 의도적으로 네트워크를 호출하는 별도 작업입니다: `.\scripts\test-paper-kis.ps1`. 이 스크립트는 paper 자격증명과 전체 및 PAPER 계좌 읽기 스위치를 확인한 다음 국내·미국 잔고 GET만 실행합니다.

## 실전 주문 게이트

기본은 `TRADING_MODE=paper`, `LIVE_TRADING_ENABLED=false`, `KIS_REAL_ORDERS_ENABLED=false`입니다. REAL 내부 오케스트레이터는 REAL 자격정보와 전체/REAL 계좌 읽기 스위치, 세 주문 잠금, 해당 주문과 3초 이내에 결합된 서버 내부 위험 승인이 있어야만 주문 요청을 보냅니다. 주문 의도를 먼저 DB에 기록하고, 부분체결 취소에는 잔량을 쓰며, 불명 결과는 자동 재전송하지 않습니다. 실계좌 체결 조회 메서드도 있지만 HTTP 운영 경로/감시 루프가 없어 자동 복구 작업은 아직 연결되지 않았습니다. P6의 paper 세션·장애 검증 전까지 세 주문 잠금을 열지 않습니다.
