# Database

PostgreSQL은 Windows 로컬 인스턴스 `localhost:5432`의 `ai_stock_trader` 데이터베이스를 사용합니다. Hibernate는 스키마를 직접 바꾸지 않고 `ddl-auto=validate`만 수행하며, 테이블 변경은 Flyway migration으로만 추가합니다.

## 현재 테이블

- `trade_order`: paper 주문, 상태·멱등성 키·요청/체결 수량
- `paper_fill`: paper 체결 이벤트와 고유 체결 키
- `paper_position`: 종목별 수량과 가중평균 매입원가
- `paper_cash_balance`: 통화별 가상 현금
- `market_quote_snapshot`: 선택 저장된 KIS 가격 조회 스냅샷
- `account_balance_snapshot`: 선택 저장된 KIS 계좌·보유 스냅샷
- `risk_event`, `market_signal`: 초기 감사·신호 테이블

## 저장 정책

Flyway는 애플리케이션 시작 시 순차 적용합니다. KIS 계좌 스냅샷 저장은 `KIS_SNAPSHOT_PERSISTENCE_ENABLED=true`로 별도 활성화하며, 계좌번호와 인증정보는 저장하지 않습니다. 스냅샷에는 잔고와 보유종목이 들어가므로 로컬 DB 파일·백업도 민감 데이터로 취급합니다.
