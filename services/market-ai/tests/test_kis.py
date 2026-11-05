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
