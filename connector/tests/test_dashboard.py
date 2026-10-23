import json
from datetime import date

import pytest

from bank_mcp import categories, config, dashboard

OWN = {"1111", "2222", "3333", "4444"}


@pytest.fixture(autouse=True)
def private_rules(tmp_path, monkeypatch):
    """Every test gets its own empty config home, so it never reads the real private rules file."""
    monkeypatch.setenv("BANK_MCP_HOME", str(tmp_path))
    categories.reload()
    yield tmp_path
    categories.reload()


def write_private(home, rules=(), internal_names=()):
    (home / "categories.json").write_text(json.dumps({"rules": list(rules), "internal_names": list(internal_names)}))
    categories.reload()


def tx(amount, counterparty="", description="", month="2026-09", day="10", status="BOOK"):
    return {"date": f"{month}-{day}", "amount": amount, "currency": "EUR",
            "counterparty": counterparty, "description": description, "status": status}


# --- built-in rules ----------------------------------------------------------


def test_generic_rules_cover_common_dutch_providers():
    assert categories.categorize(tx(-1500, "Obvion N.V."))[0] == "Mortgage"
    assert categories.categorize(tx(-185, "Vandebron Energie B.V."))[0] == "Energy"
    assert categories.categorize(tx(-3000, "Belastingdienst", "aanslag"))[0] == "Tax office"
    assert categories.categorize(tx(-60, "Lidl", "BEA, Apple Pay"))[0] == "Groceries"
    assert categories.categorize(tx(-20, "Unknown shop")) == ("Uncategorised", "household")
    assert categories.categorize(tx(-100, "Hotel at Booking.com"))[0] == "Travel & holidays"
    assert categories.categorize(tx(-900, "Apple Store via Worldpay"))[0] == "Electronics & Apple"


def test_person_transfers_are_payments_to_people_but_merchants_are_not():
    assert categories.categorize(tx(-300, "Jan de Vries", "Naam: Jan de Vries IBAN"))[0] == "Payments to people"
    assert categories.categorize(tx(-300, "Acme Widgets B.V.", "factuur"))[0] == "Uncategorised"
    assert categories.categorize(tx(-300, "Jan de Vries", "Incasso machtiging 1"))[0] == "Uncategorised"


# --- private rules ------------------------------------------------------------


def test_private_rules_are_checked_before_generic_ones(private_rules):
    write_private(private_rules, [
        ["Studio rent", "business", "studio rent"],
        ["Business mobile", "business", "55500011"],
    ])
    assert categories.categorize(tx(-249.12, "Someone via Tikkie", "Studio rent")) == ("Studio rent", "business")
    assert categories.categorize(tx(-30, "KPN - Mobiel", "klantnummer 55500011")) == ("Business mobile", "business")
    assert categories.categorize(tx(-30, "KPN B.V.", "klantnummer 123"))[0] == "Internet & mobile"


def test_bad_private_files_fail_with_a_clear_message(private_rules):
    (private_rules / "categories.json").write_text(json.dumps({"rules": [["Only", "two"]]}))
    categories.reload()
    with pytest.raises(ValueError, match="rule 1 must be"):
        categories.load_user_file()
    (private_rules / "categories.json").write_text(json.dumps({"rules": [["X", "personal", "a"]]}))
    with pytest.raises(ValueError, match="scope must be"):
        categories.load_user_file()
    (private_rules / "categories.json").write_text(json.dumps({"rules": [["X", "business", "(unclosed"]]}))
    with pytest.raises(ValueError, match="invalid regex"):
        categories.load_user_file()


def test_internal_transfers_use_account_tails_and_private_names(private_rules):
    assert categories.is_internal(tx(-500, "Anyone", "IBAN: [IBAN ...2222]"), OWN)
    assert not categories.is_internal(tx(-800, "JOHN SMITH"), OWN)  # no private names configured yet
    write_private(private_rules, internal_names=["smith"])
    assert categories.is_internal(tx(-800, "JOHN SMITH"), OWN)
    assert not categories.is_internal(tx(-30, "Albert Heijn"), OWN)
    # a Tikkie to a relative is a real payment, not an internal move
    assert not categories.is_internal(tx(-44, "Alice Smith via Tikkie", "Tikkie"), OWN)


