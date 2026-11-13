from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from app.kis import KisApiError, KisRateLimitError, KisReadOnlyClient, KisSettings


def _token_response() -> dict[str, str]:
    expiry = (datetime.now(timezone(timedelta(hours=9))) + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    return {"access_token": "test-token", "access_token_token_expired": expiry}


def test_kis_settings_repr_masks_app_credentials_and_account_number() -> None:
    settings = KisSettings(True, "real", "app-key-sensitive", "app-secret-sensitive", "1234567801")

    representation = repr(settings)

    assert "app-key-sensitive" not in representation
    assert "app-secret-sensitive" not in representation
    assert "1234567801" not in representation
    assert "********01" in representation
    assert "credentials_configured=True" in representation


def test_kis_market_data_settings_keep_real_rest_separately_gated(monkeypatch) -> None:
    monkeypatch.setenv("KIS_MARKET_DATA_ENABLED", "true")
    monkeypatch.setenv("KIS_REAL_MARKET_DATA_ENABLED", "false")
    assert KisSettings.from_environment("paper").enabled is True
    assert KisSettings.from_environment("real").enabled is False

    monkeypatch.setenv("KIS_REAL_MARKET_DATA_ENABLED", "true")
    assert KisSettings.from_environment("real").enabled is True


def test_kis_rest_pacing_uses_paper_floor_and_serialized_slots(monkeypatch) -> None:
    moments = iter([100.0, 100.05])
    delays = []
    monkeypatch.setenv("KIS_PAPER_REST_MIN_INTERVAL_SECONDS", "0.2")
    monkeypatch.setattr("app.kis.time.monotonic", lambda: next(moments))
    monkeypatch.setattr("app.kis.time.sleep", delays.append)
    client = KisReadOnlyClient(KisSettings(True, "paper", "paper-key", "paper-secret"),
                               http_client=httpx.Client(transport=httpx.MockTransport(
                                   lambda request: httpx.Response(200, json={})
                               )))

    assert client._rest_min_interval == 1.0
    client._wait_for_rest_slot()
    client._wait_for_rest_slot()

    assert delays == [pytest.approx(0.95)]
    client.close()


def test_kis_real_rest_pacing_uses_conservative_minimum_interval(monkeypatch) -> None:
    monkeypatch.setenv("KIS_REAL_REST_MIN_INTERVAL_SECONDS", "0.01")
    client = KisReadOnlyClient(KisSettings(True, "real", "real-key", "real-secret"),
                               http_client=httpx.Client(transport=httpx.MockTransport(
                                   lambda request: httpx.Response(200, json={})
                               )))

    assert client._rest_min_interval == pytest.approx(1.0)
    client.close()


def test_http_500_egw00201_maps_to_safe_rate_limit_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(500, json={
            "msg_cd": "EGW00201", "msg1": "private rate detail", "access_token": "never-show"
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", "1234567801",
                    shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisRateLimitError, match="KIS rate limit reached") as captured:
        client.get_account_balance("KR")

    assert "private rate detail" not in str(captured.value)
    assert "never-show" not in str(captured.value)
    assert captured.value.http_status == 500
    assert captured.value.broker_code == "EGW00201"
    assert captured.value.response_content_type == "application/json"
    client.close()


def test_read_only_get_retries_one_transient_http_502(monkeypatch, caplog) -> None:
    responses = iter([
        httpx.Response(502, json={"msg_cd": "EGW00002", "msg1": "private server detail"}),
        httpx.Response(200, json={"rt_cd": "0", "output": {"value": "ok"}}),
    ])
    requests_seen: list[httpx.Request] = []
    slots: list[bool] = []
    delays: list[float] = []
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: requests_seen.append(request) or next(responses)
        )),
    )
    monkeypatch.setattr(client, "_wait_for_rest_slot", lambda: slots.append(True))
    monkeypatch.setattr("app.kis.time.sleep", delays.append)

    response = client._get("https://kis.example/read-only", headers={}, params={})

    assert response.status_code == 200
    assert len(requests_seen) == 2
    assert len(slots) == 2
    assert delays == [0.5]
    assert "private server detail" not in caplog.text
    client.close()


