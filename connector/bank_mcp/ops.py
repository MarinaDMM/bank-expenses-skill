"""Pure functions that turn raw bank data into compact, privacy-trimmed results."""

import re
from collections import defaultdict

# Grouped (NL00 TEST 0000 ...) or compact IBANs inside free text.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,4})?\b")
MAX_DESCRIPTION = 140


def mask_iban(value: str) -> str:
    compact = re.sub(r"\s", "", value)
    return f"[IBAN ...{compact[-4:]}]"


def scrub(text: str) -> str:
    """Replace IBANs in free text and cap the length."""
    text = _IBAN.sub(lambda m: mask_iban(m.group(0)), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_DESCRIPTION]


def normalize_transaction(tx: dict) -> dict:
    """Signed amount (negative = money out), one date, counterparty name, short scrubbed description."""
    amount = float(tx["transaction_amount"]["amount"])
    outgoing = tx.get("credit_debit_indicator") == "DBIT"
    party = tx.get("creditor") if outgoing else tx.get("debtor")
    description = " ".join(tx.get("remittance_information") or [])
    return {
        "date": tx.get("booking_date") or tx.get("value_date") or tx.get("transaction_date") or "",
        "amount": -amount if outgoing else amount,
        "currency": tx["transaction_amount"]["currency"],
        "counterparty": scrub((party or {}).get("name") or ""),
        "description": scrub(description),
        "status": tx.get("status", ""),
    }


def fetch_transactions(client, account_uid: str, date_from: str, date_to: str, max_pages: int = 20) -> list[dict]:
    """Follow continuation keys until the bank has no more pages (or max_pages is reached)."""
    out, key = [], None
    for _ in range(max_pages):
        page = client.transactions_page(account_uid, date_from, date_to, key)
        out.extend(normalize_transaction(tx) for tx in page.get("transactions", []))
        key = page.get("continuation_key")
        if not key:
            break
    return sorted(out, key=lambda t: t["date"], reverse=True)


def filter_transactions(
    txs: list[dict], text: str = "", min_amount: float | None = None, max_amount: float | None = None
) -> list[dict]:
    """text matches counterparty or description; amount bounds apply to the absolute amount."""
    needle = text.lower()
    result = []
    for tx in txs:
        if needle and needle not in f"{tx['counterparty']} {tx['description']}".lower():
            continue
        size = abs(tx["amount"])
        if min_amount is not None and size < min_amount:
            continue
        if max_amount is not None and size > max_amount:
            continue
        result.append(tx)
    return result


def monthly_summary(txs: list[dict], top_n: int = 5) -> list[dict]:
    """Per month: money in, money out, net, and the biggest outgoing counterparties."""
    months: dict[str, dict] = defaultdict(lambda: {"in": 0.0, "out": 0.0, "spend": defaultdict(float)})
    for tx in txs:
        if not tx["date"]:
            continue
        bucket = months[tx["date"][:7]]
        if tx["amount"] >= 0:
            bucket["in"] += tx["amount"]
        else:
            bucket["out"] += -tx["amount"]
            bucket["spend"][tx["counterparty"] or "(unknown)"] += -tx["amount"]
    summary = []
    for month in sorted(months, reverse=True):
        bucket = months[month]
        top = sorted(bucket["spend"].items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        summary.append(
            {
                "month": month,
                "money_in": round(bucket["in"], 2),
                "money_out": round(bucket["out"], 2),
                "net": round(bucket["in"] - bucket["out"], 2),
                "top_outgoing": [{"counterparty": name, "amount": round(total, 2)} for name, total in top],
            }
        )
    return summary
