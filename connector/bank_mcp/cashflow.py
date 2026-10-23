"""Income versus expenses per month for the accounts you choose, as one offline HTML file.

Run: python -m bank_mcp.cashflow [--months 6] [--exclude LABEL ...] [--title TEXT] [--out PATH] [--open]

Income is split in two, because they mean different things:
  earned    benefits, tax refunds, salary, customer and platform payouts, money from people
  transfer  money moved in from accounts outside this view (your other accounts, a partner's accounts)
Moves between the accounts in the view, shop refunds and Tikkie reimbursements are not income.
The file contains your financial data: it is written with mode 600 into a git-ignored folder.
"""

import argparse
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bank_mcp import categories, dashboard

TEMPLATE = Path(__file__).with_name("cashflow_template.html")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "output" / "cashflow.html"

TRANSFER_IN_OTHER = "Transfers from your other accounts"
TRANSFER_OUT = "Transfers out (family and other accounts)"

# (label, kind, regex); checked after the private income rules.
GENERIC_INCOME: list[tuple[str, str, str]] = [
    ("Child benefit & allowances", "earned", r"sociale verzekeringsbank|\bsvb\b|kinderbijslag|toeslag|\buwv\b"),
    ("Tax refunds", "earned", r"belastingdienst"),
    ("Salary & employer", "earned", r"salaris|\bloon\b|salary|payroll"),
    ("Platform payouts", "earned", r"etsy|sumup|shopify|stripe|paypal|mollie|adyen"),
]


def tails_in(tx: dict) -> set[str]:
    return set(categories.IBAN_TAIL.findall(f"{tx['counterparty']} {tx['description']}"))


def classify_inflow(tx: dict, included: set[str], other_linked: set[str], user_rules) -> tuple[str, str] | None:
    """(label, kind) for money coming in, or None if it is not income (internal move, refund, reimbursement)."""
    if tx["amount"] <= 0 or (tx["status"] and tx["status"] != "BOOK"):
        return None
    tails = tails_in(tx)
    if tails & included:
        return None  # a move between the accounts in this view
    text = f"{tx['counterparty']} {tx['description']}".lower()
    if "tikkie" in text and categories.categorize(tx)[0] == "Payments to people":
        return None  # reimbursement, already netted against spending
    if tails & other_linked:
        return TRANSFER_IN_OTHER, "transfer"
    payer = tx["counterparty"].lower()
    for label, kind, pattern in user_rules:  # your own rules look at who paid, not at the description
        if re.search(pattern, payer):
            return label, kind
    for label, kind, pattern in GENERIC_INCOME:
        if re.search(pattern, text):
            return label, kind
    if categories.categorize(tx)[0] in categories.REFUND_CATEGORIES:
        return None  # a shop refund, already netted against spending
    if categories.looks_like_person({**tx, "amount": -1}):
        return "Received from people", "earned"
    return "Other income", "earned"


def is_transfer_out(tx: dict, included: set[str], linked: set[str]) -> bool:
    """Money leaving this view for your other accounts or family (not spending, not a move inside the view)."""
    if tx["amount"] >= 0 or (tx["status"] and tx["status"] != "BOOK"):
        return False
    if tails_in(tx) & included:
        return False
    return categories.is_internal(tx, linked)


def month_window(today: date, months: int) -> list[str]:
    """The last `months` full months plus the current (unfinished) one, oldest first."""
    first = today.replace(day=1)
    out = [first.isoformat()[:7]]
    cursor = first
    for _ in range(months):
        cursor = (cursor - timedelta(days=1)).replace(day=1)
        out.append(cursor.isoformat()[:7])
    return sorted(out)


def build(months: int = 6, today: date | None = None, client=None, exclude=frozenset(), title: str = "Income vs expenses") -> dict:
    today = today or date.today()
    window = month_window(today, months)
    loaded = dashboard.load_accounts(dashboard.make_client(client), (months + 1) * 31, today, exclude)
    accounts, txs = loaded["accounts"], loaded["txs"]
    included = {a["last4"] for a in accounts}
    linked, other_linked = loaded["linked_last4"], loaded["excluded_last4"]
    user_rules = categories.load_income_rules()

    income: dict[tuple, float] = defaultdict(float)
    transfers_out: dict[str, float] = defaultdict(float)
    for account in accounts:
        for tx in txs[account["id"]]:
            month = tx["date"][:7]
            if month not in window:
                continue
            result = classify_inflow(tx, included, other_linked, user_rules)
            if result:
                income[(month, result[0], result[1])] += tx["amount"]
            elif is_transfer_out(tx, included, linked):
                transfers_out[month] += -tx["amount"]

    agg = dashboard.aggregate(accounts, txs, linked)
    expenses: dict[str, dict[str, float]] = {m: {"household": 0.0, "business": 0.0} for m in window}
    expense_rows: dict[tuple, float] = defaultdict(float)
    for row in agg["data"]:
        if row["month"] in expenses:
            expenses[row["month"]][row["scope"]] += row["amount"]
            expense_rows[(row["month"], row["category"], row["scope"])] += row["amount"]

    return {
        "title": title,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "current_month": today.isoformat()[:7],
        "currency": "EUR",
        "months": window,
        "accounts": accounts,
        "history_from": {a["id"]: min((t["date"] for t in txs[a["id"]]), default="") for a in accounts},
        "income": [{"month": m, "label": l, "kind": k, "amount": round(v, 2)} for (m, l, k), v in sorted(income.items())],
        "expenses": {m: {s: round(v, 2) for s, v in d.items()} for m, d in expenses.items()},
        "expense_rows": [{"month": m, "category": c, "scope": s, "amount": round(v, 2)} for (m, c, s), v in sorted(expense_rows.items())],
        "transfers_out": {m: round(v, 2) for m, v in transfers_out.items()},
    }


def render(payload: dict) -> str:
    import json

    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("__DATA__", blob)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--months", type=int, default=6, help="full months to compare (the current month is added)")
    parser.add_argument("--exclude", nargs="*", default=[], metavar="LABEL_OR_ID")
    parser.add_argument("--title", default="Income vs expenses")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)

    payload = build(months=args.months, exclude=frozenset(args.exclude), title=args.title)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(payload))
    args.out.chmod(0o600)
    print(f"wrote {args.out}")
    print("months:", payload["months"][0], "to", payload["months"][-1], "| accounts:", len(payload["accounts"]))
    if args.open:
        subprocess.run(["open", str(args.out)], check=False)


if __name__ == "__main__":
    main()
