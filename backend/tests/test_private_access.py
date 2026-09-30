from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from kindrop.api import create_app
from kindrop.config import RuntimeSettings
from kindrop.crypto import SecretStore
from kindrop.database import Database
from kindrop.models import AppSettings

PRIVATE_URL = "https://homeserver.tail4fc390.ts.net:8445"


def private_client(tmp_path):
    runtime = RuntimeSettings(app_base_url=PRIVATE_URL)
    app = create_app(Database(f"sqlite:///{tmp_path / 'test.db'}"), runtime)
    return TestClient(app, base_url=PRIVATE_URL)


def test_configured_private_host_is_accepted(tmp_path):
    client = private_client(tmp_path)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_configured_private_origin_can_modify_settings(tmp_path):
    client = private_client(tmp_path)
    response = client.delete("/api/oauth", headers={"Origin": PRIVATE_URL})
    assert response.status_code == 204


def test_unconfigured_hosts_remain_blocked(tmp_path):
    client = private_client(tmp_path)
    for host in ["evil.example", "other.tail4fc390.ts.net", "192.168.1.10"]:
        assert client.get("/api/health", headers={"Host": host}).status_code == 400


def test_untrusted_origins_remain_blocked(tmp_path):
    client = private_client(tmp_path)
    for origin in [
        "https://evil.example",
        "https://other.tail4fc390.ts.net",
        "http://homeserver.tail4fc390.ts.net:8445",
        "https://homeserver.tail4fc390.ts.net:8446",
        "null",
        "https://homeserver.tail4fc390.ts.net:invalid",
        "https://homeserver.tail4fc390.ts.net:8445/extra",
    ]:
        assert client.delete("/api/oauth", headers={"Origin": origin}).status_code == 403


def test_local_access_is_preserved(tmp_path):
    client = private_client(tmp_path)
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8787"}).status_code == 200


def test_default_configuration_rejects_private_hosts(tmp_path):
    app = create_app(Database(f"sqlite:///{tmp_path / 'test.db'}"), RuntimeSettings())
    client = TestClient(app, base_url=PRIVATE_URL)
    assert client.get("/api/health").status_code == 400


def test_oauth_uses_private_callback_for_start_and_exchange(tmp_path, monkeypatch):
    key_file = tmp_path / "secret.key"
    key_file.write_bytes(Fernet.generate_key())
    runtime = RuntimeSettings(app_base_url=PRIVATE_URL + "/", secret_key_file=key_file)
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    store = SecretStore(key_file)
    with database.session() as session:
        session.add(AppSettings(id=1, encrypted_google_client=store.encrypt_json({"web": {}})))
        session.commit()
    seen = []

    def authorize(config, redirect_uri):
        seen.append(redirect_uri)
        return "https://accounts.google.com/authorize", "test-state", "test-verifier"

    def exchange(config, redirect_uri, state, code, verifier):
        seen.append(redirect_uri)
        assert (state, code, verifier) == ("test-state", "test-code", "test-verifier")
        return {"refresh_token": "test-token"}

    def unavailable_services():
        raise RuntimeError("No Google network access in this test")

    monkeypatch.setattr("kindrop.api.authorization_url", authorize)
    monkeypatch.setattr("kindrop.api.exchange_code", exchange)
    client = TestClient(create_app(database, runtime, service_factory=unavailable_services))
    assert client.get("/api/oauth/start").status_code == 200
    response = client.get(
        "/api/oauth/callback?state=test-state&code=test-code", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?connected=true"
    assert seen == [PRIVATE_URL + "/api/oauth/callback"] * 2
    with database.session() as session:
        settings = session.get(AppSettings, 1)
        token = store.decrypt_json(settings.encrypted_google_token)
        assert token == {"refresh_token": "test-token"}
        assert settings.oauth_state is None
