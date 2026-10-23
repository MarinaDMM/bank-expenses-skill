# bank connector (MCP server)

A local, **read-only** MCP server that lets Claude read your own bank accounts through
[Enable Banking](https://enablebanking.com) (PSD2 open banking). It cannot move money: the code contains no payment calls.

Tools: `list_accounts`, `get_balances`, `get_transactions`, `monthly_summary`.

Your bank login never touches this project. You approve access inside your bank's own app.

## Before you start

- Free "restricted production" mode is meant for **personal use of private individuals**. Start with personal accounts.
  Check Enable Banking's terms before linking a business account.
- If you run a sole-trader business from a **personal** account, link it as personal (the default). Only a dedicated
  business account needs `--psu business`.
- ABN AMRO's open-banking access covers current/payment accounts, not credit cards.
- Check the current price in the Enable Banking Control Panel before you link anything.

## Setup

1. **Register at Enable Banking.** Create an account and open the Control Panel.
2. **Register an application.** Choose **Production** (not Sandbox).
   - Redirect URL: use an `https` address. `https://localhost/callback` works because you never need the page to load;
     you only copy the address from the browser bar. If Enable Banking rejects it, use another https address you control.
   - Download the **private key** (`.pem`) when offered and keep it safe; assume it cannot be shown again. Note the **Application ID**.
3. **Save your app settings** (never commit these files):

   ```bash
   mkdir -p ~/.config/bank-mcp && chmod 700 ~/.config/bank-mcp
   mv ~/Downloads/<application-id>.pem ~/.config/bank-mcp/private.pem
   chmod 600 ~/.config/bank-mcp/private.pem
   ```

   Create `~/.config/bank-mcp/app.json`:

   ```json
   { "application_id": "<application-id>", "redirect_url": "https://localhost/callback" }
   ```

4. **Activate the app by linking your own account.** In the Control Panel click **Activate by linking accounts** and
   approve in your bank's app. The app can only read accounts you link.
5. **Install:**

   ```bash
   cd connector && python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt   # or run ../setup.sh
   ./.venv/bin/python -m pytest -q tests
   ```

6. **Link each bank** (once per bank, then again before the consent expires):

   ```bash
   ./.venv/bin/python -m bank_mcp.link ing --bank "ING"
   ./.venv/bin/python -m bank_mcp.link abn --bank "ABN AMRO"
   ```

   For a **business** account add `--psu business`, for example:

   ```bash
   ./.venv/bin/python -m bank_mcp.link ing-business --bank "ING" --psu business
   ```

   Open the printed address, approve in the bank app, copy the **full address** the browser ends on and paste it back.
   If the bank name is not found, the command lists the names Enable Banking uses.

7. **Register with Claude Desktop** (`claude_desktop_config.json`), then restart Claude Desktop:

   ```json
   "bank": {
     "command": "/absolute/path/to/bank-expenses/connector/.venv/bin/python",
     "args": ["-m", "bank_mcp.server"],
     "env": { "PYTHONPATH": "/absolute/path/to/bank-expenses/connector" }
   }
   ```

## Privacy and safety

- Keys, app settings and sessions stay in `~/.config/bank-mcp/` (private, mode 600). `.gitignore` blocks `*.pem`,
  `app.json`, `sessions/` and data exports.
- Only the last 4 characters of each IBAN are stored or returned. IBANs found in transaction text are masked.
- Monthly totals are computed locally, so Claude can answer from a compact summary instead of raw lists.
- Whatever Claude reads becomes part of your conversation with Anthropic. Link only the accounts you want to analyse.
- Bank consent expires (you choose up to `--days`, default 90). `list_accounts` shows `days_left`; relink before it hits 0.
- Do not paste the private key, session files or transaction exports into chat or commit them.

## Tests

`./.venv/bin/python -m pytest -q tests` uses a throwaway key and a fake HTTP layer. No network, no real data.

## Dashboards (offline HTML, local only)

```bash
# spending per month by category
./.venv/bin/python -m bank_mcp.dashboard --months 12 --open
# income vs expenses per month, optionally leaving some accounts out
./.venv/bin/python -m bank_mcp.cashflow --months 6 --exclude <link-label> --open
```

Each writes one self-contained HTML file into `output/` (git-ignored, mode 600). No network, no external scripts.
Personal category and income rules live in `~/.config/bank-mcp/categories.json` (see `categories.example.json`).

### Try the dashboards without any bank data

```bash
cd connector && ./.venv/bin/python examples/make_sample.py
```

This writes `output/sample-spending.html` and `output/sample-income-vs-expenses.html` from invented data (fixed seed),
so you can see the dashboards, or take screenshots, without connecting an account.
