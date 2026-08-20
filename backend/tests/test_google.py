import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from kindrop.google import GoogleGmailGateway
from kindrop.services import AmbiguousSendError, PermanentSendError, TransientSendError


class FakeCall:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.result


class FakeMessages:
    def __init__(
        self,
        send_result=None,
        send_error: Exception | None = None,
        list_result=None,
    ) -> None:
        self.send_result = send_result
        self.send_error = send_error
        self.list_result = list_result or {}
        self.sent_bodies: list[dict] = []
        self.list_queries: list[str] = []

    def send(self, userId: str, body: dict) -> FakeCall:  # noqa: N803 - Google API naming
        assert userId == "me"
        self.sent_bodies.append(body)
        return FakeCall(self.send_result, self.send_error)

    def list(self, userId: str, q: str, maxResults: int) -> FakeCall:  # noqa: N803
        assert userId == "me"
        self.list_queries.append(q)
        return FakeCall(self.list_result)


class FakeGmailService:
    def __init__(self, messages: FakeMessages) -> None:
        self._messages = messages

    def users(self) -> "FakeGmailService":
        return self

    def messages(self) -> FakeMessages:
        return self._messages


class FakeFactory:
    def __init__(self, messages: FakeMessages) -> None:
        self._messages = messages

    def gmail(self) -> FakeGmailService:
        return FakeGmailService(self._messages)


def gateway(messages: FakeMessages) -> GoogleGmailGateway:
    return GoogleGmailGateway(FakeFactory(messages))


def http_error(status: int, content: bytes = b"{}") -> HttpError:
    return HttpError(Response({"status": status}), content)


@pytest.fixture
def epub(tmp_path: Path) -> Path:
    path = tmp_path / "volume.epub"
    path.write_bytes(b"epub")
    return path


def test_send_epub_stamps_the_message_id_header(epub: Path) -> None:
    messages = FakeMessages(send_result={"id": "gm-1"})

    result = gateway(messages).send_epub(
        "reader_123@kindle.com", "Volume Seven", epub, rfc822_message_id="<abc@kindrop.local>"
    )

    assert result == "gm-1"
    raw = base64.urlsafe_b64decode(messages.sent_bodies[0]["raw"].encode())
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert parsed["Message-ID"] == "<abc@kindrop.local>"
    assert parsed["To"] == "reader_123@kindle.com"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http_error(429), TransientSendError),
        (http_error(403, b'{"reason": "rateLimitExceeded"}'), TransientSendError),
        (http_error(403, b'{"reason": "userRateLimitExceeded"}'), TransientSendError),
        (http_error(403, b'{"reason": "insufficientPermissions"}'), PermanentSendError),
        (http_error(400), PermanentSendError),
        (http_error(401), PermanentSendError),
        (http_error(500), AmbiguousSendError),
        (http_error(503), AmbiguousSendError),
        (TimeoutError("slow wifi"), AmbiguousSendError),
        (ConnectionError("reset"), AmbiguousSendError),
    ],
)
def test_send_errors_are_classified(epub: Path, error: Exception, expected: type) -> None:
    messages = FakeMessages(send_error=error)

    with pytest.raises(expected):
        gateway(messages).send_epub(
            "reader_123@kindle.com", "Volume Seven", epub, rfc822_message_id="<abc@kindrop.local>"
        )


def test_find_sent_message_queries_without_angle_brackets() -> None:
    messages = FakeMessages(list_result={"messages": [{"id": "m-1"}]})

    assert gateway(messages).find_sent_message("<abc@kindrop.local>") == "m-1"
    assert messages.list_queries == ["in:sent rfc822msgid:abc@kindrop.local"]


def test_find_sent_message_returns_none_when_absent() -> None:
    messages = FakeMessages(list_result={})

    assert gateway(messages).find_sent_message("<abc@kindrop.local>") is None