def test_read_only_get_retries_http_200_egw00002_but_not_rate_limit(monkeypatch) -> None:
    responses = iter([
        httpx.Response(200, json={"rt_cd": "1", "msg_cd": "EGW00002", "msg1": "private detail"}),
        httpx.Response(200, json={"rt_cd": "0", "output": {"value": "ok"}}),
    ])
    requests_seen: list[httpx.Request] = []
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: requests_seen.append(request) or next(responses)
        )),
    )
    monkeypatch.setattr(client, "_wait_for_rest_slot", lambda: None)
    monkeypatch.setattr("app.kis.time.sleep", lambda _seconds: None)

    response = client._get("https://kis.example/read-only", headers={}, params={})
    assert response.status_code == 200
    assert len(requests_seen) == 2
    client.close()

    rate_limited: list[httpx.Request] = []
    limited_client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: (
            rate_limited.append(request) or httpx.Response(
                500, json={"msg_cd": "EGW00201", "msg1": "private quota detail"}
            )
        ))),
    )
    monkeypatch.setattr(limited_client, "_wait_for_rest_slot", lambda: None)

    limited = limited_client._get("https://kis.example/read-only", headers={}, params={})

    assert limited.status_code == 500
    assert len(rate_limited) == 1
    limited_client.close()


def test_read_only_get_retries_one_transport_failure(monkeypatch) -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if len(requests_seen) == 1:
            raise httpx.ReadTimeout("private transport details", request=request)
        return httpx.Response(200, json={"ok": True})

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    slots: list[bool] = []
    monkeypatch.setattr(client, "_wait_for_rest_slot", lambda: slots.append(True))
    monkeypatch.setattr("app.kis.time.sleep", lambda _seconds: None)

    response = client._get("https://kis.example/read-only", headers={}, params={})

    assert response.status_code == 200
    assert len(requests_seen) == 2
    assert len(slots) == 2
    client.close()


def test_read_only_get_does_not_retry_non_transport_configuration_errors(monkeypatch) -> None:
    slots: list[bool] = []
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.UnsupportedProtocol("private configuration detail")

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(client, "_wait_for_rest_slot", lambda: slots.append(True))
    monkeypatch.setattr("app.kis.time.sleep", delays.append)

    with pytest.raises(httpx.UnsupportedProtocol):
        client._get("https://kis.example/read-only", headers={}, params={})

    assert len(slots) == 1
    assert delays == []
    client.close()


def test_read_only_get_stops_after_one_retry_when_server_remains_unavailable(monkeypatch) -> None:
    requests_seen: list[httpx.Request] = []
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: (
            requests_seen.append(request) or httpx.Response(
                502, json={"msg_cd": "EGW00002", "msg1": "private server detail"}
            )
        ))),
    )
    monkeypatch.setattr(client, "_wait_for_rest_slot", lambda: None)
    monkeypatch.setattr("app.kis.time.sleep", lambda _seconds: None)

    response = client._get("https://kis.example/read-only", headers={}, params={})

    assert response.status_code == 502
    assert len(requests_seen) == 2
    client.close()


@pytest.mark.parametrize(
    ("headers", "expected_content_type"),
    [
        ({"content-type": "text/html; charset=utf-8"}, "text/html"),
        ({"content-type": "application/json"}, "application/json"),
        ({"content-type": "not-a-media-type"}, None),
    ],
)
def test_authentication_http_error_exposes_only_safe_response_metadata(
    headers: dict[str, str], expected_content_type: str | None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers=headers, json={
            "error_code": "private-code", "message": "sensitive response body",
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", "1234567801",
                    shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisApiError, match="HTTP 403, code unknown") as captured:
        client._access_token()

    assert captured.value.http_status == 403
    assert captured.value.broker_code == "unknown"
    assert captured.value.response_content_type == expected_content_type
    assert captured.value.response_bytes > 0
    assert captured.value.response_fields == ("error_code", "message")
    assert "sensitive response body" not in str(captured.value)
    assert "private-code" not in str(captured.value)
    assert "paper-secret" not in str(captured.value)
    client.close()


def test_authentication_error_category_is_allowlisted_and_description_is_never_exposed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"content-type": "application/json"}, json={
            "error_code": "invalid_client", "error_description": "private detail and secret",
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", "1234567801",
                    shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisApiError) as captured:
        client._access_token()

    assert captured.value.response_error_category == "invalid_client"
    assert captured.value.response_error_code == "invalid_client"
    assert "private detail" not in str(captured.value)
    assert "paper-secret" not in str(captured.value)
    client.close()


