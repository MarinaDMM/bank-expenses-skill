import json
from datetime import date

import pytest

from bank_mcp import cashflow, categories, config

INCLUDED = {"1111", "2222"}
OTHER = {"3333"}  # a linked account that is left out of the view


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BANK_MCP_HOME", str(tmp_path))
    categories.reload()
    yield tmp_path
    categories.reload()


def tx(amount, counterparty="", description="", month="2026-09", day="10", status="BOOK"):
    return {"date": f"{month}-{day}", "amount": amount, "currency": "EUR",
            "counterparty": counterparty, "description": description, "status": status}


def classify(t, rules=()):
    return cashflow.classify_inflow(t, INCLUDED, OTHER, rules)


# --- classifying money in --------------------------------------------------------


def test_generic_income_kinds():
    assert classify(tx(426, "Sociale Verzekeringsbank", "KINDERBIJSLAG")) == ("Child benefit & allowances", "earned")
    assert classify(tx(900, "Belastingdienst", "TERUGGAAF")) == ("Tax refunds", "earned")
    assert classify(tx(3000, "Acme Corp", "Salaris september")) == ("Salary & employer", "earned")
    assert classify(tx(120, "SumUp Limited")) == ("Platform payouts", "earned")


def test_moves_inside_the_view_and_pending_or_outgoing_are_not_income():
    assert classify(tx(500, "Me", "IBAN: [IBAN ...2222]")) is None
    assert classify(tx(500, "Employer", status="PDNG")) is None
    assert classify(tx(-50, "Acme Corp")) is None


def test_refunds_and_tikkie_reimbursements_are_not_income():
    assert classify(tx(199.99, "BOLCOM BV", "Artikel geretourneerd")) is None
    assert classify(tx(500, "AAB INZ TIKKIE", "Tikkie ID 1, Van A")) is None


def test_transfer_from_an_excluded_linked_account_is_a_transfer():
    assert classify(tx(1000, "Me", "SEPA Overboeking IBAN: [IBAN ...3333]")) == (cashflow.TRANSFER_IN_OTHER, "transfer")


def test_a_name_in_the_description_does_not_trigger_a_private_name_rule():
    rules = [("Transfers from Partner", "transfer", "smith")]
    refund = tx(10795, "Belastingdienst", "TERUGGAAF IB/PVV 2025 (SMITH )")
    assert classify(refund, rules) == ("Tax refunds", "earned")
    assert classify(tx(800, "JOHN SMITH", "monthly"), rules) == ("Transfers from Partner", "transfer")


def test_private_income_rules_come_first_and_people_fall_back():
    rules = [("Transfers from Partner", "transfer", "smith")]
    assert classify(tx(800, "JOHN SMITH"), rules) == ("Transfers from Partner", "transfer")
    assert classify(tx(180, "Jan de Vries", "Firing")) == ("Received from people", "earned")
    assert classify(tx(77, "Mystery B.V.")) == ("Other income", "earned")


def test_private_income_rules_are_validated(isolated_home):
    (isolated_home / "categories.json").write_text(json.dumps({"income_rules": [["X", "salary", "a"]]}))
    with pytest.raises(ValueError, match="kind must be"):
        categories.load_income_rules()
    (isolated_home / "categories.json").write_text(json.dumps({"income_rules": [["X", "earned"]]}))
    with pytest.raises(ValueError, match="must be"):
        categories.load_income_rules()


def test_transfers_out_only_for_money_leaving_the_view():
    assert cashflow.is_transfer_out(tx(-400, "Me", "IBAN: [IBAN ...3333]"), INCLUDED, INCLUDED | OTHER)
    assert not cashflow.is_transfer_out(tx(-400, "Me", "IBAN: [IBAN ...2222]"), INCLUDED, INCLUDED | OTHER)
    assert not cashflow.is_transfer_out(tx(-40, "Lidl"), INCLUDED, INCLUDED | OTHER)


def test_month_window_is_full_months_plus_current():
    assert cashflow.month_window(date(2026, 10, 23), 6) == [
        "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert cashflow.month_window(date(2026, 2, 5), 3) == ["2025-11", "2025-12", "2026-01", "2026-02"]


# --- build and render -------------------------------------------------------------


class FakeBank:
    def transactions_page(self, uid, date_from, date_to, key=None):
        def raw(amount, name, day, note="", month="2026-09"):
            return {"transaction_amount": {"amount": f"{abs(amount):.2f}", "currency": "EUR"},
                    "credit_debit_indicator": "CRDT" if amount > 0 else "DBIT", "booking_date": f"{month}-{day}",
                    ("debtor" if amount > 0 else "creditor"): {"name": name}, "remittance_information": [note], "status": "BOOK"}
        data = {
            "u-shared": [raw(3000, "Acme Corp", "01", "Salaris"), raw(800, "Me", "02", "IBAN NL00TEST0000003333"),
                         raw(-1500, "Obvion N.V.", "28"), raw(-400, "Me", "15", "IBAN NL00TEST0000003333"),
                         raw(-600, "Me", "20", "IBAN NL00TEST0000002222")],
            "u-second": [raw(600, "Me", "20", "IBAN NL00TEST0000001111"), raw(-50, "Lidl", "21")],
            "u-mine": [raw(-999, "Somewhere", "05")],
        }[uid]
        return {"transactions": data, "continuation_key": None}


def link(home):
    for label, uid, last4, name in (("shared", "u-shared", "1111", "Joint"), ("second", "u-second", "2222", "Second"), ("mine", "u-mine", "3333", "Personal")):
        config.save_session(label, {"aspsp": {"name": "Bank"}, "valid_until": "2099-01-01T00:00:00+00:00",
                                    "accounts": [{"uid": uid, "name": name, "currency": "EUR", "iban_last4": last4}]})


def test_build_separates_earned_income_transfers_and_expenses(isolated_home):
    link(isolated_home)
    (isolated_home / "categories.json").write_text(json.dumps({"internal_names": []}))
    categories.reload()
    p = cashflow.build(months=1, today=date(2026, 10, 5), client=FakeBank(), exclude={"mine"})
    assert p["months"] == ["2026-09", "2026-10"]
    assert [a["id"] for a in p["accounts"]] == ["second/2222", "shared/1111"]
    income = {(r["label"], r["kind"]): r["amount"] for r in p["income"] if r["month"] == "2026-09"}
    # 3000 salary is earned; 800 from the excluded account is a transfer; the 600 between the two included accounts is neither
    assert income == {("Salary & employer", "earned"): 3000.0, (cashflow.TRANSFER_IN_OTHER, "transfer"): 800.0}
    assert p["expenses"]["2026-09"] == {"household": 1550.0, "business": 0.0}
    assert p["transfers_out"] == {"2026-09": 400.0}


def test_render_embeds_data_safely():
    html = cashflow.render({"x": "</script>"})
    assert "__DATA__" not in html and "</script>\"" not in html
