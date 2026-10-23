"""Minimal Enable Banking API client. Only read endpoints plus the two calls needed to link an account.

There is deliberately no payment method here: this project cannot move money.
"""

import time

API = "https://api.enablebanking.com"
RETRIES = 3


class BankApiError(RuntimeError):
    pass


class EnableBankingClient:
    def __init__(self, application_id: str, private_key: str, http=None, clock=time.time, sleep=time.sleep):
        self.application_id = application_id
        self.private_key = private_key
        self._http = http
        self._clock = clock
        self._sleep = sleep

    @property
    def http(self):
        if self._http is None:
            import requests

            self._http = requests.Session()
        return self._http

    def token(self) -> str:
        import jwt  # PyJWT

        issued = int(self._clock())
        claims = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": issued, "exp": issued + 3600}
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.application_id})

    def _send(self, method: str, path: str, **kwargs):
        """One request, retried on network errors (the API is sometimes slow)."""
        import requests

        for attempt in range(RETRIES + 1):
            try:
                return self.http.request(
                    method, API + path, headers={"Authorization": f"Bearer {self.token()}"}, timeout=60, **kwargs
                )
            except (requests.Timeout, requests.ConnectionError) as error:
                if attempt == RETRIES:
                    raise BankApiError(f"{method} {path} failed after {RETRIES + 1} tries: {type(error).__name__}") from None
                self._sleep(2 * (attempt + 1))

    def _call(self, method: str, path: str, **kwargs) -> dict:
        response = self._send(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("message") or response.json().get("error") or ""
            except ValueError:
                detail = ""
            raise BankApiError(f"{method} {path} failed with HTTP {response.status_code} {detail}".strip())
        return response.json()

    # --- read endpoints -----------------------------------------------------

    def list_aspsps(self, country: str) -> list[dict]:
        return self._call("GET", "/aspsps", params={"country": country})["aspsps"]

    def balances(self, account_uid: str) -> list[dict]:
        return self._call("GET", f"/accounts/{account_uid}/balances")["balances"]

    def transactions_page(
        self, account_uid: str, date_from: str, date_to: str, continuation_key: str | None = None
    ) -> dict:
        params = {"date_from": date_from, "date_to": date_to}
        if continuation_key:
            params["continuation_key"] = continuation_key
        return self._call("GET", f"/accounts/{account_uid}/transactions", params=params)

    # --- linking (used only by bank_mcp.link) -------------------------------

    def start_auth(self, bank: str, country: str, valid_until: str, state: str, redirect_url: str, psu_type: str) -> dict:
        body = {
            "access": {"valid_until": valid_until},
            "aspsp": {"name": bank, "country": country},
            "state": state,
            "redirect_url": redirect_url,
            "psu_type": psu_type,
        }
        return self._call("POST", "/auth", json=body)

    def create_session(self, code: str) -> dict:
        return self._call("POST", "/sessions", json={"code": code})
