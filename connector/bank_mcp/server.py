"""Local, read-only MCP server for linked bank accounts."""

from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP

from bank_mcp import config, ops
from bank_mcp.client import EnableBankingClient

mcp = FastMCP("bank")
_client: EnableBankingClient | None = None


def _get_client() -> EnableBankingClient:
    global _client
    if _client is None:
        app = config.load_app()
        _client = EnableBankingClient(app["application_id"], app["private_key"])
    return _client


def _accounts() -> list[dict]:
    """Every linked account with a stable id like 'ing/4321' (link label / last 4 of the IBAN)."""
    found = []
    for label in config.list_labels():
        session = config.load_session(label)
        remaining = config.days_left(session)
        for account in session["accounts"]:
            found.append(
                {
                    "id": f"{label}/{account['iban_last4']}",
                    "bank": session["aspsp"]["name"],
                    "name": account["name"],
                    "currency": account["currency"],
                    "uid": account["uid"],
                    "days_left": remaining,
                }
            )
    return found


def _select(account: str) -> list[dict]:
    chosen = [
        a for a in _accounts() if account == "all" or a["id"] == account or a["id"].startswith(account + "/")
    ]
    if not chosen:
        raise ValueError(f"unknown account {account!r}; use list_accounts to see valid ids")
    expired = [a["id"] for a in chosen if a["days_left"] < 0]
    if expired:
        raise ValueError(f"bank consent expired for {expired}; relink with: python -m bank_mcp.link")
    return chosen


def _dates(date_from: str, date_to: str) -> tuple[str, str]:
    today = date.today()
    return date_from or (today - timedelta(days=90)).isoformat(), date_to or today.isoformat()


@mcp.tool()
def list_accounts() -> list[dict]:
    """List linked bank accounts (id, bank, name, currency) and how many days the bank consent lasts."""
    return [{k: v for k, v in a.items() if k != "uid"} for a in _accounts()]


@mcp.tool()
def get_balances(account: str = "all") -> list[dict]:
    """Current balances. account: an id from list_accounts, a link label (all its accounts), or 'all'."""
    result = []
    for a in _select(account):
        for balance in _get_client().balances(a["uid"]):
            result.append(
                {
                    "account": a["id"],
                    "type": balance.get("balance_type"),
                    "amount": balance["balance_amount"]["amount"],
                    "currency": balance["balance_amount"]["currency"],
                    "as_of": balance.get("reference_date") or balance.get("last_change_date_time"),
                }
            )
    return result


@mcp.tool()
def get_transactions(
    account: str = "all",
    date_from: str = "",
    date_to: str = "",
    text: str = "",
    min_amount: float | None = None,
    max_amount: float | None = None,
    max_results: int = 100,
) -> list[dict]:
    """Transactions, newest first. Dates are YYYY-MM-DD (default: last 90 days). Amounts are signed: negative = money out.

    text matches counterparty or description. min_amount and max_amount apply to the absolute amount.
    IBANs in the text are masked to their last 4 characters.
    """
    start, end = _dates(date_from, date_to)
    result = []
    for a in _select(account):
        txs = ops.fetch_transactions(_get_client(), a["uid"], start, end)
        for tx in ops.filter_transactions(txs, text, min_amount, max_amount):
            result.append({"account": a["id"], **tx})
    result.sort(key=lambda t: t["date"], reverse=True)
    return result[:max_results]


@mcp.tool()
def monthly_summary(account: str = "all", months: int = 6, top_n: int = 5) -> list[dict]:
    """Per month: money in, money out, net, and the biggest outgoing counterparties (computed locally)."""
    start = (date.today().replace(day=1) - timedelta(days=30 * (months - 1))).replace(day=1).isoformat()
    txs = []
    for a in _select(account):
        txs.extend(ops.fetch_transactions(_get_client(), a["uid"], start, date.today().isoformat()))
    return ops.monthly_summary(txs, top_n)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