def test_kis_egw00002_auth_error_is_classified_as_broker_server_error(monkeypatch) -> None:
    token_slots: list[bool] = []
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"content-type": "application/json"}, json={
            "error_code": "EGW00002", "error_description": "private detail",
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", "1234567801",
                    shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(client, "_wait_for_token_slot", lambda: token_slots.append(True))

    with pytest.raises(KisApiError) as captured:
        client._access_token()

    assert len(token_slots) == 2
    assert captured.value.response_error_code == "EGW00002"
    assert captured.value.response_error_category == "broker_server_error"
    assert "private detail" not in str(captured.value)
    client.close()


def test_kis_egw00002_auth_error_retries_once_after_token_slot(monkeypatch) -> None:
    token_slots: list[bool] = []
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if len(requests_seen) == 1:
            return httpx.Response(403, headers={"content-type": "application/json"}, json={
                "error_code": "EGW00002", "error_description": "private detail",
            })
        return httpx.Response(200, json=_token_response())

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", "1234567801",
                    shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(client, "_wait_for_token_slot", lambda: token_slots.append(True))

    assert client._access_token() == "test-token"
    assert len(requests_seen) == 2
    assert len(token_slots) == 2
    assert all(request.url.path == "/oauth2/tokenP" for request in requests_seen)
    assert "paper-secret" not in str(requests_seen)
    client.close()


def test_domestic_quote_uses_read_only_endpoint_and_reuses_token() -> None:
    requests_seen: list[httpx.Request] = []
    quote_responses: list[httpx.Response] = []

    class CountingResponse(httpx.Response):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.json_calls = 0

        def json(self, **kwargs):
            self.json_calls += 1
            return super().json(**kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        response = CountingResponse(200, json={
            "rt_cd": "0",
            "output": {
                "stck_prpr": "73500", "prdy_vrss": "500", "prdy_ctrt": "0.68", "acml_vol": "12345"
            },
        })
        quote_responses.append(response)
        return response

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
    assert len(quote_responses) == 2
    assert [response.json_calls for response in quote_responses] == [1, 1]
    assert sum(request.url.path == "/oauth2/tokenP" for request in requests_seen) == 1
    quote_request = requests_seen[1]
    assert quote_request.method == "GET"
    assert quote_request.url.path.endswith("/inquire-price")
    assert quote_request.headers["tr_id"] == "FHKST01010100"
    assert quote_request.url.params["FID_INPUT_ISCD"] == "005930"
    assert "paper-secret" not in str(quote_request.url)
    client.close()


@pytest.mark.parametrize("array_output", [False, True])
def test_us_quote_uses_official_exchange_code_and_fields(array_output: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        output = {"last": "231.59", "diff": "3.57", "rate": "1.57", "tvol": "38914221"}
        return httpx.Response(200, json={
            "rt_cd": "0",
            "output": [output] if array_output else output,
        })

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    quote = client.get_quote("US", "aapl", "NAS")
    assert quote.symbol == "AAPL"
    assert quote.exchange == "NAS"
    assert str(quote.price) == "231.59"
    assert quote.currency == "USD"
    assert str(quote.change_percent) == "1.57"
    client.close()


@pytest.mark.parametrize("output", [[], [{"last": "231.59"}, {"last": "232.00"}], ["malformed"]])
def test_us_quote_rejects_ambiguous_or_invalid_output_arrays(output: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={"rt_cd": "0", "output": output})

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisApiError, match="exactly one output object"):
        client.get_quote("US", "F", "NAS")
    client.close()


@pytest.mark.parametrize(
    ("error_type", "expected_category"),
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "connect"),
        (httpx.RemoteProtocolError, "protocol"),
    ],
)
def test_us_quote_transport_failures_expose_only_safe_category(error_type, expected_category: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        raise error_type("private upstream detail and token", request=request)

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisApiError, match=f"transport category: {expected_category}") as captured:
        client.get_quote("US", "F", "NAS")

    assert captured.value.transport_error_category == expected_category
    assert "private upstream detail" not in str(captured.value)
    assert "token" not in str(captured.value)
    client.close()


