from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from typing import Iterable, List, Set

import httpx


def parse_paste(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_txt_bytes(content: bytes) -> List[str]:
    decoded = content.decode("utf-8-sig", errors="replace")
    return parse_paste(decoded)


def parse_csv_bytes(content: bytes) -> List[str]:
    decoded = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(decoded))
    output: List[str] = []
    for row in reader:
        if not row:
            continue
        first = row[0].strip()
        if first:
            output.append(first)
    return output


def _iter_locs(xml_text: str) -> Iterable[str]:
    root = ET.fromstring(xml_text)
    for element in root.iter():
        if element.tag.endswith("loc") and element.text:
            value = element.text.strip()
            if value:
                yield value


def parse_sitemap_url(sitemap_url: str, max_depth: int = 2, timeout_seconds: int = 30) -> List[str]:
    visited: Set[str] = set()
    urls: List[str] = []

    def walk(current_url: str, depth: int) -> None:
        if current_url in visited or depth > max_depth:
            return
        visited.add(current_url)

        response = httpx.get(current_url, timeout=timeout_seconds, follow_redirects=True)
        response.raise_for_status()

        locs = list(_iter_locs(response.text))
        if not locs:
            return

        # If this sitemap points to other sitemap files, follow those.
        nested_sitemaps = [
            loc for loc in locs if loc.lower().endswith(".xml") or "sitemap" in loc.lower()
        ]
        if nested_sitemaps:
            for nested in nested_sitemaps:
                walk(nested, depth + 1)
        else:
            urls.extend(locs)

    walk(sitemap_url, 0)
    return urls
