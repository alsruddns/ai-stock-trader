# KIS 국내주식 모의투자 취소 사전조회 문의 초안

아래 내용은 KIS Developers 문의에 사용할 초안입니다. 아직 외부로 발송하지 않았으며 계좌번호, App Key, App Secret, 접근토큰은 포함하지 않습니다.

## 문의 제목

국내주식 모의투자 취소 가능수량 사전조회 API 및 TR ID 확인 요청

## 문의 내용

안녕하세요. 국내주식 모의투자 계좌를 사용하는 개인 개발자입니다. 국내주식 모의 주문의 취소 전 잔량을 확인하는 공식 지원 경로를 확인하고 싶습니다.

공식 Open Trading API 저장소의 `inquire_psbl_rvsecncl` 예제는 `/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl` 호출 후 `output.psbl_qty`를 확인하고 취소 주문을 보내도록 안내합니다. 공식 모의투자 지원 목록에는 해당 조회 API의 모의투자 지원 여부가 표시되지 않은 반면, 국내 주식 주문·정정취소·일별 주문체결 조회는 모의투자 지원으로 표시되어 있습니다.

2026-11-16에 현행 공식 `order_rvsecncl` 예제를 추가 확인했습니다. 이 예제는 `env_dv=demo`를 지원하고 국내 취소 POST(`/uapi/domestic-stock/v1/trading/order-rvsecncl`)를 안내하지만, POST 전에 위 `inquire-psbl-rvsecncl`을 호출해 해당 주문의 `output.psbl_qty`를 확인하라고 명시합니다. 따라서 취소 POST 자체의 모의지원 여부와 취소수량 사전조회 경로의 모의지원 여부를 구분해 답변 부탁드립니다.

2026-11-16 17:10 KST에 계좌번호나 자격정보를 기록하지 않는 로컬 읽기 전용 점검으로 현재 공식 예제의 실전 TR ID `TTTC0084R`를 모의 ID `VTTC0084R`로 매핑해 조회했습니다. HTTP 200, `rt_cd=1`, `msg_cd=OPSQ0002`, 조회 행 0건이 반환됐고 `psbl_qty`는 없었습니다. 2026-11-15에 공식 legacy helper의 `TTTC8036R`를 `VTTC8036R`로 매핑한 조회도 `rt_cd=1`, `msg_cd=90000000`, 조회 행 0건이었습니다. 두 요청 모두 조회만 수행했으며 주문이나 취소를 전송하지 않았습니다.

다음 사항을 확인 부탁드립니다.

1. `inquire-psbl-rvsecncl` 조회가 국내 모의투자에서 지원되는지요?
2. 지원된다면 모의투자용 TR ID, 필수 요청 파라미터/조회 구분, 출력의 취소 가능수량 필드는 무엇인가요?
3. `VTTC0084R` 조회에서 반환된 `OPSQ0002`는 모의환경 미지원, TR ID 불일치, 별도 신청 필요 중 어떤 경우를 뜻하나요?
4. 지원되지 않는다면 국내 모의투자에서 미체결 주문을 취소하기 전에 남은 취소 가능 수량을 확인하는 권장 API 절차가 있나요?
5. 공식 예제가 `env_dv=demo`에서 취소 POST TR ID를 `VTTC0013U`로 설정합니다. 이 국내 PAPER 취소 TR ID와 실제 계좌 지원 여부가 맞는지요?
6. 모의 취소 POST 전에 필요한 주문별 `psbl_qty`를 얻는 공식 모의투자 API와 TR ID를 안내해 주실 수 있나요? 해당 조회가 모의에서 미지원이라면, KIS가 승인하는 대체 사전조회 방법이 있는지요?

계좌번호 및 자격정보는 문의 본문에 포함하지 않았습니다. 필요하다면 공식 보안 접수 절차에 따라 추가 정보를 제공하겠습니다.

## 참고 문서

- [KIS Open Trading API 모의투자 지원 목록](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/README.md#3-api-%EC%A0%9C%EA%B3%B5-%EB%AA%A9%EB%A1%9D)
- [KIS 국내주식 취소 가능수량 조회 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl/inquire_psbl_rvsecncl.py)
- [KIS 국내주식 정정취소 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/order_rvsecncl/order_rvsecncl.py)