def test_us_quote_invalid_json_exposes_only_invalid_response_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, content=b"private response body", headers={"content-type": "application/json"})

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisApiError, match="transport category: invalid_response") as captured:
        client.get_quote("US", "F", "NAS")

    assert captured.value.transport_error_category == "invalid_response"
    assert "private response body" not in str(captured.value)
    client.close()


def test_domestic_candidates_use_official_turnover_rank_and_require_fresh_quote_spread() -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        if request.url.path.endswith("/volume-rank"):
            return httpx.Response(200, json={"rt_cd": "0", "output": [{
                "data_rank": "1", "mksc_shrn_iscd": "005930", "hts_kor_isnm": "Samsung",
                "acml_vol": "1000000", "acml_tr_pbmn": "200000000000",
            }]})
        return httpx.Response(200, json={"rt_cd": "0", "output": {
            "stck_prpr": "70000", "askp1": "70010", "bidp1": "70000",
            "prdy_vrss": "0", "prdy_ctrt": "0", "acml_vol": "1000000",
        }})

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    candidates = client.get_ranked_candidates("KR", minimum_turnover=Decimal("1000000000"))

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.symbol == "005930"
    assert candidate.turnover == Decimal("200000000000")
    assert candidate.spread_bps == pytest.approx(Decimal("1.428469"), abs=Decimal("0.000001"))
    assert candidate.passes_market_filter is True
    assert candidate.executable is False
    ranking_request = next(request for request in requests_seen if request.url.path.endswith("/volume-rank"))
    assert ranking_request.headers["tr_id"] == "FHPST01710000"
    assert ranking_request.url.params["FID_BLNG_CLS_CODE"] == "3"
    assert sum(request.url.path.endswith("/inquire-price") for request in requests_seen) == 1
    client.close()


def test_overseas_candidates_use_official_trade_value_ranking_and_fail_closed_without_spread() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={"rt_cd": "0", "output2": [{
            "rank": "1", "symb": "AAPL", "name": "Apple", "last": "230.00",
            "tvol": "1000000", "tamt": "230000000", "pask": "", "pbid": "",
        }]})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    candidates = client.get_ranked_candidates("US", exchange="NAS")

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.symbol == "AAPL"
    assert candidate.turnover == Decimal("230000000")
    assert candidate.spread_bps is None
    assert candidate.passes_market_filter is False
    assert candidate.filter_reason == "LIVE_SPREAD_UNAVAILABLE"
    assert candidate.executable is False
    client.close()


def test_market_candidate_rejects_crossed_spread_and_invalid_limits() -> None:
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))),
    )

    with pytest.raises(KisApiError, match="crossed bid/ask"):
        client._spread_bps(Decimal("99"), Decimal("100"))
    with pytest.raises(ValueError, match="limit must be between 1 and 20"):
        client.get_ranked_candidates("KR", limit=21)
    client.close()


