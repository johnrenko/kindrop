import time
from urllib.parse import parse_qs, urlparse

import requests_oauthlib

from kindrop.oauth import authorization_url, exchange_code

CLIENT_CONFIG = {
    "web": {
        "client_id": "client-id.apps.googleusercontent.com",
        "client_secret": "client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}

REDIRECT_URI = "http://127.0.0.1:8787/api/oauth/callback"


def test_exchange_code_sends_the_pkce_verifier_issued_at_authorization(monkeypatch) -> None:
    url, state, verifier = authorization_url(CLIENT_CONFIG, REDIRECT_URI)

    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"], "the authorization URL must carry a PKCE challenge"
    assert verifier

    captured: dict[str, object] = {}

    def fake_fetch_token(self, token_url, **kwargs):
        captured["code_verifier"] = kwargs.get("code_verifier")
        token = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "expires_at": time.time() + 3600,
        }
        self.token = token
        return token

    monkeypatch.setattr(requests_oauthlib.OAuth2Session, "fetch_token", fake_fetch_token)

    token = exchange_code(CLIENT_CONFIG, REDIRECT_URI, state, "auth-code", verifier)

    assert captured["code_verifier"] == verifier
    assert token["refresh_token"] == "refresh-token"
