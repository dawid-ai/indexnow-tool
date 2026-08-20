from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List

import httpx

ENDPOINTS = {
    "indexnow": "https://api.indexnow.org/indexnow",
    "bing": "https://www.bing.com/indexnow",
}
SUCCESS_CODES = {200, 202}


@dataclass(frozen=True)
class SubmissionResult:
    status_code: int | None
    is_success: bool
    response_excerpt: str


def chunked(items: List[str], size: int = 10_000) -> Iterable[List[str]]:
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
            text_excerpt = (response.text or "")[:300]
            if response.status_code in SUCCESS_CODES:
                return SubmissionResult(response.status_code, True, text_excerpt)

            if response.status_code == 429 and attempt < max_retries:
                retry_after = response.headers.get("Retry-After")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else attempt * 5
                time.sleep(wait_seconds)
                continue

            return SubmissionResult(response.status_code, False, text_excerpt)
        except httpx.HTTPError as exc:
            if attempt < max_retries:
                time.sleep(attempt * 3)
                continue
            return SubmissionResult(None, False, str(exc)[:300])