def test_domestic_minute_chart_uses_read_only_contract_and_preserves_missing_spread() -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={"rt_cd": "0", "output2": [
            {"stck_bsop_date": "20261105", "stck_cntg_hour": "090100", "stck_oprc": "101",
             "stck_hgpr": "103", "stck_lwpr": "100", "stck_prpr": "102", "cntg_vol": "1200"},
            {"stck_bsop_date": "20261105", "stck_cntg_hour": "090000", "stck_oprc": "100",
             "stck_hgpr": "101", "stck_lwpr": "99", "stck_prpr": "100", "cntg_vol": "1000"},
            {"stck_bsop_date": "20261104", "stck_cntg_hour": "153000", "stck_oprc": "99",
             "stck_hgpr": "100", "stck_lwpr": "98", "stck_prpr": "99", "cntg_vol": "900"},
        ]})

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    from datetime import date

    bars = client.get_domestic_minute_bars("005930", date(2026, 11, 5))

    request = requests_seen[-1]
    assert request.method == "GET"
    assert request.url.path.endswith("/inquire-time-dailychartprice")
    assert request.headers["tr_id"] == "FHKST03010230"
    assert request.url.params["FID_INPUT_DATE_1"] == "20261105"
    assert request.url.params["FID_PW_DATA_INCU_YN"] == "Y"
    assert [bar.timestamp.isoformat() for bar in bars] == [
        "2026-11-05T09:00:00+09:00", "2026-11-05T09:01:00+09:00",
    ]
    assert [bar.volume for bar in bars] == [1000, 1200]
    assert all(bar.spread_bps is None for bar in bars)
    client.close()


def test_domestic_minute_chart_rejects_malformed_kis_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={"rt_cd": "0", "output2": [{"stck_bsop_date": "20261105"}]})

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    from datetime import date

    with pytest.raises(KisApiError, match="invalid bar data"):
        client.get_domestic_minute_bars("005930", date(2026, 11, 5))
    client.close()


