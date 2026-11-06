from datetime import datetime, timedelta, timezone

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
    client.close()


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
    assert request.url.params["FID_PW_DATA_INCU_YN"] == "N"
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

    bars = client.get_domestic_minute_session_bars("005930", date(2026, 11, 5))

    assert [request.url.params["FID_INPUT_HOUR_1"] for request in requests_seen] == list(pages)
    assert [bar.timestamp.strftime("%H%M%S") for bar in bars] == [
        "090000", "092000", "092100", "112000", "112100", "132000", "132100",
    ]
    assert all(bar.spread_bps is None for bar in bars)
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
    from datetime import date

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
