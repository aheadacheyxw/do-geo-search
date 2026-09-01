"""Step 6 public-source capture with content-minimising structural evidence."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FetchResult = tuple[str, int, str, bytes]


class _StructureParser(HTMLParser):
    """Classify page components without retaining page prose or headings."""

    def __init__(self) -> None:
        super().__init__()
        self.components: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_text = " ".join(f"{key}={value or ''}" for key, value in attrs).casefold()
        if tag in {"ol", "ul"}:
            self.components.add("步骤")
        if tag == "table":
            self.components.add("选项比较")
        if tag in {"details", "summary"} or "faq" in attr_text:
            self.components.add("FAQ")
        if tag in {"figure", "svg", "video"} or "chart" in attr_text or "diagram" in attr_text:
            self.components.add("图示")
        if tag == "form" or "cta" in attr_text or "contact" in attr_text:
            self.components.add("CTA")
        if tag in {"blockquote", "article"} and ("case" in attr_text or "案例" in attr_text):
            self.components.add("案例")


def _default_fetcher(url: str) -> FetchResult:
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        with urlopen(request, timeout=15) as response:  # nosec B310 - URL originates from visible source-card evidence
            return (
                response.geturl(),
                int(response.status),
                str(response.headers.get_content_type()),
                response.read(2_000_000),
            )
    except URLError:
        # Some managed desktop runtimes allow the system HTTP client while
        # Python's resolver remains sandboxed. Curl is invoked without a shell,
        # with the same read-only limits, so verified source URLs remain data.
        return _curl_fetcher(url)


def _curl_fetcher(url: str) -> FetchResult:
    curl = shutil.which("curl")
    if not curl:
        raise URLError("curl is unavailable")
    with tempfile.TemporaryDirectory(prefix="geo-source-") as temp_dir:
        body_path = Path(temp_dir) / "body"
        result = subprocess.run(  # nosec B603 - fixed executable and argv; no shell
            [
                curl,
                "--location",
                "--silent",
                "--show-error",
                "--max-time",
                "15",
                "--max-filesize",
                "2000000",
                "--user-agent",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36",
                "--header",
                "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "--header",
                "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
                "--output",
                str(body_path),
                "--write-out",
                "%{url_effective}\n%{http_code}\n%{content_type}",
                url,
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or f"curl exited {result.returncode}"
            raise URLError(message)
        metadata = result.stdout.splitlines()
        if len(metadata) < 3:
            raise URLError("curl response metadata is incomplete")
        final_url, status_text, content_type = metadata[-3:]
        try:
            status = int(status_text)
        except ValueError as exc:
            raise URLError("curl returned an invalid HTTP status") from exc
        body = body_path.read_bytes()[:2_000_000] if body_path.exists() else b""
        return final_url, status, content_type or "application/octet-stream", body


def capture_verified_sources(
    topology: Sequence[Mapping[str, object]],
    *,
    fetcher: Callable[[str], FetchResult] = _default_fetcher,
    captured_at: str,
    max_workers: int = 8,
) -> list[dict[str, object]]:
    """Capture only visible cited pages and emit structural, non-prose evidence.

    The source URL must already be a verified visible source-card item.  The
    capture contains neither full HTML nor extracted body text: a fingerprint,
    access state, and normalized component names are enough for Step 6.
    """

    rows: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for row in topology:
        url = str(row.get("url") or "")
        if not url or url in seen or row.get("access_state") != "visible_verified":
            continue
        seen.add(url)
        rows.append(row)

    def capture_one(item: tuple[int, Mapping[str, object]]) -> dict[str, object]:
        index, row = item
        url = str(row.get("url") or "")
        record: dict[str, object] = {
            "capture_id": f"source-capture-{index}",
            "url": url,
            "source_domain": row.get("source_domain"),
            "source_publisher": row.get("source_publisher"),
            "question_id": row.get("question_id"),
            "observation_id": row.get("observation_id"),
            "evidence_id": row.get("evidence_id"),
            "captured_at": captured_at,
            "capture_method": "public_http_readonly",
            "source_access": "http_error",
            "final_url": None,
            "http_status": None,
            "content_type": None,
            "content_fingerprint_sha256": None,
            "observed_structure": [],
        }
        try:
            final_url, status, content_type, body = fetcher(url)
            record.update(final_url=final_url, http_status=status, content_type=content_type)
            if status < 200 or status >= 300:
                record["source_access"] = "http_error"
            elif "html" not in content_type.casefold():
                record["source_access"] = "not_html"
            else:
                parser = _StructureParser()
                parser.feed(body.decode("utf-8", errors="replace"))
                record.update(
                    source_access="accessible",
                    content_fingerprint_sha256=hashlib.sha256(body).hexdigest(),
                    observed_structure=sorted(parser.components),
                )
        except HTTPError as exc:
            record.update(source_access="http_error", http_status=exc.code, access_error=f"HTTPError:{exc.code}")
        except URLError as exc:  # network/WAF failures are evidence limitations, not content facts
            record.update(source_access="blocked", access_error=f"URLError:{exc.reason}")
        except Exception as exc:
            record.update(source_access="blocked", access_error=type(exc).__name__)
        return record

    worker_count = max(1, int(max_workers))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(capture_one, enumerate(rows, 1)))
