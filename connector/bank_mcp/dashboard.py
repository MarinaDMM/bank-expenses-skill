"""Build an offline expenses dashboard (one HTML file) from your linked bank accounts.

Run: python -m bank_mcp.dashboard [--months 12] [--out PATH] [--exclude LABEL ...] [--title TEXT] [--open]

The file is self-contained: no network, no external scripts. It holds your financial data, so it is written
with mode 600 into a git-ignored folder. Do not share or upload it.
"""

import argparse
import json
import re
import statistics
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bank_mcp import categories, config, ops
from bank_mcp.client import BankApiError, EnableBankingClient

TEMPLATE = Path(__file__).with_name("dashboard_template.html")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "output" / "index.html"
HISTORY_TRIES = (365, 270, 180, 90)  # days; banks cap how far back they return data


def fetch_account(client, uid: str, today: date, days: int) -> tuple[list[dict], int]:
    """Fetch as much history as the bank allows, trying shorter windows if it refuses."""
    last_error = None
    for window in [days] + [d for d in HISTORY_TRIES if d < days]:  # the full request first, then shorter fallbacks
        try:
            txs = ops.fetch_transactions(
                client, uid, (today - timedelta(days=window)).isoformat(), today.isoformat(), max_pages=40
            )
            return txs, window
        except BankApiError as error:
            last_error = error
    raise last_error


def month_of(tx: dict) -> str:
    return tx["date"][:7]


def clean_name(tx: dict) -> str:
    """A short payee label with no card or account numbers."""
    text = f"{tx['counterparty']} {tx['description']}"
    if re.search(r"incasso ing creditcard", text, re.I):
        return "ING credit card (statement)"
    name = tx["counterparty"] or tx["description"]
    name = re.sub(r"^(BEA|eCom),?\s*(Apple Pay|Betaalpas|Google Pay)?\s*", "", name)
    name = re.split(r",\s*PAS|\s+NR:|\s+\d{2}\.\d{2}\.\d{2}", name)[0]
    name = re.sub(r"\s+via (Stichting )?(Mollie|Worldpay|Tikkie).*", "", name, flags=re.I) or name
    return re.sub(r"\s+", " ", name).strip()[:40] or "(unknown)"


def aggregate(accounts: list[dict], txs_by_account: dict[str, list[dict]], own_last4: set[str]) -> dict:
    """Sum expenses by (month, account, category) and collect the biggest counterparties per month."""
    totals: dict[tuple, float] = defaultdict(float)
    counterparties: dict[tuple, float] = defaultdict(float)
    transfers_out = defaultdict(float)
    for account in accounts:
        for tx in txs_by_account[account["id"]]:
            month = month_of(tx)
            if not month:
                continue
            result = categories.expense_of(tx, own_last4)
            if result is None:
                if tx["amount"] < 0 and categories.is_internal(tx, own_last4):
                    transfers_out[month] += -tx["amount"]
                continue
            category, scope, amount = result
            totals[(month, account["id"], category, scope)] += amount
            name = clean_name(tx)
            counterparties[(month, category, scope, name)] += amount

    # Refunds can pull a category below zero in a month; show it as zero instead of negative spending.
    by_month_category: dict[tuple, float] = defaultdict(float)
    for (m, _, c, _), v in totals.items():
        by_month_category[(m, c)] += v
    data = [
        {"month": m, "account": a, "category": c, "scope": s, "amount": round(v, 2)}
        for (m, a, c, s), v in sorted(totals.items())
        if abs(v) >= 0.005 and by_month_category[(m, c)] > 0
    ]
    top = defaultdict(list)
    for (m, c, s, name), v in counterparties.items():
        top[m].append({"category": c, "scope": s, "name": name, "amount": round(v, 2)})
    top_by_month = {m: sorted(rows, key=lambda r: r["amount"], reverse=True)[:25] for m, rows in top.items()}
    return {"data": data, "top": top_by_month, "internal_transfers": {m: round(v, 2) for m, v in transfers_out.items()}}