# --- expenses -----------------------------------------------------------------


def test_pending_and_incoming_are_ignored_but_shop_refunds_reduce_spending():
    assert categories.expense_of(tx(-10, "Lidl", status="PDNG"), OWN) is None
    assert categories.expense_of(tx(1500, "Employer"), OWN) is None
    assert categories.expense_of(tx(199.99, "BOLCOM BV", "Artikel geretourneerd"), OWN) == ("Online shopping", "household", -199.99)


def test_incoming_tikkie_reduces_payments_to_people():
    paid = categories.expense_of(tx(-500, "Someone via Tikkie", "Tikkie"), OWN)
    back = categories.expense_of(tx(500, "AAB INZ TIKKIE", "Tikkie ID 1, Van A"), OWN)
    assert paid == ("Payments to people", "household", 500)
    assert back == ("Payments to people", "household", -500)


def test_customer_payments_are_not_refunds(private_rules):
    write_private(private_rules, [["Business supplies", "business", "pottery"]])
    assert categories.expense_of(tx(833, "Acme Pottery", "INVOICE 001"), OWN) is None


# --- aggregation --------------------------------------------------------------


def test_aggregate_sums_by_month_account_and_category(private_rules):
    write_private(private_rules, internal_names=["smith"])
    accounts = [{"id": "ing/1111"}, {"id": "abn/2222"}]
    txs = {
        "ing/1111": [tx(-100, "Lidl"), tx(-50, "Lidl", day="12"), tx(-500, "Smith FS", "[IBAN ...2222]"), tx(-70, "Lidl", month="2026-10")],
        "abn/2222": [tx(-30, "Lidl")],
    }
    agg = dashboard.aggregate(accounts, txs, OWN)
    groceries = {(r["month"], r["account"]): r["amount"] for r in agg["data"] if r["category"] == "Groceries"}
    assert groceries == {("2026-09", "ing/1111"): 150.0, ("2026-09", "abn/2222"): 30.0, ("2026-10", "ing/1111"): 70.0}
    assert agg["internal_transfers"] == {"2026-09": 500.0}
    assert agg["top"]["2026-09"][0] == {"category": "Groceries", "scope": "household", "name": "Lidl", "amount": 180.0}


def test_refunds_cannot_make_a_month_negative():
    accounts = [{"id": "a/1"}]
    txs = {"a/1": [tx(-100, "bol.com"), tx(250, "bol.com", "refund")]}
    assert dashboard.aggregate(accounts, txs, OWN)["data"] == []


def test_recurring_needs_four_of_six_months_with_similar_amounts():
    accounts = [{"id": "a/1"}]
    months = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    monthly = [tx(-69, "Sportschool Fit B.V.", month=m, day="07") for m in months]
    sparse = [tx(-30, "Rare Shop", month=m) for m in months[:2]]
    changing = [tx(-v, "Varying", month=m) for v, m in zip([10, 55, 120, 300, 20, 80], months)]
    rec = dashboard.find_recurring(accounts, {"a/1": monthly + sparse + changing}, OWN, date(2026, 10, 23))
    assert [r["name"] for r in rec] == ["Sportschool Fit B.V."]
    assert rec[0]["amount"] == 69.0 and rec[0]["day"] == 7 and rec[0]["months_seen"] == 6


def test_payee_names_hide_card_and_account_numbers():
    card = tx(-844.32, "", "Incasso ING creditcard Accountnr 210030063338 Periode september")
    assert dashboard.clean_name(card) == "ING credit card (statement)"
    shop = tx(-60, "", "BEA, Apple Pay BCK*Main Street Superm,PAS752 NR:CT964293, 12.10.26/17:11 UTRECHT")
    assert dashboard.clean_name(shop) == "BCK*Main Street Superm"


