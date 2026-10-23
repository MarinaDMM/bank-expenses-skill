"""Where credentials and linked sessions live. Everything is under one private folder, outside the repo."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("BANK_MCP_HOME", Path.home() / ".config" / "bank-mcp"))


def sessions_dir() -> Path:
    return home() / "sessions"


def load_app() -> dict:
    """App settings from app.json plus the private key text. Raises ValueError with the fix if anything is missing."""
    app_file = home() / "app.json"
    if not app_file.is_file():
        raise ValueError(f"missing {app_file}; see README step 'Save your app settings'")
    cfg = json.loads(app_file.read_text())
    for field in ("application_id", "redirect_url"):
        if not cfg.get(field):
            raise ValueError(f"{app_file} needs a '{field}' value")
    key_path = Path(cfg.get("private_key_path") or home() / "private.pem").expanduser()
    if not key_path.is_file():
        raise ValueError(f"private key not found at {key_path}")
    return {
        "application_id": cfg["application_id"],
        "redirect_url": cfg["redirect_url"],
        "private_key": key_path.read_text(),
    }


def _session_path(label: str) -> Path:
    if not label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("account label may only contain letters, digits, '-' and '_'")
    return sessions_dir() / f"{label}.json"


def save_session(label: str, data: dict) -> Path:
    directory = sessions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = _session_path(label)
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)
    return path


def load_session(label: str) -> dict:
    path = _session_path(label)
    if not path.is_file():
        raise ValueError(f"unknown bank link {label!r}; linked: {list_labels() or 'none, run bank_mcp.link first'}")
    return json.loads(path.read_text())


def list_labels() -> list[str]:
    directory = sessions_dir()
    return sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []


def days_left(session: dict, now: datetime | None = None) -> int:
    """Whole days until the bank consent expires (negative once expired)."""
    now = now or datetime.now(timezone.utc)
    valid_until = datetime.fromisoformat(session["valid_until"].replace("Z", "+00:00"))
    return (valid_until - now).days
