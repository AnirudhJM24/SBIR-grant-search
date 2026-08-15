"""Minimal HTTP helpers (stdlib only, no third-party dependency).

Federal endpoints are slow, rate-limited and intermittently unavailable, so
everything here retries with exponential backoff and honours ``Retry-After``.
"""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = "sbirgrantsearch/0.1 (public-innovation-search; research use)"
DEFAULT_TIMEOUT = 60
# Transient statuses. 403 is included because SBIR.gov currently returns it
# while its API is under maintenance -- retrying costs little and the
# endpoint is expected to come back.
RETRY_STATUSES = frozenset({403, 408, 425, 429, 500, 502, 503, 504})


class HttpError(RuntimeError):
    """Non-retryable HTTP failure, or retries exhausted."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _sleep_for(attempt: int, retry_after: str | None, base: float) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 120.0)
        except ValueError:
            pass
    return min(base * (2**attempt), 60.0) + random.uniform(0, 0.5)


def request(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 4,
    backoff: float = 1.5,
) -> bytes:
    """Perform an HTTP request with retries, returning the response body."""
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, doseq=True
        )
        url = f"{url}{'&' if '?' in url else '?'}{query}"

    data = None
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    if headers:
        all_headers.update(headers)

    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read(2000).decode("utf-8", "replace")
            last = HttpError(f"HTTP {exc.code} for {url}: {body[:200]}",
                             status=exc.code, body=body)
            if exc.code not in RETRY_STATUSES or attempt == retries:
                raise last from exc
            delay = _sleep_for(attempt, exc.headers.get("Retry-After"), backoff)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == retries:
                raise HttpError(f"Network error for {url}: {exc}") from exc
            delay = _sleep_for(attempt, None, backoff)

        log.warning("retry %d/%d in %.1fs: %s", attempt + 1, retries, delay, last)
        time.sleep(delay)

    raise HttpError(f"Request failed for {url}: {last}")


def get_json(url: str, **kwargs: Any) -> Any:
    """GET a URL and parse JSON."""
    body = request(url, **kwargs)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise HttpError(
            f"Expected JSON from {url}, got {body[:200]!r}"
        ) from exc


def download(
    url: str,
    dest: Path | str,
    *,
    chunk_size: int = 1 << 20,
    timeout: int = 600,
    progress_every: int = 50 << 20,
) -> Path:
    """Stream a large file to disk.

    Downloads to ``dest.part`` and renames on completion so an interrupted
    download is never mistaken for a finished one.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    downloaded = 0
    next_report = progress_every

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            log.info("downloading %s (%s)", url, f"{total / 1e6:.0f} MB" if total else "size unknown")
            with part.open("wb") as fh:
                while chunk := response.read(chunk_size):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if downloaded >= next_report:
                        pct = f" ({downloaded / total:.0%})" if total else ""
                        log.info("  %.0f MB%s", downloaded / 1e6, pct)
                        next_report += progress_every
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    part.replace(dest)
    log.info("saved %s (%.0f MB)", dest, downloaded / 1e6)
    return dest
