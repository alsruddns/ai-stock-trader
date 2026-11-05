from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.kis import KisApiError, KisReadOnlyClient, KisSettings


def _token_response() -> dict[str, str]:
    expiry = (datetime.now(timezone(timedelta(hours=9))) + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    return {"access_token": "test-token", "access_token_token_expired": expiry}


def test_domestic_quote_uses_read_only_endpoint_and_reuses_token() -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={
            "rt_cd": "0",
            "output": {
                "stck_prpr": "73500", "prdy_vrss": "500", "prdy_ctrt": "0.68", "acml_vol": "12345"
            },
        })

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret"), http_client=http
    )
    first = client.get_quote("KR", "005930")
    second = client.get_quote("KR", "005930")

    assert first.price == 73500
    assert first.currency == "KRW"
    assert first.volume == 12345
    assert first.source == "KIS_REST_SNAPSHOT"
    assert second.price == first.price
    assert sum(request.url.path == "/oauth2/tokenP" for request in requests_seen) == 1
    quote_request = requests_seen[1]
    assert quote_request.method == "GET"
    assert quote_request.url.path.endswith("/inquire-price")
    assert quote_request.headers["tr_id"] == "FHKST01010100"
    assert quote_request.url.params["FID_INPUT_ISCD"] == "005930"
    assert "paper-secret" not in str(quote_request.url)
    client.close()


def test_us_quote_uses_official_exchange_code_and_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={
            "rt_cd": "0",
            "output": {"last": "231.59", "diff": "3.57", "rate": "1.57", "tvol": "38914221"},
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    quote = client.get_quote("US", "aapl", "NAS")
    assert quote.symbol == "AAPL"
    assert quote.exchange == "NAS"
    assert str(quote.price) == "231.59"
    assert quote.currency == "USD"
    assert str(quote.change_percent) == "1.57"
    client.close()


def test_invalid_kr_symbol_is_rejected_before_network_request() -> None:
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid symbol must not make a request")
        )),
    )
    with pytest.raises(ValueError, match="six digits"):
        client.get_quote("KR", "ABC")
    client.close()


def test_non_successful_kis_response_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "sensitive"})

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(KisApiError, match="EGW00123") as exc:
        client.get_quote("KR", "005930")
    assert "sensitive" not in str(exc.value)
    client.close()


@pytest.mark.parametrize(
    ("market", "environment", "expected_path", "expected_tr", "body"),
    [
        ("KR", "paper", "/uapi/domestic-stock/v1/trading/inquire-balance", "VTTC8434R", {
            "rt_cd": "0", "output1": [{"pdno": "005930", "hldg_qty": "2", "CANO": "do-not-return"}],
            "output2": {"dnca_tot_amt": "100000", "secret_field": "do-not-return"},
        }),
        ("US", "real", "/uapi/overseas-stock/v1/trading/inquire-balance", "TTTS3012R", {
            "rt_cd": "0", "output1": {"frcr_dncl_amt_2": "50.00"},
            "output2": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "1", "CANO": "do-not-return"}],
        }),
    ],
)
def test_account_balance_uses_environment_specific_read_only_contract(
    market: str, environment: str, expected_path: str, expected_tr: str, body: dict[str, object]
) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json=body)

    client = KisReadOnlyClient(
        KisSettings(True, environment, "test-key", "test-secret", "1234567801"),  # type: ignore[arg-type]
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    balance = client.get_account_balance(market)  # type: ignore[arg-type]

    request = requests_seen[-1]
    assert request.method == "GET"
    assert request.url.path == expected_path
    assert request.headers["tr_id"] == expected_tr
    assert request.url.params["CANO"] == "12345678"
    assert request.url.params["ACNT_PRDT_CD"] == "01"
    assert balance.environment == environment
    assert "CANO" not in str(balance)
    assert "secret_field" not in str(balance)
    client.close()


def test_domestic_account_balance_merges_official_continuation_pages() -> None:
    market_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        market_requests.append(request)
        if len(market_requests) == 1:
            return httpx.Response(200, headers={"tr_cont": "M"}, json={
                "rt_cd": "0", "output1": [{"pdno": "005930", "hldg_qty": "2"}],
                "output2": {"dnca_tot_amt": "100000"},
                "ctx_area_fk100": "next-fk", "ctx_area_nk100": "next-nk",
            })
        return httpx.Response(200, headers={"tr_cont": "N"}, json={
            "rt_cd": "0", "output1": [{"pdno": "000660", "hldg_qty": "1"}],
            "output2": {"dnca_tot_amt": "100000"},
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "test-key", "test-secret", "1234567801"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    balance = client.get_account_balance("KR")

    assert [position["pdno"] for position in balance.positions] == ["005930", "000660"]
    assert balance.summary["dnca_tot_amt"] == "100000"
    assert len(market_requests) == 2
    assert market_requests[1].headers["tr_cont"] == "N"
    assert market_requests[1].url.params["CTX_AREA_FK100"] == "next-fk"
    assert market_requests[1].url.params["CTX_AREA_NK100"] == "next-nk"
    client.close()


def test_overseas_account_balance_uses_200_cursor_fields_for_continuation() -> None:
    market_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        market_requests.append(request)
        if len(market_requests) == 1:
            return httpx.Response(200, headers={"tr_cont": "M"}, json={
                "rt_cd": "0", "output1": {"frcr_dncl_amt_2": "50.00"},
                "output2": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "1"}],
                "ctx_area_fk200": "next-fk", "ctx_area_nk200": "next-nk",
            })
        return httpx.Response(200, headers={"tr_cont": "N"}, json={
            "rt_cd": "0", "output1": {"frcr_dncl_amt_2": "50.00"},
            "output2": [{"ovrs_pdno": "MSFT", "ovrs_cblc_qty": "2"}],
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "test-key", "test-secret", "1234567801"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    balance = client.get_account_balance("US")

    assert [position["ovrs_pdno"] for position in balance.positions] == ["AAPL", "MSFT"]
    assert balance.summary["frcr_dncl_amt_2"] == "50.00"
    assert market_requests[1].url.params["CTX_AREA_FK200"] == "next-fk"
    assert market_requests[1].url.params["CTX_AREA_NK200"] == "next-nk"
    client.close()


def test_invalid_account_number_is_rejected_before_network_request() -> None:
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", "not-an-account"),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid account must not make a request")
        )),
    )
    with pytest.raises(ValueError, match="10 digits"):
        client.get_account_balance("KR")
    client.close()
