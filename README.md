# bank-expenses: a Claude skill + local MCP server for your own bank accounts

Ask Claude where your money goes. A small **read-only** MCP server reads your own accounts through
[Enable Banking](https://enablebanking.com) (PSD2 open banking), and a skill teaches Claude how to use it:

- spending per month by category, balances, transaction search
- **offline dashboards**: spending by category, and income versus expenses (earned income kept apart from transfers)
- a structured **optimisation review**: billing anomalies, overlapping subscriptions, contracts to re-shop, card costs

Everything runs on your computer. The server has no payment, transfer or order tool.

- `SKILL.md`: the skill (workflow, method and safety rules).
- `connector/`: the MCP server, dashboard generators and tests.

## Install

```bash
git clone <this-repo-url> ~/.claude/skills/bank-expenses
cd ~/.claude/skills/bank-expenses && ./setup.sh
```

`setup.sh` builds the environment, runs the tests and prints the remaining steps. Then follow
[`connector/README.md`](connector/README.md): register your own Enable Banking application, link your banks (you approve
in your bank's own app), and add the server to Claude Desktop.

## Good to know

- **You need your own Enable Banking application.** Nothing is shared with the author. Their free "restricted
  production" mode is meant for the personal use of private individuals: read their current terms, especially for
  business accounts, and check prices in their Control Panel.
- **Banks cap consent.** You choose the period when linking (for example 60 days); relink before it ends.
- **Credit cards are not itemised** by the bank, only the payments to them. ABN AMRO leaves credit cards out of its
  open-banking access entirely.
- **Someone else's account needs their agreement** before you connect it or analyse it.

## Privacy

- Your private key, app settings, bank sessions and personal category rules live in `~/.config/bank-mcp/`, outside
  this repository. `.gitignore` also blocks keys, sessions, exports and the generated dashboards.
- Only the last four characters of an IBAN are stored or returned, and IBANs inside payment text are masked.
- Dashboards are single offline HTML files (no network, no external scripts) that contain your finances. Do not share
  or upload them.
- Whatever Claude reads becomes part of your conversation with Anthropic. Connect only accounts you want analysed.
- Tests use fictional data in a throwaway config folder and never read your real files.

## License

MIT. See [LICENSE](LICENSE).