def find_recurring(accounts: list[dict], txs_by_account: dict[str, list[dict]], own_last4: set[str], today: date) -> list[dict]:
    """Same payee, similar amount, in at least 4 of the last 6 months."""
    window_start = (today.replace(day=1) - timedelta(days=170)).replace(day=1).isoformat()
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for account in accounts:
        for tx in txs_by_account[account["id"]]:
            if tx["date"] < window_start or tx["amount"] >= 0:
                continue
            result = categories.expense_of(tx, own_last4)
            if result is None or result[2] <= 0:
                continue
            groups[(account["id"], tx["counterparty"] or tx["description"][:30], round(tx["amount"] / 5))].append(
                {**tx, "category": result[0], "scope": result[1]}
            )
    recurring = []
    for (_, name, _), rows in groups.items():
        months = {month_of(r) for r in rows}
        if len(months) < 4:
            continue
        recurring.append(
            {
                "name": name,
                "category": rows[0]["category"],
                "scope": rows[0]["scope"],
                "amount": round(statistics.median(-r["amount"] for r in rows), 2),
                "day": int(statistics.median(int(r["date"][8:10]) for r in rows)),
                "months_seen": len(months),
            }
        )
    return sorted(recurring, key=lambda r: r["amount"], reverse=True)


def load_accounts(client, days: int, today: date, exclude=frozenset()) -> dict:
    """Fetch every linked account that is not excluded.

    Returns accounts, transactions per account, history coverage, the last-4 of all linked accounts, and the
    last-4 of the excluded ones (so callers can tell a transfer to an excluded account from a real payment).
    """
    accounts, txs_by_account, coverage = [], {}, {}
    linked_last4: set[str] = set()
    excluded_last4: set[str] = set()
    for label in config.list_labels():
        session = config.load_session(label)
        tails = {a["iban_last4"] for a in session["accounts"]}
        linked_last4 |= tails
        if label in exclude:
            excluded_last4 |= tails
            continue
        if config.days_left(session) < 0:
            print(f"skipping {label}: bank consent expired")
            continue
        for a in session["accounts"]:
            ident = f"{label}/{a['iban_last4']}"
            if ident in exclude:
                excluded_last4.add(a["iban_last4"])
                continue
            try:
                txs, window = fetch_account(client, a["uid"], today, days)
            except BankApiError as error:
                print(f"skipping {ident}: {error}")
                continue
            accounts.append({"id": ident, "bank": session["aspsp"]["name"], "holder": a["name"], "last4": a["iban_last4"]})
            txs_by_account[ident] = txs
            coverage[ident] = window
    return {"accounts": accounts, "txs": txs_by_account, "coverage": coverage,
            "linked_last4": linked_last4, "excluded_last4": excluded_last4}


def make_client(client=None):
    if client is not None:
        return client
    app = config.load_app()
    return EnableBankingClient(app["application_id"], app["private_key"])


def build(
    days: int = 365, today: date | None = None, client=None, exclude=frozenset(), title: str = "Monthly expenses"
) -> dict:
    """exclude: link labels (e.g. 'abn-mine') or account ids (e.g. 'ing/1234') to leave out of the dashboard."""
    today = today or date.today()
    loaded = load_accounts(make_client(client), days, today, exclude)
    accounts, txs_by_account = loaded["accounts"], loaded["txs"]
    own_last4 = loaded["linked_last4"]  # every linked account, so a transfer to an excluded one is still 'internal'
    agg = aggregate(accounts, txs_by_account, own_last4)
    months = sorted({row["month"] for row in agg["data"]})
    return {
        "title": title,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "current_month": today.isoformat()[:7],
        "currency": "EUR",
        "months": months,
        "accounts": accounts,
        "coverage_days": loaded["coverage"],
        **agg,
        "recurring": find_recurring(accounts, txs_by_account, own_last4, today),
    }


def render(payload: dict) -> str:
    html = TEMPLATE.read_text()
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("__DATA__", blob)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--months", type=int, default=12, help="history to request (banks may return less)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--exclude", nargs="*", default=[], metavar="LABEL_OR_ID",
                        help="leave out these links (e.g. abn-mine) or account ids (e.g. ing/1234)")
    parser.add_argument("--title", default="Monthly expenses")
    parser.add_argument("--open", action="store_true", help="open the file in your browser when done")
    args = parser.parse_args(argv)

    payload = build(days=args.months * 31, exclude=frozenset(args.exclude), title=args.title)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(payload))
    args.out.chmod(0o600)
    print(f"wrote {args.out}")
    print("months:", payload["months"][0], "to", payload["months"][-1], "| accounts:", len(payload["accounts"]))
    if args.open:
        subprocess.run(["open", str(args.out)], check=False)


if __name__ == "__main__":
    main()
