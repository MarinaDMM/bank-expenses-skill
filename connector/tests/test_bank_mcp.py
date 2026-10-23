import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bank_mcp import config, link, ops
from bank_mcp.client import API, BankApiError, EnableBankingClient


# --- helpers ----------------------------------------------------------------


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private, public


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeHttp:
    """Returns queued responses and records every request."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, headers=None, timeout=None, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
        return self.responses.pop(0)


def tx(amount, indicator, booking_date, creditor=None, debtor=None, remittance=None, status="BOOK"):
    return {
        "transaction_amount": {"amount": amount, "currency": "EUR"},
        "credit_debit_indicator": indicator,
        "booking_date": booking_date,
        "creditor": {"name": creditor} if creditor else None,
        "debtor": {"name": debtor} if debtor else None,
        "remittance_information": remittance or [],
        "status": status,
    }


# --- client -----------------------------------------------------------------


def test_token_has_required_header_and_claims(keypair):
    private, public = keypair
    client = EnableBankingClient("app-123", private, clock=lambda: 1_000_000)
    token = client.token()

    header = jwt.get_unverified_header(token)
    assert header["kid"] == "app-123" and header["alg"] == "RS256" and header["typ"] == "JWT"
    claims = jwt.decode(
        token, public, algorithms=["RS256"], audience="api.enablebanking.com", options={"verify_exp": False}
    )
    assert claims["iss"] == "enablebanking.com"
    assert claims["exp"] - claims["iat"] == 3600


def test_requests_carry_bearer_token_and_correct_url(keypair):
    http = FakeHttp(FakeResponse(200, {"balances": [{"name": "x"}]}))
    client = EnableBankingClient("app", keypair[0], http=http)
    assert client.balances("uid-1") == [{"name": "x"}]
    call = http.calls[0]
    assert call["url"] == f"{API}/accounts/uid-1/balances" and call["method"] == "GET"
    assert call["headers"]["Authorization"].startswith("Bearer ")


def test_api_error_includes_status_and_message_but_not_the_token(keypair):
    http = FakeHttp(FakeResponse(401, {"message": "bad signature"}))
    client = EnableBankingClient("app", keypair[0], http=http)
    with pytest.raises(BankApiError) as error:
        client.balances("u")
    assert "401" in str(error.value) and "bad signature" in str(error.value)
    assert "Bearer" not in str(error.value)


def test_client_has_no_payment_methods():
    names = [n for n in dir(EnableBankingClient) if not n.startswith("_")]
    assert not [n for n in names if "pay" in n.lower() or "transfer" in n.lower()]


# --- normalizing and masking -------------------------------------------------


def test_normalize_signs_amounts_and_picks_counterparty():
    out = ops.normalize_transaction(tx("25.50", "DBIT", "2026-10-01", creditor="Albert Heijn"))
    assert out["amount"] == -25.5 and out["counterparty"] == "Albert Heijn"
    inc = ops.normalize_transaction(tx("1000.00", "CRDT", "2026-10-02", debtor="Employer BV"))
    assert inc["amount"] == 1000.0 and inc["counterparty"] == "Employer BV"


def test_ibans_in_text_are_masked():
    text = "Pay to NL00 TEST 0000 0043 00 and NL00TEST0000004300 thanks"
    cleaned = ops.scrub(text)
    assert "TEST" not in cleaned
    assert cleaned.count("[IBAN ...4300]") == 2


def test_description_is_capped():
    assert len(ops.scrub("x" * 500)) == ops.MAX_DESCRIPTION


def test_filter_by_text_and_absolute_amount():
    txs = [
        ops.normalize_transaction(tx("12.00", "DBIT", "2026-10-01", creditor="Gym", remittance=["membership"])),
        ops.normalize_transaction(tx("800.00", "DBIT", "2026-10-01", creditor="Landlord", remittance=["rent"])),
        ops.normalize_transaction(tx("900.00", "CRDT", "2026-10-02", debtor="Employer")),
    ]
    assert [t["counterparty"] for t in ops.filter_transactions(txs, text="RENT")] == ["Landlord"]
    assert len(ops.filter_transactions(txs, min_amount=500)) == 2
    assert [t["counterparty"] for t in ops.filter_transactions(txs, max_amount=100)] == ["Gym"]


# --- pagination and summary --------------------------------------------------


class PagedClient:
    def __init__(self):
        self.keys = []

    def transactions_page(self, uid, date_from, date_to, key=None):
        self.keys.append(key)
        if key is None:
            return {"transactions": [tx("10.00", "DBIT", "2026-09-05", creditor="A")], "continuation_key": "k2"}
        return {"transactions": [tx("20.00", "DBIT", "2026-10-05", creditor="B")], "continuation_key": None}


def test_fetch_follows_continuation_keys_and_sorts_newest_first():
    client = PagedClient()
    result = ops.fetch_transactions(client, "u", "2026-09-01", "2026-10-31")
    assert client.keys == [None, "k2"]
    assert [t["date"] for t in result] == ["2026-10-05", "2026-09-05"]


def test_fetch_stops_at_max_pages():
    class Endless(PagedClient):
        def transactions_page(self, *args, **kwargs):
            self.keys.append(args)
            return {"transactions": [], "continuation_key": "again"}

    client = Endless()
    ops.fetch_transactions(client, "u", "a", "b", max_pages=3)
    assert len(client.keys) == 3


def test_monthly_summary_totals_and_top_counterparties():
    txs = [
        ops.normalize_transaction(tx("1000.00", "CRDT", "2026-10-01", debtor="Employer")),
        ops.normalize_transaction(tx("700.00", "DBIT", "2026-10-02", creditor="Landlord")),
        ops.normalize_transaction(tx("50.00", "DBIT", "2026-10-03", creditor="Shop")),
        ops.normalize_transaction(tx("30.00", "DBIT", "2026-09-03", creditor="Shop")),
    ]
    october, september = ops.monthly_summary(txs, top_n=1)
    assert october == {
        "month": "2026-10",
        "money_in": 1000.0,
        "money_out": 750.0,
        "net": 250.0,
        "top_outgoing": [{"counterparty": "Landlord", "amount": 700.0}],
    }
    assert september["money_out"] == 30.0


# --- config and sessions -----------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_MCP_HOME", str(tmp_path))
    return tmp_path


def test_load_app_reports_each_missing_piece(home):
    with pytest.raises(ValueError, match="app.json"):
        config.load_app()
    (home / "app.json").write_text(json.dumps({"application_id": "a", "redirect_url": "https://x"}))
    with pytest.raises(ValueError, match="private key"):
        config.load_app()
    (home / "private.pem").write_text("KEY")
    assert config.load_app() == {"application_id": "a", "redirect_url": "https://x", "private_key": "KEY"}


def test_session_files_are_private(home):
    path = config.save_session("ing", {"valid_until": "2030-01-01T00:00:00+00:00", "accounts": []})
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(config.sessions_dir().stat().st_mode & 0o777) == "0o700"
    assert config.list_labels() == ["ing"]


def test_unsafe_labels_are_rejected(home):
    with pytest.raises(ValueError):
        config.save_session("../evil", {})


def test_days_left_counts_down_and_goes_negative():
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    soon = {"valid_until": (now + timedelta(days=10, hours=1)).isoformat()}
    gone = {"valid_until": (now - timedelta(days=2)).isoformat()}
    assert config.days_left(soon, now) == 10
    assert config.days_left(gone, now) < 0


# --- linking -----------------------------------------------------------------


def test_extract_code_checks_state():
    url = "https://example.com/done?code=ABC123&state=s1"
    assert link.extract_code(url, "s1") == "ABC123"
    with pytest.raises(ValueError, match="state mismatch"):
        link.extract_code(url, "other")


def test_extract_code_reports_bank_errors_and_missing_code():
    with pytest.raises(ValueError, match="access_denied"):
        link.extract_code("https://x/?error=access_denied&state=s", "s")
    with pytest.raises(ValueError, match="no 'code'"):
        link.extract_code("https://x/?state=s", "s")


def test_session_record_keeps_only_last_four_of_iban():
    session = {
        "session_id": "sess",
        "accounts": [{"uid": "u1", "name": "Current", "currency": "EUR", "account_id": {"iban": "NL00 TEST 0000 0043 00"}}],
    }
    record = link.session_record("abn", "ABN AMRO", "NL", session, "2027-01-01T00:00:00+00:00")
    assert record["accounts"][0]["iban_last4"] == "4300"
    assert "NL00" not in json.dumps(record) and "0000 0043" not in json.dumps(record)


def test_bank_error_includes_its_description():
    url = "https://x/?state=s&error=invalid_request&error_description=valid_until+is+too+long"
    with pytest.raises(ValueError, match="invalid_request valid_until is too long"):
        link.extract_code(url, "s")


def test_network_timeouts_are_retried_then_reported_without_secrets(keypair):
    import requests

    class Flaky:
        def __init__(self, failures):
            self.failures, self.calls = failures, 0

        def request(self, method, url, **kwargs):
            self.calls += 1
            if self.calls <= self.failures:
                raise requests.Timeout("slow")
            return FakeResponse(200, {"balances": []})

    waited = []
    ok = EnableBankingClient("app", keypair[0], http=Flaky(2), sleep=waited.append)
    assert ok.balances("u") == [] and waited == [2, 4]

    dead = EnableBankingClient("app", keypair[0], http=Flaky(99), sleep=lambda s: None)
    with pytest.raises(BankApiError, match="after 4 tries") as error:
        dead.balances("u")
    assert "Bearer" not in str(error.value)
