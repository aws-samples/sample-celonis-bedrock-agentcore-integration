"""OAuth2 Client Credentials token provider for Celonis MCP Server."""
""" only used by the test_local.py testing script"""

import time
import requests


class CelonisOAuthProvider:
    """Fetches and caches an OAuth2 access token using the client_credentials grant."""

    def __init__(self, token_url: str, client_id: str, client_secret: str, scope: str):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self._token: str | None = None
        self._expires_at: float = 0

    def get_token(self) -> str:
        """Return a valid access token, refreshing if expired."""
        if self._token and time.time() < self._expires_at:
            return self._token

        response = requests.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "scope": self.scope,
            },
            auth=(self.client_id, self.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        self._token = data["access_token"]
        # Refresh 60 s before actual expiry
        self._expires_at = time.time() + data.get("expires_in", 3600) - 60
        return self._token
