# KIS 인증 오류 문의 초안

KIS 공식 [고객의 소리 접수 페이지](https://securities.koreainvestment.com/main/customer/support/Support_1.jsp?cmd=PreInput)에서 아래 내용을 전달할 수 있습니다. 접수 페이지는 계좌번호·비밀번호 등 금융거래정보를 포함한 조회나 처리를 하지 않는다고 안내합니다. App Key, App Secret, 계좌번호, 액세스 토큰, 전체 응답 본문은 문의에 포함하지 마세요. 인증에 사용한 값이 필요하다고 안내받으면 공식 보안 채널에서만 제출합니다.

## 전달 문안

안녕하세요. Windows 로컬 PC에서 본인 KIS 계좌의 Open API 읽기 전용 연결을 설정하고 있습니다. 모의투자와 실전투자에 발급된 자격정보를 각각 해당 환경에 넣어 OAuth 토큰 발급만 확인했으나, 두 환경 모두 HTTP 403과 오류코드 `EGW00002`(서버 에러)가 반환됩니다.

- OAuth 재점검(PAPER 11:16, REAL 11:20 KST): 양쪽 HTTP 403, `EGW00002`; PAPER는 10:59부터 반복
- 계좌 GET(PAPER 국내 11:22 KST): HTTP 502, `EGW00002`; 미국 11:05 조회는 제한시간 초과. 잔고 응답 본문은 표시하지 않음
- PAPER 서버: `https://openapivts.koreainvestment.com:29443`
- REAL 서버: `https://openapi.koreainvestment.com:9443`
- 인증 경로: `POST /oauth2/tokenP`, `client_credentials`
- PAPER/REAL App Key·App Secret은 각 환경별로 분리해 사용하며 로컬 설정에서 값이 입력된 것을 확인했습니다.
- 공식 오류표상 `EGW00002`는 서버 에러입니다. 같은 오류가 두 환경에서 반복됩니다.
- 주문·정정·취소 요청은 보내지 않았습니다.

두 환경의 API 서비스 상태 및 인증 자격정보 등록 상태를 확인해 주시고, 로컬에서 추가로 점검해야 할 신청·계정 연결·접근 설정이 있다면 알려주시기 바랍니다. 문의 식별에 필요한 안전한 진단 정보를 안내해 주시면 공식 채널을 통해 제공하겠습니다.

계정 연결도 확인해 주세요. KIS Developers의 모의투자 안내는 HTS ID 변경 또는 잘못된 모의/실전 계좌 연결이 계좌 처리 오류 `90070000`의 원인이 될 수 있다고 설명합니다. 이번 관측 코드는 `EGW00002`로 서로 다르므로 동일 원인이라고 가정하지 않으며, 현재 HTS ID와 PAPER 주식계좌의 신청 연결만 확인 부탁드립니다.

## 참고

- [KIS Developers 오류 코드](https://apiportal.koreainvestment.com/faq-error-code)
- [KIS Developers 서비스 이용안내](https://apiportal.koreainvestment.com/about-howto) — 서버 등 장애 오류는 고객의 소리 접수를 안내합니다.
- [KIS Developers 공지사항](https://apiportal.koreainvestment.com/community/10000000-0000-0011-0000-000000000001) — 모의투자 API 계정 연결 및 HTS ID 변경 관련 안내
- [KIS 공식 Open API 샘플 저장소](https://github.com/koreainvestment/open-trading-api)

이 문안은 초안이며 KIS에 전송되지 않았습니다.
