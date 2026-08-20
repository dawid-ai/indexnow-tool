from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator, List

import httpx

ENDPOINTS = {
    "indexnow": "https://api.indexnow.org/indexnow",
    "bing": "https://www.bing.com/indexnow",
}
SUCCESS_CODES = {200, 202}
MAX_BATCH = 10_000

# Documented IndexNow responses, translated into the thing you actually need to fix.
STATUS_HELP = {
    200: "Accepted.",
    202: "Accepted, key validation pending.",
    400: "Bad request: malformed JSON or URL list.",
    403: "Key not valid: the key file is missing, unreadable, or does not match the key sent.",
    404: "Endpoint or key file not found.",
    422: "Unprocessable: URLs do not belong to the host, or the key format is wrong.",
    429: "Rate limited: too many requests.",
}


@dataclass(frozen=True)
class SubmissionResult:
    status_code: int | None
    is_success: bool
    response_excerpt: str
    explanation: str

    @property
    def detail(self) -> str:
        code = self.status_code if self.status_code is not None else "no response"
        excerpt = self.response_excerpt.strip()
        return f"HTTP {code} - {self.explanation}" + (f" | {excerpt}" if excerpt else "")


def chunked(items: List[str], size: int = MAX_BATCH) -> Iterator[List[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def submit_url_batch(
    endpoint_choice: str,
    host: str,
    key: str,
    key_location: str | None,
    urls: List[str],
    max_retries: int = 3,
) -> SubmissionResult:
    if len(urls) > MAX_BATCH:
        raise ValueError(f"Batch of {len(urls)} exceeds the IndexNow limit of {MAX_BATCH}.")

    endpoint = ENDPOINTS[endpoint_choice]
    payload = {"host": host, "key": key, "urlList": urls}
    if key_location:
        payload["keyLocation"] = key_location

    attempt = 0
    while True:
        attempt += 1
        try:
            response = httpx.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=30,
            )
            excerpt = (response.text or "")[:300]
            explanation = STATUS_HELP.get(response.status_code, "Unexpected status code.")

            if response.status_code in SUCCESS_CODES:
                return SubmissionResult(response.status_code, True, excerpt, explanation)

            if response.status_code == 429 and attempt < max_retries:
                retry_after = response.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else attempt * 5
                time.sleep(min(wait_seconds, 120))
                continue

            return SubmissionResult(response.status_code, False, excerpt, explanation)
        except httpx.HTTPError as exc:
            if attempt < max_retries:
                time.sleep(attempt * 3)
                continue
            return SubmissionResult(None, False, str(exc)[:300], "Network error reaching the endpoint.")


def verify_key_file(host: str, key: str, key_location: str | None = None) -> tuple[bool, str]:
    """Check the key file a search engine will fetch before trusting a submission.

    A missing or mismatched key file is the usual cause of a 403, and it fails
    silently otherwise: submissions look accepted and nothing gets indexed.
    """
    url = key_location or f"https://{host}/{key}.txt"
    try:
        response = httpx.get(url, timeout=15, follow_redirects=True)
    except httpx.HTTPError as exc:
        return False, f"Could not fetch {url}: {exc}"

    if response.status_code != 200:
        return False, f"{url} returned HTTP {response.status_code}."

    body = (response.text or "").strip()
    if body != key:
        preview = body[:60] + ("..." if len(body) > 60 else "")
        return False, f"{url} does not contain the key. It contains: '{preview}'"

    return True, f"Key file verified at {url}"