# --- building and rendering ------------------------------------------------------


def test_render_embeds_data_and_cannot_break_out_of_the_script_tag():
    html = dashboard.render({"x": "</script><b>"})
    assert "__DATA__" not in html and "</script><b>" not in html
    assert '<script id="data" type="application/json">' in html


def test_fetch_falls_back_to_shorter_history_when_bank_refuses():
    from bank_mcp.client import BankApiError

    class Picky:
        def transactions_page(self, uid, date_from, date_to, key=None):
            if date_from < "2026-03-01":
                raise BankApiError("too far back")
            return {"transactions": [], "continuation_key": None}

    txs, window = dashboard.fetch_account(Picky(), "u", date(2026, 10, 23), 365)
    assert window == 180 and txs == []


class FakeBank:
    """Two linked accounts: 'shared' (1111) and 'mine' (2222)."""

    def transactions_page(self, uid, date_from, date_to, key=None):
        raw = {
            "u-shared": [
                {"transaction_amount": {"amount": "100.00", "currency": "EUR"}, "credit_debit_indicator": "DBIT",
                 "booking_date": "2026-09-05", "creditor": {"name": "Lidl"}, "remittance_information": [], "status": "BOOK"},
                {"transaction_amount": {"amount": "500.00", "currency": "EUR"}, "credit_debit_indicator": "DBIT",
                 "booking_date": "2026-09-06", "creditor": {"name": "Me"}, "remittance_information": ["IBAN NL00TEST0000002222"],
                 "creditor_account": {}, "status": "BOOK"},
            ],
            "u-mine": [
                {"transaction_amount": {"amount": "40.00", "currency": "EUR"}, "credit_debit_indicator": "DBIT",
                 "booking_date": "2026-09-07", "creditor": {"name": "Lidl"}, "remittance_information": [], "status": "BOOK"},
            ],
        }[uid]
        return {"transactions": raw, "continuation_key": None}


def link_accounts(home):
    config.save_session("shared", {"aspsp": {"name": "Bank A"}, "valid_until": "2099-01-01T00:00:00+00:00",
                                   "accounts": [{"uid": "u-shared", "name": "Joint", "currency": "EUR", "iban_last4": "1111"}]})
    config.save_session("mine", {"aspsp": {"name": "Bank B"}, "valid_until": "2099-01-01T00:00:00+00:00",
                                 "accounts": [{"uid": "u-mine", "name": "Personal", "currency": "EUR", "iban_last4": "2222"}]})


def test_excluding_an_account_removes_it_but_transfers_to_it_stay_internal(private_rules):
    link_accounts(private_rules)
    everyone = dashboard.build(client=FakeBank(), today=date(2026, 10, 23))
    shared_only = dashboard.build(client=FakeBank(), today=date(2026, 10, 23), exclude={"mine"}, title="Shared")

    assert [a["id"] for a in everyone["accounts"]] == ["mine/2222", "shared/1111"]
    assert [a["id"] for a in shared_only["accounts"]] == ["shared/1111"]
    assert shared_only["title"] == "Shared"
    total = lambda p: sum(r["amount"] for r in p["data"])
    assert total(everyone) == 140.0 and total(shared_only) == 100.0
    # the 500 sent to the excluded account's number is an internal move, not spending
    assert shared_only["internal_transfers"] == {"2026-09": 500.0}


def test_excluding_a_single_account_id(private_rules):
    link_accounts(private_rules)
    payload = dashboard.build(client=FakeBank(), today=date(2026, 10, 23), exclude={"shared/1111"})
    assert [a["id"] for a in payload["accounts"]] == ["mine/2222"]


def test_the_requested_window_is_tried_before_the_preset_fallbacks():
    seen = []

    class Records:
        def transactions_page(self, uid, date_from, date_to, key=None):
            seen.append(date_from)
            return {"transactions": [], "continuation_key": None}

    _, window = dashboard.fetch_account(Records(), "u", date(2026, 10, 23), 217)
    assert window == 217 and seen == ["2026-03-20"]