def test_domestic_session_chart_walks_backward_windows_and_sorts_the_full_session() -> None:
    requests_seen: list[httpx.Request] = []
    pages = {
        "153000": ["132100", "153000"],
        "132000": ["112100", "132000"],
        "112000": ["092100", "112000"],
        "092000": ["090000", "092000"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        requests_seen.append(request)
        cursor = request.url.params["FID_INPUT_HOUR_1"]
        rows = [{
            "stck_bsop_date": "20261105", "stck_cntg_hour": hour,
            "stck_oprc": "100", "stck_hgpr": "101", "stck_lwpr": "99",
            "stck_prpr": "100", "cntg_vol": "1000",
        } for hour in pages[cursor]]
        return httpx.Response(200, json={"rt_cd": "0", "output2": rows})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "read-key", "read-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    from datetime import date

    history = client.get_domestic_minute_session_snapshot("005930", date(2026, 11, 5))
    bars = history.bars

    assert [request.url.params["FID_INPUT_HOUR_1"] for request in requests_seen] == list(pages)
    assert history.pages_fetched == 4
    assert history.source_record_count == 8
    assert history.first_source_timestamp.isoformat() == "2026-11-05T09:00:00+09:00"
    assert history.last_source_timestamp.isoformat() == "2026-11-05T15:30:00+09:00"
    assert history.continuation_states == []
    assert [bar.timestamp.strftime("%H%M%S") for bar in bars] == [
        "090000", "092000", "092100", "112000", "112100", "132000", "132100",
    ]
    assert all(bar.spread_bps is None for bar in bars)
    assert client.get_domestic_minute_session_bars("005930", date(2026, 11, 5)) == bars
    assert len(requests_seen) == 8
    client.close()


def test_overseas_minute_chart_uses_official_bounded_contract_and_new_york_timezone() -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={"rt_cd": "0", "output2": [
            {"xymd": "20261105", "xhms": "093100", "open": "101.1", "high": "103.0",
             "low": "100.5", "last": "102.5", "evol": "1200"},
            {"xymd": "20261105", "xhms": "093000", "open": "100", "high": "101",
             "low": "99", "last": "100.5", "evol": "1000"},
            {"xymd": "20261104", "xhms": "155900", "open": "99", "high": "99",
             "low": "98", "last": "98.5", "evol": "500"},
        ]})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    bars = client.get_overseas_minute_bars("aapl", "NAS", date(2026, 11, 5))
    request = requests_seen[-1]
    assert request.url.path.endswith("/inquire-time-itemchartprice")
    assert request.headers["tr_id"] == "HHDFS76950200"
    assert request.url.params["EXCD"] == "NAS"
    assert request.url.params["SYMB"] == "AAPL"
    assert request.url.params["NMIN"] == "1"
    assert request.url.params["PINC"] == "0"
    assert request.url.params["NREC"] == "120"
    assert request.url.params["NEXT"] == ""
    assert [bar.timestamp.isoformat() for bar in bars] == [
        "2026-11-05T09:30:00-05:00", "2026-11-05T09:31:00-05:00",
    ]
    assert [bar.volume for bar in bars] == [1000, 1200]
    assert all(bar.spread_bps is None for bar in bars)
    client.close()


def test_overseas_minute_session_pages_back_to_requested_date_with_separate_cursor() -> None:
    requests_seen: list[httpx.Request] = []
    pages = [
        ([
            {"xymd": "20261106", "xhms": "093100", "open": "101", "high": "102",
             "low": "100", "last": "101.5", "evol": "1200"},
            {"xymd": "20261106", "xhms": "093000", "open": "100", "high": "101",
             "low": "99", "last": "100.5", "evol": "1000"},
        ], "M"),
        ([
            {"xymd": "20261105", "xhms": "155900", "open": "110", "high": "111",
             "low": "109", "last": "110.5", "evol": "1500"},
            {"xymd": "20261105", "xhms": "155800", "open": "109", "high": "110",
             "low": "108", "last": "109.5", "evol": "1400"},
        ], "M"),
        ([
            {"xymd": "20261105", "xhms": "093000", "open": "100", "high": "101",
             "low": "99", "last": "100.5", "evol": "1000"},
            {"xymd": "20261104", "xhms": "155900", "open": "99", "high": "100",
             "low": "98", "last": "99.5", "evol": "900"},
        ], "D"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        rows, continuation = pages[len([r for r in requests_seen if r.url.path.endswith("inquire-time-itemchartprice")]) - 1]
        return httpx.Response(200, headers={"tr_cont": continuation}, json={"rt_cd": "0", "output2": rows})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    history = client.get_overseas_minute_session_snapshot("aapl", "NAS", date(2026, 11, 5))
    bars = history.bars

    assert [bar.timestamp.isoformat() for bar in bars] == [
        "2026-11-05T09:30:00-05:00", "2026-11-05T15:58:00-05:00", "2026-11-05T15:59:00-05:00",
    ]
    assert len([request for request in requests_seen if request.url.path.endswith("inquire-time-itemchartprice")]) == 3
    second = requests_seen[2]
    assert second.url.params["PINC"] == "1"
    assert second.url.params["NEXT"] == "1"
    assert second.url.params["KEYB"] == "20261106092900"
    assert second.headers["tr_cont"] == "N"
    assert all(bar.spread_bps is None for bar in bars)
    assert history.pages_fetched == 3
    assert history.source_record_count == 6
    assert history.first_source_timestamp.isoformat() == "2026-11-04T15:59:00-05:00"
    assert history.last_source_timestamp.isoformat() == "2026-11-06T09:31:00-05:00"
    assert history.continuation_states == ["M", "M", "D"]
    client.close()


def test_overseas_minute_session_uses_cursor_when_full_page_has_no_continuation_header() -> None:
    requests_seen: list[httpx.Request] = []
    first_rows = []
    first_start = datetime(2026, 11, 6, 16, 0)
    for minute in range(120):
        timestamp = first_start + timedelta(minutes=minute)
        first_rows.append({
            "xymd": timestamp.strftime("%Y%m%d"), "xhms": timestamp.strftime("%H%M%S"),
            "open": "100", "high": "101", "low": "99", "last": "100.5", "evol": "1000",
        })
    older_rows = [
        {"xymd": "20261105", "xhms": "155900", "open": "110", "high": "111",
         "low": "109", "last": "110.5", "evol": "1500"},
        {"xymd": "20261104", "xhms": "155900", "open": "99", "high": "100",
         "low": "98", "last": "99.5", "evol": "900"},
    ]
    chart_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chart_requests
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        chart_requests += 1
        if chart_requests == 1:
            return httpx.Response(200, json={"rt_cd": "0", "output2": first_rows})
        return httpx.Response(200, headers={"tr_cont": "D"}, json={"rt_cd": "0", "output2": older_rows})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    history = client.get_overseas_minute_session_snapshot("AAPL", "NAS", date(2026, 11, 5))

    chart = [request for request in requests_seen if request.url.path.endswith("inquire-time-itemchartprice")]
    assert history.pages_fetched == 2
    assert history.source_record_count == 122
    assert history.continuation_states == ["EMPTY", "D"]
    assert [bar.timestamp.isoformat() for bar in history.bars] == ["2026-11-05T15:59:00-05:00"]
    assert chart[1].url.params["NEXT"] == "1"
    assert chart[1].url.params["KEYB"] == "20261106155900"
    assert chart[1].headers["tr_cont"] == "N"
    client.close()


def test_overseas_minute_session_recovers_full_day_across_many_empty_header_pages(
    monkeypatch,
) -> None:
    requests_seen: list[httpx.Request] = []
    eastern = timezone(timedelta(hours=-5))
    first_timestamp = datetime(2026, 11, 4, 18, 0, tzinfo=eastern)
    rows = []
    for minute in range(1800):
        timestamp = first_timestamp + timedelta(minutes=minute)
        rows.append({
            "xymd": timestamp.strftime("%Y%m%d"), "xhms": timestamp.strftime("%H%M%S"),
            "open": "100", "high": "101", "low": "99", "last": "100.5", "evol": "1000",
        })
    chart_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chart_requests
        requests_seen.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        page_index = chart_requests
        chart_requests += 1
        end = len(rows) - page_index * 120
        start = end - 120
        continuation = "" if page_index == 0 else ("D" if page_index == 12 else "N")
        return httpx.Response(200, headers={"tr_cont": continuation},
                              json={"rt_cd": "0", "output2": rows[start:end]})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(client, "_wait_for_rest_slot", lambda: None)
    history = client.get_overseas_minute_session_snapshot("AAPL", "NAS", date(2026, 11, 5))

    chart = [request for request in requests_seen if request.url.path.endswith("inquire-time-itemchartprice")]
    assert history.pages_fetched == 13
    assert history.source_record_count == 1560
    assert len(history.bars) == 390
    assert history.bars[0].timestamp.isoformat() == "2026-11-05T09:30:00-05:00"
    assert history.bars[-1].timestamp.isoformat() == "2026-11-05T15:59:00-05:00"
    assert history.continuation_states == ["EMPTY", *(["N"] * 11), "D"]
    assert chart[1].url.params["NEXT"] == "1"
    assert chart[1].url.params["KEYB"] == "20261105215900"
    assert chart[-1].url.params["PINC"] == "1"
    client.close()


def test_overseas_minute_session_fails_closed_when_pagination_repeats() -> None:
    page = [{"xymd": "20261106", "xhms": "093000", "open": "100", "high": "101",
             "low": "99", "last": "100.5", "evol": "1000"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, headers={"tr_cont": "M"}, json={"rt_cd": "0", "output2": page})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(KisApiError, match="did not move backward"):
        client.get_overseas_minute_session_bars("AAPL", "NAS", date(2026, 11, 6))
    client.close()


def test_overseas_minute_session_page_limit_is_bounded_to_twenty() -> None:
    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("invalid page limit must not make a request")
        )),
    )
    with pytest.raises(ValueError, match="between 1 and 20"):
        client.get_overseas_minute_session_bars("AAPL", "NAS", date(2026, 11, 5), max_pages=21)
    client.close()


def test_overseas_minute_session_rejects_unknown_continuation_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, headers={"tr_cont": "UNKNOWN"}, json={"rt_cd": "0", "output2": []})

    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(KisApiError, match="unsupported continuation state"):
        client.get_overseas_minute_session_bars("AAPL", "NAS", date(2026, 11, 6))
    client.close()


