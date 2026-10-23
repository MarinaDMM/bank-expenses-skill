"""Link a bank once. Run: python -m bank_mcp.link <label> --bank "ING" [--country NL] [--days 90] [--psu personal]

You approve access in your bank's own app. This program never sees your bank login.
"""

import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from bank_mcp import config
from bank_mcp.client import EnableBankingClient


def extract_code(redirect: str, expected_state: str) -> str:
    """Pull the authorization code out of the address the bank sent you back to."""
    query = parse_qs(urlparse(redirect.strip()).query)
    if query.get("error"):
        detail = query.get("error_description", [""])[0]
        raise ValueError(f"the bank reported an error: {query['error'][0]} {detail}".strip())
    if query.get("state", [""])[0] != expected_state:
        raise ValueError("state mismatch: this redirect does not belong to the request you just started")
    if not query.get("code"):
        raise ValueError("no 'code' in that address; paste the full address from the browser bar")
    return query["code"][0]


def session_record(label: str, bank: str, country: str, session: dict, valid_until: str) -> dict:
    """What we keep: ids needed to read data, and only the last 4 characters of each IBAN."""
    return {
        "label": label,
        "aspsp": {"name": bank, "country": country},
        "session_id": session["session_id"],
        "valid_until": valid_until,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "accounts": [
            {
                "uid": account["uid"],
                "name": account.get("name") or "",
                "currency": account.get("currency") or "",
                "iban_last4": "".join((account.get("account_id") or {}).get("iban", "????").split())[-4:],
            }
            for account in session["accounts"]
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("label", help="short name for this link, e.g. ing or abn")
    parser.add_argument("--bank", required=True, help='bank name exactly as Enable Banking lists it, e.g. "ING"')
    parser.add_argument("--country", default="NL")
    parser.add_argument("--days", type=int, default=90, help="how long the consent should last")
    parser.add_argument("--psu", default="personal", choices=["personal", "business"])
    args = parser.parse_args(argv)

    app = config.load_app()
    client = EnableBankingClient(app["application_id"], app["private_key"])

    banks = [b["name"] for b in client.list_aspsps(args.country)]
    match = next((name for name in banks if name.lower() == args.bank.lower()), None)
    if not match:
        sys.exit(f"bank {args.bank!r} not found for {args.country}. Available: {', '.join(sorted(banks))}")

    state = str(uuid.uuid4())
    valid_until = (datetime.now(timezone.utc) + timedelta(days=args.days)).replace(microsecond=0).isoformat()
    auth = client.start_auth(match, args.country, valid_until, state, app["redirect_url"], args.psu)

    print("\n1. Open this address in your browser and approve access in your bank's app:\n")
    print(auth["url"])
    print("\n2. When the browser lands on your redirect page, copy the FULL address from the address bar.\n")
    redirect = input("Paste it here: ")

    try:
        code = extract_code(redirect, state)
    except ValueError as error:
        sys.exit(str(error))

    session = client.create_session(code)
    path = config.save_session(args.label, session_record(args.label, match, args.country, session, valid_until))
    print(f"\nLinked {len(session['accounts'])} account(s) as '{args.label}'. Saved {path}")
    print("Consent expires:", valid_until)


if __name__ == "__main__":
    main()
