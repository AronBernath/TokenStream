from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("orchestrator-api.pipeline")


def _canonical_tool_name(name: str) -> str:
    return (name or "").strip().lower().replace(".", "__")


def _as_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            norm = item.strip()
            if norm:
                out.append(norm)
    return out


def _as_dict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if k}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    return bool(value)


def is_tool_allowed(tool_name: str, allowed_tools: Sequence[str] | None) -> bool:
    if allowed_tools is None:
        return True
    if not allowed_tools:
        return False

    canonical_tool = _canonical_tool_name(tool_name)
    canonical_allowed = [_canonical_tool_name(x) for x in allowed_tools if isinstance(x, str)]
    if not canonical_allowed:
        return False

    for rule in canonical_allowed:
        if rule in {"*", "all", ".*", ""}:
            return True

        if rule == "rag" and canonical_tool.startswith("rag__"):
            return True

        if rule == "rag__query" and canonical_tool in {"rag__query", "rag.query"}:
            return True

        if rule.startswith("mcp__"):
            rest = rule[len("mcp__") :]
            if not rest:
                continue
            if "__" in rest:
                # Namespace + tool name
                if canonical_tool == rule:
                    return True
            else:
                # Namespace wildcard: allow all tools from this namespace
                if canonical_tool.startswith(rule + "__"):
                    return True
            continue

        if rule == canonical_tool or rule in {canonical_tool.replace("__", "."), f"mcp.{canonical_tool}"}:
            return True

    return False


@dataclass(frozen=True)
class ChunkingPolicy:
    enabled: bool = False
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    allowed_providers: Optional[Tuple[str, ...]] = None
    allowed_models: Optional[Tuple[str, ...]] = None
    target_chars: Optional[int] = None
    window_chars: Optional[int] = None
    window_overlap_chars: Optional[int] = None
    max_retries: Optional[int] = None

    @classmethod
    def from_mapping(cls, payload: Any) -> "ChunkingPolicy":
        data = _as_dict(payload)
        allowed_providers = data.get("allowed_providers")
        allowed_models = data.get("allowed_models")
        return cls(
            enabled=_as_bool(data.get("enabled"), False),
            default_provider=str(data.get("default_provider") or "").strip() or None,
            default_model=str(data.get("default_model") or "").strip() or None,
            allowed_providers=tuple(_as_list(allowed_providers)) if allowed_providers is not None else None,
            allowed_models=tuple(_as_list(allowed_models)) if allowed_models is not None else None,
            target_chars=data.get("target_chars") if isinstance(data.get("target_chars"), int) else None,
            window_chars=data.get("window_chars") if isinstance(data.get("window_chars"), int) else None,
            window_overlap_chars=data.get("window_overlap_chars")
            if isinstance(data.get("window_overlap_chars"), int)
            else None,
            max_retries=data.get("max_retries") if isinstance(data.get("max_retries"), int) else None,
        )


@dataclass(frozen=True)
class PipelinePolicy:
    pipeline_id: str
    default_corpus_id: str | None
    allowed_corpus_ids: Tuple[str, ...]
    default_filters: Dict[str, Any]
    allowed_tools: Tuple[str, ...]
    allowed_providers: Optional[Tuple[str, ...]] = None
    allowed_models: Optional[Tuple[str, ...]] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    chunking: ChunkingPolicy = field(default_factory=ChunkingPolicy)

    @classmethod
    def from_mapping(cls, pipeline_id: str, payload: Mapping[str, Any], fallback_corpus_id: str) -> "PipelinePolicy":
        if not isinstance(payload, Mapping):
            raise ValueError(f"pipeline '{pipeline_id}' must be an object")
        if "default_corpus_id" in payload:
            raw_default = payload.get("default_corpus_id")
            default_corpus_id = str(raw_default).strip() if raw_default is not None else None
        else:
            default_corpus_id = fallback_corpus_id

        allowed_corpus_ids = _as_list(payload.get("allowed_corpus_ids"))
        if not allowed_corpus_ids and default_corpus_id:
            allowed_corpus_ids = [default_corpus_id]
        if default_corpus_id and default_corpus_id not in allowed_corpus_ids:
            allowed_corpus_ids.append(default_corpus_id)

        allowed_tools = tuple(_as_list(payload.get("allowed_tools")))

        allowed_providers = payload.get("allowed_providers")
        allowed_models = payload.get("allowed_models")

        return cls(
            pipeline_id=pipeline_id,
            default_corpus_id=default_corpus_id,
            allowed_corpus_ids=tuple(sorted(set(allowed_corpus_ids))),
            default_filters=_as_dict(payload.get("default_filters")),
            allowed_tools=allowed_tools,
            allowed_providers=tuple(_as_list(allowed_providers)) if allowed_providers is not None else None,
            allowed_models=tuple(_as_list(allowed_models)) if allowed_models is not None else None,
            max_input_tokens=payload.get("max_input_tokens"),
            max_output_tokens=payload.get("max_output_tokens"),
            max_total_tokens=payload.get("max_total_tokens"),
            max_top_k=payload.get("max_top_k"),
            default_provider=payload.get("default_provider"),
            default_model=payload.get("default_model"),
            chunking=ChunkingPolicy.from_mapping(payload.get("chunking")),
        )


