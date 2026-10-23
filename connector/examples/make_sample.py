"""Build example dashboards from INVENTED data, for screenshots and demos. No bank, no real data.

Run from the connector folder:  python examples/make_sample.py [--out DIR]
Writes sample-spending.html and sample-income-vs-expenses.html. The numbers are generated with a fixed seed.
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bank_mcp import cashflow, dashboard  # noqa: E402

TODAY = "2026-10-23"
CURRENT = "2026-10"
MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
          "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
ACCOUNTS = [
    {"id": "bank-a/1111", "bank": "Bank A", "holder": "Sample Household", "last4": "1111"},
    {"id": "bank-b/2222", "bank": "Bank B", "holder": "Sample Person", "last4": "2222"},
]

# category: (scope, account, typical monthly amount, noise, payees as (name, share))
CATEGORIES = {
    "Mortgage": ("household", "bank-a/1111", 1450, 0, [("Home Loan Bank", 1.0)]),
    "Energy": ("household", "bank-a/1111", 140, 0.08, [("Greenvolt Energy", 1.0)]),
    "Water": ("household", "bank-a/1111", 45, 0.05, [("Aqua Utility", 1.0)]),
    "Health insurance": ("household", "bank-a/1111", 310, 0, [("Care Insure", 0.6), ("Wellness Cover", 0.4)]),
    "Other insurance": ("household", "bank-a/1111", 95, 0, [("Safe Home Insurance", 1.0)]),
    "Internet & mobile": ("household", "bank-a/1111", 105, 0.05, [("Telco One", 1.0)]),
    "Groceries": ("household", "bank-a/1111", 690, 0.12, [("Fresh Market", 0.55), ("Daily Mart", 0.35), ("Corner Bakery", 0.1)]),
    "Credit card payments": ("household", "bank-a/1111", 1150, 0.3, [("Credit card (statement)", 0.75), ("Extra card payment", 0.25)]),
    "Eating out & leisure": ("household", "bank-b/2222", 190, 0.35, [("Noodle House", 0.4), ("Cinema City", 0.3), ("Cafe Roma", 0.3)]),
    "Online shopping": ("household", "bank-b/2222", 210, 0.5, [("Shopsy", 0.5), ("Fashion Hub", 0.3), ("Marketplace", 0.2)]),
    "Sports & clubs": ("household", "bank-b/2222", 115, 0.1, [("FitLife Gym", 0.6), ("Padel Club", 0.4)]),
    "Transport": ("household", "bank-b/2222", 90, 0.3, [("City Transit", 0.7), ("Parking App", 0.3)]),
    "Health & drugstore": ("household", "bank-b/2222", 70, 0.5, [("Pharmacy Plus", 1.0)]),
    "Payments to people": ("household", "bank-b/2222", 95, 0.6, [("Friend via Tikkie", 1.0)]),
    "Uncategorised": ("household", "bank-b/2222", 180, 0.5, [("Misc shop", 1.0)]),
    "Studio rent": ("business", "bank-b/2222", 220, 0, [("Studio Landlord", 1.0)]),
    "Accountant": ("business", "bank-b/2222", 55, 0, [("Numbers & Co", 1.0)]),
    "Advertising": ("business", "bank-b/2222", 85, 0.5, [("Social Ads", 1.0)]),
    "Shop & online tools": ("business", "bank-b/2222", 75, 0.1, [("Web Shop Platform", 0.7), ("Domain Registrar", 0.3)]),
    "Business supplies": ("business", "bank-b/2222", 110, 0.8, [("Craft Supplies Ltd", 1.0)]),
}
RECURRING = [  # name, category, amount, typical day
    ("Home Loan Bank", "Mortgage", 1450.00, 28), ("Care Insure", "Health insurance", 186.00, 27),
    ("Wellness Cover", "Health insurance", 124.00, 27), ("Greenvolt Energy", "Energy", 140.00, 26),
    ("Safe Home Insurance", "Other insurance", 95.00, 4), ("Telco One", "Internet & mobile", 105.00, 9),
    ("FitLife Gym", "Sports & clubs", 69.00, 7), ("Studio Landlord", "Studio rent", 220.00, 1),
    ("Numbers & Co", "Accountant", 55.00, 14), ("Web Shop Platform", "Shop & online tools", 28.00, 3),
]


def spending_rows(rng: random.Random) -> list[dict]:
    rows = []
    for month in MONTHS:
        partial = month == CURRENT
        for category, (scope, account, base, noise, _) in CATEGORIES.items():
            if category == "Mortgage" and partial:
                continue  # not debited yet this month
            amount = base * (1 + rng.uniform(-noise, noise))
            if month == "2025-12" and category in ("Online shopping", "Eating out & leisure"):
                amount *= 1.9  # holiday month
            if partial:
                amount *= 0.45
            rows.append({"month": month, "account": account, "category": category, "scope": scope, "amount": round(amount, 2)})
        for category, amount in (("Local taxes", 125.0), ) if "2026-04" <= month <= "2026-07" else ():
            rows.append({"month": month, "account": "bank-a/1111", "category": category, "scope": "household", "amount": amount})
        if month == "2026-08":
            rows.append({"month": month, "account": "bank-a/1111", "category": "Tax office", "scope": "household", "amount": 2400.0})
        if month == "2025-11":
            rows.append({"month": month, "account": "bank-b/2222", "category": "Electronics & Apple", "scope": "household", "amount": 1150.0})
    return rows


def top_payees(rows: list[dict]) -> dict:
    top = {}
    for month in MONTHS:
        entries = []
        for row in (r for r in rows if r["month"] == month):
            payees = CATEGORIES.get(row["category"], (None, None, None, None, [("Various", 1.0)]))[4]
            for name, share in payees:
                entries.append({"category": row["category"], "scope": row["scope"], "name": name, "amount": round(row["amount"] * share, 2)})
        top[month] = sorted(entries, key=lambda e: e["amount"], reverse=True)[:25]
    return top


def spending_payload(seed: int = 42) -> dict:
    rows = spending_rows(random.Random(seed))
    return {
        "title": "Monthly expenses (sample data)", "generated": TODAY + "T09:00:00+00:00", "today": TODAY,
        "current_month": CURRENT, "currency": "EUR", "months": MONTHS, "accounts": ACCOUNTS,
        "coverage_days": {a["id"]: 372 for a in ACCOUNTS}, "data": rows, "top": top_payees(rows),
        "internal_transfers": {m: 800.0 for m in MONTHS[:-1]},
        "recurring": [{"name": n, "category": c, "scope": CATEGORIES[c][0], "amount": a, "day": d, "months_seen": 6}
                      for n, c, a, d in sorted(RECURRING, key=lambda r: -r[2])],
    }


def cashflow_payload(seed: int = 42) -> dict:
    rows = spending_rows(random.Random(seed))
    window = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    rng = random.Random(seed + 1)
    expenses = {m: {"household": 0.0, "business": 0.0} for m in window}
    for row in rows:
        if row["month"] in expenses:
            expenses[row["month"]][row["scope"]] += row["amount"]
    income = []
    for month in window:
        if month != CURRENT:
            income.append({"month": month, "label": "Salary & employer", "kind": "earned", "amount": 4650.0})
            income.append({"month": month, "label": "Customers", "kind": "earned", "amount": round(rng.uniform(250, 900), 2)})
            income.append({"month": month, "label": "Transfers from partner", "kind": "transfer", "amount": 1200.0})
            income.append({"month": month, "label": "Transfers from your other accounts", "kind": "transfer", "amount": round(rng.uniform(200, 500), 2)})
    income.append({"month": "2026-07", "label": "Tax refunds", "kind": "earned", "amount": 1850.0})
    for month in ("2026-07", CURRENT):
        income.append({"month": month, "label": "Child benefit & allowances", "kind": "earned", "amount": 415.0})
    income.append({"month": CURRENT, "label": "Customers", "kind": "earned", "amount": 160.0})
    return {
        "title": "Income vs expenses (sample data)", "generated": TODAY + "T09:00:00+00:00", "today": TODAY,
        "current_month": CURRENT, "currency": "EUR", "months": window, "accounts": ACCOUNTS,
        "history_from": {a["id"]: "2026-03-20" for a in ACCOUNTS}, "income": income,
        "expenses": {m: {s: round(v, 2) for s, v in d.items()} for m, d in expenses.items()},
        "expense_rows": [], "transfers_out": {m: round(rng.uniform(100, 250), 2) for m in window},
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "output")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "sample-spending.html").write_text(dashboard.render(spending_payload()))
    (args.out / "sample-income-vs-expenses.html").write_text(cashflow.render(cashflow_payload()))
    print("wrote", args.out / "sample-spending.html", "and", args.out / "sample-income-vs-expenses.html")


if __name__ == "__main__":
    main()
