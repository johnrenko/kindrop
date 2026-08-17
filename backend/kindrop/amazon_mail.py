import re
from dataclasses import dataclass
from email.utils import parseaddr
from urllib.parse import urlparse

AMAZON_DOMAINS = {
    "amazon.com",
    "amazon.co.uk",
    "amazon.ca",
    "amazon.com.au",
    "amazon.de",
    "amazon.es",
    "amazon.fr",
    "amazon.it",
    "amazon.co.jp",
}
ERROR_CODE = re.compile(r"\b(E(?:00[1-9]|01[0-5]|999))\b", re.IGNORECASE)
URL = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)


@dataclass(frozen=True)
class AmazonMailClassification:
    kind: str
    error_code: str | None = None
    verification_url: str | None = None


def is_allowed_amazon_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.rstrip(".").lower()
    return any(
        normalized == domain or normalized.endswith(f".{domain}") for domain in AMAZON_DOMAINS
    )


def is_safe_verification_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    return (
        parsed.scheme == "https"
        and parsed.port in (None, 443)
        and is_allowed_amazon_host(parsed.hostname)
        and ("sendtokindle" in path or ("kindle" in path and "verif" in path))
    )


def classify_amazon_message(*, sender: str, subject: str, text: str) -> AmazonMailClassification:
    sender_address = parseaddr(sender)[1]
    sender_domain = sender_address.rsplit("@", 1)[-1] if "@" in sender_address else ""
    if not is_allowed_amazon_host(sender_domain):
        return AmazonMailClassification(kind="irrelevant")

    combined = f"{subject}\n{text}"
    error = ERROR_CODE.search(combined)
    if error:
        return AmazonMailClassification(kind="rejected", error_code=error.group(1).upper())

    if "verif" in combined.lower():
        for candidate in URL.findall(combined):
            cleaned = candidate.rstrip(".,);]")
            if is_safe_verification_url(cleaned):
                return AmazonMailClassification(
                    kind="verification_required", verification_url=cleaned
                )

    return AmazonMailClassification(kind="irrelevant")
