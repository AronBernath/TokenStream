import httpx
from pathlib import Path
import hashlib
import logging
import os
import time

from common.object_storage import S3ObjectStorage

logger = logging.getLogger("ingestion-worker.fetchers")

FETCH_TIMEOUT_SECONDS = float(os.environ.get("FETCH_TIMEOUT_SECONDS", "120") or "120")
FETCH_MAX_ATTEMPTS = max(1, int(os.environ.get("FETCH_MAX_ATTEMPTS", "5") or "5"))
FETCH_RETRY_BACKOFF_SECONDS = max(
    0.0,
    float(os.environ.get("FETCH_RETRY_BACKOFF_SECONDS", "2") or "2"),
)
FETCH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "hu,en;q=0.8",
    "User-Agent": os.environ.get(
        "FETCH_USER_AGENT",
        "Mozilla/5.0 (compatible; research-agent-ingestion/0.1; +https://github.com/openai)",
    ),
}


def _safe_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _fetch_url(url: str) -> httpx.Response:
    last_response: httpx.Response | None = None
    for attempt in range(1, FETCH_MAX_ATTEMPTS + 1):
        response = httpx.get(
            url,
            timeout=FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=FETCH_HEADERS,
        )
        last_response = response
        response.raise_for_status()
        if response.status_code == 202:
            logger.warning(
                "Remote source returned 202 Accepted attempt=%d/%d bytes=%d waf_action=%s cache=%s url=%s",
                attempt,
                FETCH_MAX_ATTEMPTS,
                len(response.content),
                response.headers.get("x-amzn-waf-action"),
                response.headers.get("x-cache"),
                url,
            )
            if attempt < FETCH_MAX_ATTEMPTS:
                time.sleep(FETCH_RETRY_BACKOFF_SECONDS * attempt)
                continue
            preview = response.text[:200].replace("\n", "\\n")
            raise ValueError(
                f"Remote source returned 202 Accepted after {FETCH_MAX_ATTEMPTS} attempts for {url}; "
                f"content-type={(response.headers.get('content-type') or '').lower()!r} "
                f"waf_action={response.headers.get('x-amzn-waf-action')!r} "
                f"cache={response.headers.get('x-cache')!r} preview={preview!r}"
            )
        return response
    assert last_response is not None
    return last_response


def _validate_remote_response(src: dict, fmt: str, url: str, response: httpx.Response) -> None:
    content_type = (response.headers.get("content-type") or "").lower()
    if fmt not in {"xlsx", "pdf"} and response.content[:4] == b"%PDF":
        raise ValueError(
            f"Source {src.get('id', url)} looks like a PDF but is configured as {fmt!r}. "
            "Configure this source with format: pdf."
        )
    if fmt == "xlsx" and len(response.content) < 256:
        preview = response.text[:200].replace("\n", "\\n")
        raise ValueError(
            f"Suspiciously small XLSX payload ({len(response.content)} bytes) for {url}; "
            f"content-type={content_type!r} preview={preview!r}"
        )
    if fmt == "pdf":
        if len(response.content) < 256:
            raise ValueError(
                f"Suspiciously small PDF payload ({len(response.content)} bytes) for {url}; "
                f"content-type={content_type!r}"
            )
        if response.content[:4] != b"%PDF" and "pdf" not in content_type:
            preview = response.text[:200].replace("\n", "\\n")
            raise ValueError(
                f"Source {src.get('id', url)} is configured as pdf but response does not look like a PDF; "
                f"content-type={content_type!r} preview={preview!r}"
            )
    if fmt in {"html", "yaml", "markdown", "md", "text"}:
        text = response.text or ""
        if len(text.strip()) < 32:
            preview = text[:200].replace("\n", "\\n")
            raise ValueError(
                f"Suspiciously small {fmt} payload ({len(text)} chars) for {url}; "
                f"content-type={content_type!r} preview={preview!r}"
            )


