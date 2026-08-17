import os
import secrets
from typing import Any

from google_auth_oauthlib.flow import Flow

from .google import GOOGLE_SCOPES


def validate_client_config(config: dict[str, Any]) -> None:
    section = config.get("web") or config.get("installed")
    if not isinstance(section, dict):
        raise ValueError("Upload a Google OAuth client JSON file")
    required = {"client_id", "client_secret", "auth_uri", "token_uri"}
    if not required.issubset(section):
        raise ValueError("The Google OAuth client JSON is missing required fields")


def authorization_url(client_config: dict[str, Any], redirect_uri: str) -> tuple[str, str, str]:
    validate_client_config(client_config)
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    state = secrets.token_urlsafe(32)
    flow = Flow.from_client_config(client_config, scopes=GOOGLE_SCOPES, state=state)
    flow.redirect_uri = redirect_uri
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return url, state, flow.code_verifier


def exchange_code(
    client_config: dict[str, Any],
    redirect_uri: str,
    state: str,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    flow.fetch_token(code=code)
    return {
        "token": flow.credentials.token,
        "refresh_token": flow.credentials.refresh_token,
        "token_uri": flow.credentials.token_uri,
        "client_id": flow.credentials.client_id,
        "client_secret": flow.credentials.client_secret,
        "scopes": list(flow.credentials.scopes or GOOGLE_SCOPES),
    }
