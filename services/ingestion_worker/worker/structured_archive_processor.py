from __future__ import annotations

import fnmatch
import hashlib
import io
import tarfile
import zipfile
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote

from common.models import Chunk

PROCESSOR_NAME = "structured-archive"
PROCESSOR_VERSION = "structured-archive-v1"

DEFAULT_EXCLUDE = [
    ".git/**",
    "**/.git/**",
    "node_modules/**",
    "**/node_modules/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    ".venv/**",
    "**/.venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    ".mypy_cache/**",
    "**/.mypy_cache/**",
    ".ruff_cache/**",
    "**/.ruff_cache/**",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*.pyc",
    "*.pyo",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.pdf",
    "*.zip",
    "*.tar",
    "*.gz",
]

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".json": "json",
    ".jsonl": "jsonl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".md": "markdown",
    ".mdx": "mdx",
    ".rst": "rst",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "shell",
    ".ps1": "powershell",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
}

CONFIG_FILENAMES = {
    "dockerfile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "pnpm-lock.yaml",
    "vite.config.ts",
    "vite.config.js",
    "tsconfig.json",
}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _as_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(parsed, minimum)


def _safe_path(path: str) -> str | None:
    cleaned = str(path or "").replace("\\", "/").strip("/")
    if not cleaned:
        return None
    pure = PurePosixPath(cleaned)
    if any(part in {"", ".", ".."} for part in pure.parts):
        return None
    return pure.as_posix()


def _matches(path: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        text = str(pattern or "").strip()
        if text and (fnmatch.fnmatch(path, text) or fnmatch.fnmatch(f"/{path}", text)):
            return True
    return False


def _should_include(path: str, include: list[str], exclude: list[str]) -> bool:
    if include and not _matches(path, include):
        return False
    return not _matches(path, exclude)


def _iter_archive_members(raw: dict[str, Any]) -> Iterable[tuple[str, bytes]]:
    content = raw.get("content")
    if not isinstance(content, bytes):
        raise ValueError("structured_archive processor requires byte content from a zip or tar source")

    fmt = str(raw.get("format") or "").strip().lower()
    if fmt == "zip" or zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename):
                if info.is_dir():
                    continue
                safe_path = _safe_path(info.filename)
                if not safe_path:
                    continue
                yield safe_path, archive.read(info)
        return

    if fmt == "tar" or tarfile.is_tarfile(raw.get("local_path") or ""):
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            for member in sorted(archive.getmembers(), key=lambda item: item.name):
                if not member.isfile():
                    continue
                safe_path = _safe_path(member.name)
                if not safe_path:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                yield safe_path, extracted.read()
        return

    raise ValueError(f"structured_archive processor does not support format: {fmt or '<unknown>'}")


def _decode_text(data: bytes) -> str | None:
    if b"\x00" in data[:4096]:
        return None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    if not text.strip():
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _language(path: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    if name == "dockerfile":
        return "dockerfile"
    return LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())


def _source_kind(path: str, language: str | None) -> str:
    lower = path.lower()
    name = PurePosixPath(lower).name
    parts = set(PurePosixPath(lower).parts)
    if "test" in parts or "tests" in parts or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if "docs" in parts or "doc" in parts or name.startswith("readme") or language in {"markdown", "mdx", "rst"}:
        return "docs"
    if "schema" in parts or "schemas" in parts or "openapi" in name:
        return "schema"
    if name in CONFIG_FILENAMES or language in {"yaml", "toml", "ini", "json"}:
        return "config"
    if language:
        return "code"
    return "text"


def _line_windows(text: str, max_chunk_chars: int) -> Iterable[tuple[int, int, str]]:
    lines = text.splitlines()
    current: list[str] = []
    start_line = 1
    current_len = 0
    for index, line in enumerate(lines, start=1):
        added_len = len(line) + (1 if current else 0)
        if current and current_len + added_len > max_chunk_chars:
            yield start_line, index - 1, "\n".join(current).strip()
            current = []
            current_len = 0
            start_line = index
        current.append(line)
        current_len += added_len
    if current:
        yield start_line, len(lines) or start_line, "\n".join(current).strip()


def _source_url(base: str | None, path: str, start_line: int, end_line: int) -> str | None:
    if not base:
        return None
    separator = "&" if "#" in base else "#"
    return f"{base}{separator}path={quote(path)}&L{start_line}-L{end_line}"


def _chunk_id(corpus_id: str, source_id: str, path: str, start_line: int, end_line: int, text: str) -> str:
    payload = (
        f"{corpus_id}:{source_id}:{path}:{start_line}:{end_line}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def process_structured_archive(context: Any) -> dict[str, Any]:
    config = context.processor_config or {}
    include = _as_list(config.get("include"))
    exclude = [*DEFAULT_EXCLUDE, *_as_list(config.get("exclude"))]
    max_file_bytes = _as_int(config.get("max_file_bytes"), 400_000)
    max_files = _as_int(config.get("max_files"), 5000)
    max_chunk_chars = _as_int(config.get("max_chunk_chars"), 6000)
    metadata_defaults = config.get("metadata_defaults") if isinstance(config.get("metadata_defaults"), dict) else {}

    corpus_id = str(context.corpus.get("corpus_id") or "")
    source_id = str(context.source.get("id") or context.source.get("source_id") or context.source.get("doc_id") or "")
    base_url = context.raw.get("url") or context.source.get("url") or context.source.get("object_uri")
    source_metadata = context.source.get("metadata") if isinstance(context.source.get("metadata"), dict) else {}

    chunks: list[Chunk] = []
    files_seen = 0
    files_indexed = 0
    files_skipped = 0

    for path, data in _iter_archive_members(context.raw):
        files_seen += 1
        if files_seen > max_files:
            files_skipped += 1
            continue
        if not _should_include(path, include, exclude):
            files_skipped += 1
            continue
        if len(data) > max_file_bytes:
            files_skipped += 1
            continue
        text = _decode_text(data)
        if text is None:
            files_skipped += 1
            continue

        files_indexed += 1
        language = _language(path)
        source_kind = _source_kind(path, language)
        for ordinal, (start_line, end_line, chunk_text) in enumerate(_line_windows(text, max_chunk_chars), start=1):
            if not chunk_text:
                continue
            metadata = {
                **metadata_defaults,
                **source_metadata,
                "path": path,
                "language": language,
                "source_kind": source_kind,
                "start_line": start_line,
                "end_line": end_line,
                "file_size_bytes": len(data),
                "archive_source_id": source_id,
                "parser_name": PROCESSOR_NAME,
                "parser_version": PROCESSOR_VERSION,
                "doc_type": source_kind,
                "tags": context.source.get("tags", []),
            }
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(corpus_id, source_id, path, start_line, end_line, chunk_text),
                    corpus_id=corpus_id,
                    doc_id=path,
                    title=path,
                    section_id=f"file:{path}:chunk:{ordinal}",
                    version_date=context.version_date,
                    language=language,
                    jurisdiction=None,
                    source_url=_source_url(base_url, path, start_line, end_line),
                    text=chunk_text,
                    metadata=metadata,
                )
            )

    return {
        "chunks": chunks,
        "blocks_parsed": files_indexed,
        "stats": {
            "parser_name": PROCESSOR_NAME,
            "parser_version": PROCESSOR_VERSION,
            "files_seen": files_seen,
            "files_indexed": files_indexed,
            "files_skipped": files_skipped,
        },
    }
