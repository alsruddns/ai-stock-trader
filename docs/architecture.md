# Architecture

## v1.0 구성

- **Java Spring Boot:** 주문 상태, 리스크 검사, paper broker, KIS paper/REAL 주문 어댑터 및 운영 요약의 유일한 소유자
- **Python FastAPI:** 시세 수집, WebSocket, 퀀트 지표, LightGBM/XGBoost 신호 생성
- **PostgreSQL:** 계좌·시세 스냅샷, 주문·체결·포지션, 신호·모델·감사 데이터
- **Next.js:** 로컬 운영 대시보드
- **LEAN:** 공식 오픈소스 엔진을 Windows 로컬에서 빌드하고 C# ORB/VWAP/EMA/ATR 전략과 로컬 KIS CSV를 재생합니다. `scripts/run-lean-backtest.ps1`은 한 세션을 Python 기준 엔진과 대조하고, `scripts/run-lean-backtest-range.ps1`은 동일 종목의 독립 세션들을 순차 검증합니다. 선택한 로컬 후보 모델의 AI 확률 필터도 적용할 수 있습니다. 이 네이티브 경로에는 Docker나 QuantConnect 유료 조직 계정이 필요하지 않으며, 별도 LEAN CLI 경로는 Docker와 조직 계정이 필요합니다. 국내 005930·000660 각각 11월 11일·13일 총 4개 세션의 퀀트 재현은 일치했으나, 독립 세션 합계는 공용자금 성과가 아니며 AI 후보의 재현·홀드아웃 성능은 아직 검증되지 않았습니다.
- **KIS Open API:** 국내·미국 주식의 기본 증권사 연결

AI는 주문을 직접 제출하지 않습니다. Python의 모델 예측은 시장별로 버전이 구분되는 advisory 신호이며 `executable=false`를 유지합니다. Spring Boot에는 PAPER와 REAL 주문 어댑터 및 오케스트레이터가 있습니다. REAL 주문 HTTP 경로는 `/api/v1/kis/orders/real`이며 REAL 거래 모드, live 주문 스위치, 전체·REAL 계좌 읽기 스위치를 모두 통과해야 합니다. 기본값은 PAPER 모드와 모든 REAL 주문 잠금 비활성입니다. 실제 KIS 계좌 주문은 이 개발 상태에서 전송하지 않았습니다.

Next.js 대시보드는 server-side API route를 통해 loopback Spring/Python API를 polling합니다. 주문 제출·취소 UI를 제공하지 않습니다. 모델 파일은 PostgreSQL에 저장하지 않고 `.local/models` 아래 로컬 아티팩트와 메타데이터로 관리하며, 승인·활성·롤백 이벤트를 JSONL 로컬 기록으로 남깁니다.

## 외부 경계

P1 계좌 API는 명시적 `paper` 또는 `real` 환경에서 인증정보와 계좌를 선택하고, 잔고·매수가능 정보만 GET으로 조회합니다. REAL 주문 경로는 계좌 잔고 사전조회 후 멱등 주문 제출·상태 조회·체결 대조·취소를 Spring REAL 오케스트레이터에 전달합니다. REAL 쓰기는 모든 주문/계좌 잠금이 열린 경우에만 시도되며, 자동 재전송하지 않습니다. REST 시세와 WebSocket 시세는 각각 지연·연결상태와 함께 구분합니다.

## 프로세스 결합

Windows 로컬에서 Spring API는 `127.0.0.1:18080`, Python API는 `127.0.0.1:18000`에 바인딩합니다. 초기에 서비스 간 호출은 HTTP/JSON으로 유지하고, 신호 ID를 멱등성 키로 전달합니다. 키·시크릿은 프로세스 환경변수에서만 읽습니다.