def test_overseas_minute_chart_rejects_unsupported_exchange_before_network() -> None:
    client = KisReadOnlyClient(
        KisSettings(True, "real", "real-key", "real-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("unsupported exchange must not make a request")
        )),
    )
    with pytest.raises(ValueError, match="exchange must be"):
        client.get_overseas_minute_bars("AAPL", "HKS")  # type: ignore[arg-type]
    client.close()


def test_overseas_minute_chart_fails_closed_in_paper_environment() -> None:
    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret"),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail("KIS documents no paper support for this API")
        )),
    )
    with pytest.raises(KisApiError, match="not available in the paper environment"):
        client.get_overseas_minute_bars("AAPL", "NAS")
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
    assert exc.value.broker_code == "EGW00123"
    client.close()


@pytest.mark.parametrize("unsafe_msg_cd", ["EGW00002 token=private", "private-response-code"])
def test_quote_rejection_does_not_echo_unrecognized_broker_code(unsafe_msg_cd: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        return httpx.Response(200, json={
            "rt_cd": "1", "msg_cd": unsafe_msg_cd, "msg1": "private response detail",
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisApiError, match=r"KIS rejected the quote request \(HTTP 200, code unknown") as captured:
        client.get_quote("US", "F", "NAS")

    assert captured.value.broker_code == "unknown"
    assert captured.value.http_status == 200
    assert captured.value.response_content_type == "application/json"
    assert captured.value.response_fields == ("rt_cd", "msg_cd", "msg1")
    assert "fields rt_cd,msg_cd,msg1" in str(captured.value)
    assert "token=private" not in str(captured.value)
    assert "private response detail" not in str(captured.value)
    client.close()


@pytest.mark.parametrize("body", [[], "private response body", None])
def test_quote_rejects_non_object_json_without_echoing_response(body: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json=_token_response())
        if body is None:
            return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})
        return httpx.Response(200, json=body)

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "paper-key", "paper-secret", shared_rate_limit_enabled=False),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(KisApiError, match="was not a JSON object") as captured:
        client.get_quote("US", "F", "NAS")

    assert "private response body" not in str(captured.value)
    assert "paper-secret" not in str(captured.value)
    client.close()