@dataclass(frozen=True)
class PipelineResolution:
    pipeline_id: str | None
    resolved_corpus_id: str | None
    effective_filters: Dict[str, Any]
    allowed_tools: Tuple[str, ...] | None
    allowed_corpus_ids: Tuple[str, ...]
    allowed_providers: Optional[Tuple[str, ...]] = None
    allowed_models: Optional[Tuple[str, ...]] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    max_total_tokens: Optional[int] = None
    max_top_k: Optional[int] = None
    chunking: ChunkingPolicy = field(default_factory=ChunkingPolicy)

    def enforce_corpus(self, requested_corpus_id: str | None) -> str:
        requested = str(requested_corpus_id or "").strip()
        if not requested:
            if not self.resolved_corpus_id:
                raise ValueError(f"pipeline '{self.pipeline_id}' does not allow corpus access")
            return self.resolved_corpus_id
        if requested not in self.allowed_corpus_ids:
            raise ValueError(f"corpus_id '{requested}' is not allowed for pipeline '{self.pipeline_id}'")
        return requested


@dataclass(frozen=True)
class PipelineRegistry:
    policies: Tuple[PipelinePolicy, ...]
    default_corpus_id: str
    default_filters: Dict[str, Any]

    @classmethod
    def load(cls, default_corpus_id: str) -> "PipelineRegistry":
        payload_str = (
            os.environ.get("ORCHESTRATOR_PIPELINE_REGISTRY_JSON")
            or os.environ.get("PIPELINE_REGISTRY_JSON")
            or os.environ.get("PIPELINE_REGISTRY")
            or ""
        ).strip()

        path = (
            os.environ.get("ORCHESTRATOR_PIPELINE_REGISTRY_PATH") or os.environ.get("PIPELINE_REGISTRY_PATH") or ""
        ).strip()

        payload: Mapping[str, Any] | None = None

        if payload_str:
            try:
                parsed = json.loads(payload_str)
                if not isinstance(parsed, Mapping):
                    raise ValueError("pipeline registry JSON must be an object")
                payload = parsed
            except Exception as exc:
                raise ValueError(f"Invalid pipeline registry JSON: {exc}") from exc
        elif path:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        parsed = json.load(fp)
                    if not isinstance(parsed, Mapping):
                        raise ValueError("pipeline registry file must contain an object")
                    payload = parsed
                except Exception as exc:
                    raise ValueError(f"Invalid pipeline registry file '{path}': {exc}") from exc
            else:
                logger.warning(
                    "pipeline_registry_snapshot_missing path=%s; starting with empty policy registry",
                    path,
                )

        policies: List[PipelinePolicy] = []
        if payload:
            for pid, data in payload.items():
                if not isinstance(pid, str) or not pid.strip():
                    raise ValueError("pipeline_id must be a non-empty string")
                policies.append(PipelinePolicy.from_mapping(pid.strip(), data, fallback_corpus_id=default_corpus_id))

        logger.info("loaded_pipeline_registry count=%d", len(policies))
        return cls(
            policies=tuple(policies),
            default_corpus_id=default_corpus_id,
            default_filters={},
        )

    @property
    def by_id(self) -> Dict[str, PipelinePolicy]:
        return {p.pipeline_id: p for p in self.policies}

    def resolve(
        self,
        *,
        pipeline_id: str | None,
        requested_corpus_id: str | None,
        requested_filters: Optional[Mapping[str, Any]],
    ) -> PipelineResolution:
        pid = (pipeline_id or "").strip() or None
        base_filters = dict(self.default_filters)
        allowed_tools: Tuple[str, ...] | None = None
        allowed_corpus_ids = (self.default_corpus_id,) if self.default_corpus_id else ()
        resolved_corpus_id: str | None = (requested_corpus_id or "").strip() or self.default_corpus_id or None

        allowed_providers = None
        allowed_models = None
        default_provider = None
        default_model = None
        max_input_tokens = None
        max_output_tokens = None
        max_total_tokens = None
        max_top_k = None
        chunking = ChunkingPolicy()

        if pid is None:
            if requested_corpus_id:
                resolved_corpus_id = requested_corpus_id.strip()
        else:
            policy = self.by_id.get(pid)
            if policy is None:
                raise ValueError(f"Unknown pipeline_id '{pid}'")
            requested = (requested_corpus_id or "").strip()
            if requested and requested not in policy.allowed_corpus_ids:
                raise ValueError(f"corpus_id '{requested}' is not allowed for pipeline '{pid}'")
            resolved_corpus_id = self._resolve_corpus(
                requested=requested_corpus_id,
                default_corpus_id=policy.default_corpus_id,
            )
            base_filters = dict(policy.default_filters)
            allowed_tools = policy.allowed_tools
            allowed_corpus_ids = policy.allowed_corpus_ids
            allowed_providers = policy.allowed_providers
            allowed_models = policy.allowed_models
            default_provider = policy.default_provider
            default_model = policy.default_model
            max_input_tokens = policy.max_input_tokens
            max_output_tokens = policy.max_output_tokens
            max_total_tokens = policy.max_total_tokens
            max_top_k = policy.max_top_k
            chunking = policy.chunking

        merged = base_filters
        for k, v in _as_dict(requested_filters).items():
            merged[str(k)] = v

        return PipelineResolution(
            pipeline_id=pid,
            resolved_corpus_id=resolved_corpus_id,
            effective_filters=merged,
            allowed_tools=allowed_tools,
            allowed_corpus_ids=allowed_corpus_ids,
            allowed_providers=allowed_providers,
            allowed_models=allowed_models,
            default_provider=default_provider,
            default_model=default_model,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_total_tokens=max_total_tokens,
            max_top_k=max_top_k,
            chunking=chunking,
        )

    def _resolve_corpus(self, *, requested: str | None, default_corpus_id: str | None) -> str | None:
        return (requested or "").strip() or default_corpus_id or None

    def merge_filters(self, defaults: Mapping[str, Any], requested: Mapping[str, Any] | None) -> Dict[str, Any]:
        merged = dict(defaults)
        for k, v in _as_dict(requested).items():
            merged[str(k)] = v
        return merged