def fetch_source(src: dict, data_dir: str = "/data") -> dict:
    """
    Returns a dict with:
      - format: "xlsx", "html", "pdf", "yaml", "markdown", "md", or "text"
      - content: str for text formats, bytes for xlsx/pdf
      - url (or file path)
      - local_path (where raw asset saved)
    """
    src_type = src.get("type", "url")
    fmt = src.get("format", "html")
    raw_dir = Path(data_dir) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    text_suffix_map = {
        "html": ".html",
        "yaml": ".yaml",
        "markdown": ".md",
        "md": ".md",
        "text": ".txt",
        "json": ".json",
        "jsonl": ".jsonl",
    }
    binary_suffix_map = {
        "zip": ".zip",
        "tar": ".tar",
        "binary": ".bin",
    }

    if src_type == "object":
        object_uri = str(src.get("object_uri") or "").strip()
        if not object_uri.startswith("s3://"):
            raise ValueError(f"Unsupported object URI for {src.get('id', 'unknown')}: {object_uri}")
        content = S3ObjectStorage.from_env().get_bytes(object_uri)
        suffix = (
            ".xlsx"
            if fmt == "xlsx"
            else ".pdf"
            if fmt == "pdf"
            else binary_suffix_map.get(fmt, text_suffix_map.get(fmt, ".txt"))
        )
        local_path = raw_dir / f"{src['id']}_{_safe_id(object_uri)}{suffix}"
        if fmt in {"xlsx", "pdf"}:
            local_path.write_bytes(content)
            if fmt == "pdf" and not content.startswith(b"%PDF"):
                logger.warning(
                    "Object source %s is configured as pdf but does not start with %%PDF: %s",
                    src.get("id", object_uri),
                    object_uri,
                )
            return {
                "format": fmt,
                "content": content,
                "url": object_uri,
                "local_path": str(local_path),
                "content_hash": hashlib.sha256(content).hexdigest(),
            }
        if fmt in text_suffix_map:
            try:
                text = content.decode(src.get("encoding", "utf-8"))
            except UnicodeDecodeError:
                text = content.decode("utf-8-sig")
            if len(text.strip()) < 32:
                raise ValueError(f"Suspiciously small object {fmt} payload ({len(text)} chars) for {object_uri}")
            local_path.write_text(text, encoding="utf-8")
            return {
                "format": fmt,
                "content": text,
                "url": object_uri,
                "local_path": str(local_path),
                "content_hash": hashlib.sha256(content).hexdigest(),
            }
        if fmt in binary_suffix_map:
            local_path.write_bytes(content)
            return {
                "format": fmt,
                "content": content,
                "url": object_uri,
                "local_path": str(local_path),
                "content_hash": hashlib.sha256(content).hexdigest(),
            }
        raise ValueError(f"Unsupported object source format: {fmt}")

    if src_type != "url":
        raise ValueError(f"Unknown source type: {src_type}")

    url = str(src["url"]).strip()
    r = _fetch_url(url)
    _validate_remote_response(src, fmt, url, r)
    content_type = (r.headers.get("content-type") or "").lower()

    suffix = (
        ".xlsx"
        if fmt == "xlsx"
        else ".pdf"
        if fmt == "pdf"
        else binary_suffix_map.get(fmt, text_suffix_map.get(fmt, ".txt"))
    )
    local_path = raw_dir / f"{src['id']}_{_safe_id(url)}{suffix}"

    if fmt == "xlsx":
        local_path.write_bytes(r.content)
        logger.info(
            "Fetched %s as xlsx status=%d bytes=%d content-type=%s path=%s",
            src.get("id", url),
            r.status_code,
            len(r.content),
            content_type,
            local_path,
        )
        content_hash = hashlib.sha256(r.content).hexdigest()
        return {
            "format": "xlsx",
            "content": r.content,
            "url": url,
            "local_path": str(local_path),
            "content_hash": content_hash,
        }

    if fmt == "pdf":
        local_path.write_bytes(r.content)
        logger.info(
            "Fetched %s as pdf status=%d bytes=%d content-type=%s path=%s",
            src.get("id", url),
            r.status_code,
            len(r.content),
            content_type,
            local_path,
        )
        content_hash = hashlib.sha256(r.content).hexdigest()
        return {
            "format": "pdf",
            "content": r.content,
            "url": url,
            "local_path": str(local_path),
            "content_hash": content_hash,
        }

    if fmt in text_suffix_map:
        text = r.text or ""
        local_path.write_text(text, encoding="utf-8")
        logger.info(
            "Fetched %s as %s status=%d chars=%d content-type=%s path=%s",
            src.get("id", url),
            fmt,
            r.status_code,
            len(text),
            content_type,
            local_path,
        )
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return {"format": fmt, "content": text, "url": url, "local_path": str(local_path), "content_hash": content_hash}

    if fmt in binary_suffix_map:
        local_path.write_bytes(r.content)
        logger.info(
            "Fetched %s as %s status=%d bytes=%d content-type=%s path=%s",
            src.get("id", url),
            fmt,
            r.status_code,
            len(r.content),
            content_type,
            local_path,
        )
        content_hash = hashlib.sha256(r.content).hexdigest()
        return {
            "format": fmt,
            "content": r.content,
            "url": url,
            "local_path": str(local_path),
            "content_hash": content_hash,
        }

    raise ValueError(f"Unsupported remote source format: {fmt}")
