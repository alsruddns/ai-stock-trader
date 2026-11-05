# Architecture

## v1.0 구성

- **Java Spring Boot:** 주문 상태, 리스크 검사, paper broker, 향후 KIS 주문 어댑터의 유일한 소유자
- **Python FastAPI:** 시세 수집, WebSocket, 퀀트 지표, LightGBM/XGBoost 신호 생성
- **PostgreSQL:** 계좌·시세 스냅샷, 주문·체결·포지션, 신호·모델·감사 데이터
- **Next.js:** 로컬 운영 대시보드
- **LEAN:** 퀀트 전략의 과거 데이터 백테스트
- **KIS Open API:** 국내·미국 주식의 기본 증권사 연결

AI는 주문을 직접 제출하지 않습니다. Python은 버전과 만료 시각을 포함한 신호를 만들고, Spring Boot가 가격·계좌·포지션·손실 한도를 확인한 뒤 주문 여부를 결정합니다. 실거래 구현도 같은 경계를 유지합니다.

## 외부 경계

P1 계좌 API는 명시적 `paper` 또는 `real` 환경에서 인증정보와 계좌를 선택하고, 잔고·매수가능 정보만 GET으로 조회합니다. 주문·정정·취소 API 호출은 포함하지 않습니다. REST 시세와 WebSocket 시세는 각각 지연·연결상태와 함께 구분합니다.

## 프로세스 결합

Windows 로컬에서 Spring API는 `127.0.0.1:8080`, Python API는 `127.0.0.1:8000`에 바인딩합니다. 초기에 서비스 간 호출은 HTTP/JSON으로 유지하고, 신호 ID를 멱등성 키로 전달합니다. 키·시크릿은 프로세스 환경변수에서만 읽습니다.
