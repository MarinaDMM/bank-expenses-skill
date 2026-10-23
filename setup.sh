#!/usr/bin/env bash
# Builds the connector's virtual environment, runs its tests, and prints the Claude Desktop config entry.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONNECTOR="$HERE/connector"

python3 -m venv "$CONNECTOR/.venv"
"$CONNECTOR/.venv/bin/pip" install -q -r "$CONNECTOR/requirements.txt"
"$CONNECTOR/.venv/bin/python" -m pytest -q -p no:cacheprovider "$CONNECTOR/tests"

cat <<MSG

Environment ready. Next steps (details in README.md):
  1. Register at Enable Banking, create a PRODUCTION application, download its private key.
     Save it as ~/.config/bank-mcp/private.pem and create ~/.config/bank-mcp/app.json
  2. Link each bank (approve in your bank's own app):
       cd "$CONNECTOR" && ./.venv/bin/python -m bank_mcp.link <label> --bank "<Bank name>" --days 60
  3. Register the server with Claude Desktop (claude_desktop_config.json, under "mcpServers"), then restart it:

  "bank": {
    "command": "$CONNECTOR/.venv/bin/python",
    "args": ["-m", "bank_mcp.server"],
    "env": { "PYTHONPATH": "$CONNECTOR" }
  }
MSG
