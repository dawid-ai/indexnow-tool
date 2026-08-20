from __future__ import annotations

import csv
import gzip
import io
import xml.etree.ElementTree as ET
from typing import Callable, List, Set

import httpx


class SourceError(Exception):
    """A URL source could not be read. Message is safe to show to the user."""


ProgressFn = Callable[[str], None]


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_paste(text: str) -> List[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def parse_txt_bytes(content: bytes) -> List[str]:
    return parse_paste(content.decode("utf-8-sig", errors="replace"))


def parse_csv_bytes(content: bytes) -> List[str]:
    decoded = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(decoded))
    output: List[str] = []
    for index, row in enumerate(reader):
        if not row:
            continue
        first = row[0].strip()
        if not first:
            continue
        # Skip a header row so "url" does not get reported as an invalid URL.
        if index == 0 and "://" not in first:
            continue
        output.append(first)
    return output


def _fetch(url: str, timeout_seconds: int) -> str:
    try:
        response = httpx.get(
            url,
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "indexnow-tool/1.0"},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise SourceError(f"{url} returned HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        raise SourceError(f"Could not fetch {url}: {exc}") from exc

    raw = response.content
    if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw).decode("utf-8", errors="replace")
        except OSError as exc:
            raise SourceError(f"Could not decompress {url}: {exc}") from exc
    return response.text


def parse_sitemap_url(
    sitemap_url: str,
    max_depth: int = 3,
    timeout_seconds: int = 30,
    on_progress: ProgressFn | None = None,
) -> List[str]:
    """Collect page URLs from a sitemap or sitemap index.

    Which branch to follow is decided by the document's root element
    (`sitemapindex` vs `urlset`), not by guessing from the link text. Guessing
    silently dropped every page in a sitemap that happened to contain a URL with
    "sitemap" in it.
    """
    visited: Set[str] = set()
    urls: List[str] = []

    def walk(current_url: str, depth: int) -> None:
        if current_url in visited or depth > max_depth:
            return
        visited.add(current_url)

        if on_progress:
            on_progress(f"Fetching sitemap: {current_url}")

        text = _fetch(current_url, timeout_seconds)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise SourceError(f"{current_url} is not valid XML: {exc}") from exc

        locs = [
            element.text.strip()
            for element in root.iter()
            if _local_tag(element.tag) == "loc" and element.text and element.text.strip()
        ]
        if not locs:
            if on_progress:
                on_progress(f"No <loc> entries in {current_url}")
            return

        if _local_tag(root.tag) == "sitemapindex":
            for nested in locs:
                walk(nested, depth + 1)
        else:
            urls.extend(locs)
            if on_progress:
                on_progress(f"Found {len(locs)} URLs in {current_url}")

    walk(sitemap_url, 0)

    if not urls and not visited:
        raise SourceError(f"No sitemap could be read from {sitemap_url}.")
    return urls
