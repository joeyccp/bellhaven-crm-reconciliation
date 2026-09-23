from __future__ import annotations

from typing import Any

import requests

from .config import API_BASE, TOKEN


class CRMClient:
    def __init__(self, token: str = TOKEN, api_base: str = API_BASE):
        if not token:
            raise RuntimeError("Set BELLHAVEN_API_TOKEN before accessing the CRM")
        self.api_base = api_base
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        response = self.session.request(method, f"{self.api_base}{path}", timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()

    def _all(self, path: str) -> list[dict]:
        first = self._request("GET", path, params={"page": 1, "page_size": 200})
        return first["data"]

    def accounts(self) -> list[dict]:
        return self._all("/accounts")

    def contacts(self) -> list[dict]:
        return self._all("/contacts")

    def get_account(self, account_id: str) -> dict:
        return self._request("GET", f"/accounts/{account_id}")

    def create_account(self, fields: dict) -> dict:
        return self._request("POST", "/accounts", json=fields)

    def update_account(self, account_id: str, fields: dict) -> dict:
        return self._request("PATCH", f"/accounts/{account_id}", json=fields)

    def create_contact(self, fields: dict) -> dict:
        return self._request("POST", "/contacts", json=fields)

    def update_contact(self, contact_id: str, fields: dict) -> dict:
        return self._request("PATCH", f"/contacts/{contact_id}", json=fields)

