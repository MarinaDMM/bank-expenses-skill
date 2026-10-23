import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("make_sample", Path(__file__).resolve().parent.parent / "examples" / "make_sample.py")
sample = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sample)


def test_sample_data_is_deterministic():
    assert sample.spending_payload() == sample.spending_payload()
    assert sample.cashflow_payload() == sample.cashflow_payload()


def test_spending_sample_is_consistent():
    p = sample.spending_payload()
    ids = {a["id"] for a in p["accounts"]}
    assert {r["account"] for r in p["data"]} <= ids
    assert set(p["months"]) == {r["month"] for r in p["data"]}
    assert all(r["amount"] > 0 for r in p["data"])
    assert not any(r["category"] == "Mortgage" and r["month"] == p["current_month"] for r in p["data"])
    assert p["recurring"] == sorted(p["recurring"], key=lambda r: -r["amount"])


def test_cashflow_sample_matches_the_spending_sample_and_shows_both_kinds_of_income():
    spend, flow = sample.spending_payload(), sample.cashflow_payload()
    for month in flow["months"]:
        total = sum(r["amount"] for r in spend["data"] if r["month"] == month)
        assert round(sum(flow["expenses"][month].values()), 2) == round(total, 2)
    assert {r["kind"] for r in flow["income"]} == {"earned", "transfer"}


def test_sample_pages_render_without_placeholders_or_real_data():
    for html in (sample.dashboard.render(sample.spending_payload()), sample.cashflow.render(sample.cashflow_payload())):
        assert "__DATA__" not in html and "sample data" in html