@pytest.mark.parametrize(
    ("market", "environment", "expected_path", "expected_tr", "body"),
    [
        ("KR", "paper", "/uapi/domestic-stock/v1/trading/inquire-balance", "VTTC8434R", {
            "rt_cd": "0", "output1": [{"pdno": "005930", "hldg_qty": "2", "CANO": "do-not-return"}],
            "output2": {"dnca_tot_amt": "100000", "secret_field": "do-not-return"},
        }),
        ("US", "real", "/uapi/overseas-stock/v1/trading/inquire-balance", "TTTS3012R", {
            "rt_cd": "0", "output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "1", "CANO": "do-not-return"}],
            "output2": {"frcr_dncl_amt_2": "50.00", "secret_field": "do-not-return"},
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
                "rt_cd": "0", "output1": [{"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "1"}],
                "output2": {"unverified_field": "50.00"},
                "ctx_area_fk200": "next-fk", "ctx_area_nk200": "next-nk",
            })
        return httpx.Response(200, headers={"tr_cont": "N"}, json={
            "rt_cd": "0", "output1": [{"ovrs_pdno": "MSFT", "ovrs_cblc_qty": "2"}],
            "output2": {"unverified_field": "50.00"},
        })

    client = KisReadOnlyClient(
        KisSettings(True, "paper", "test-key", "test-secret", "1234567801"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    balance = client.get_account_balance("US")

    assert [position["ovrs_pdno"] for position in balance.positions] == ["AAPL", "MSFT"]
    assert "frcr_dncl_amt_2" not in balance.summary
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
