import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from common.llm.errors import LLMError
from common.llm.types import ChatMessage, GenerationParams, ToolChoiceFunction, ToolDefinition
from common.models import LookupRequest as RetrievalLookupRequest
from common.models import QueryRequest as RetrievalQueryRequest
from common.models import QueryResponse as RetrievalQueryResponse

from .config import load_settings
from .errors import ServiceError
from .logging_config import configure_logging
from .validation import normalize_response_format_for_provider, validate_response_format

configure_logging()


def _is_strict_schema_capable(provider_name: str, model: str) -> bool:
    """
    Returns True if the provider/model combination is known to support strict JSON schemas.
    """
    p = provider_name.lower()
    for pdef in settings.providers:
        if pdef.name == p:
            return pdef.capabilities.json_schema
    return False


from .logging_utils import bounded_log_payload, bounded_response_payload
from .models import OpenAIChatCompletionRequest, OpenAIChatMessage, RagLookupRequest, RagQueryRequest
from .provider_registry import build_provider_registry
from .mcp.registry import McpToolRegistry
from .mcp.settings import parse_mcp_servers, summarize_mcp_servers
from .rag_tools import RagTooling, _call_retrieval_api, _call_retrieval_lookup_api
from .pipeline import PipelineRegistry, PipelineResolution

settings = load_settings()
providers = build_provider_registry(settings)
rag_tooling = RagTooling(settings=settings)
pipeline_registry = PipelineRegistry.load(default_corpus_id=settings.default_corpus_id)

app = FastAPI(
    title="TokenStream API",
    version="1.0.0",
    description="OpenAI-compatible facade for provider routing and MCP tool orchestration.",
)
logger = logging.getLogger("orchestrator-api")

mcp_registry: Optional[McpToolRegistry] = None
reload_token = (os.environ.get("ORCHESTRATOR_RELOAD_TOKEN") or "").strip()


@app.on_event("startup")
async def _startup():
    global mcp_registry
    configure_logging()
    try:
        servers = parse_mcp_servers(settings.mcp_servers_json)
    except Exception as exc:
        if settings.mcp_strict:
            raise
        logger.warning("mcp_config_invalid error=%s", str(exc), exc_info=True)
        servers = []

    mcp_registry = McpToolRegistry(
        servers=servers,
        protocol_version=settings.mcp_protocol_version,
        timeout_s=settings.mcp_timeout_s,
        strict=settings.mcp_strict,
    )
    if mcp_registry.enabled:
        logger.info("mcp_starting %s", summarize_mcp_servers(servers))
        await mcp_registry.start()
        logger.info("mcp_ready tools=%d", len(mcp_registry.list_tool_names()))
    else:
        logger.info("mcp_disabled")


@app.on_event("shutdown")
async def _shutdown():
    global mcp_registry
    if mcp_registry is not None:
        await mcp_registry.close()
    mcp_registry = None


def _error_payload(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"error": {"code": code, "message": message}}
    if details is not None:
        payload["error"]["details"] = details
    return payload


from .auth import AuthContext, AuthRegistry

auth_registry = AuthRegistry.load(legacy_key=settings.service_api_key)


def reload_runtime_state() -> None:
    global settings, providers, rag_tooling, pipeline_registry, auth_registry
    settings = load_settings()
    providers = build_provider_registry(settings)
    rag_tooling = RagTooling(settings=settings)
    pipeline_registry = PipelineRegistry.load(default_corpus_id=settings.default_corpus_id)
    auth_registry = AuthRegistry.load(legacy_key=settings.service_api_key)


def require_auth_context(authorization: Optional[str] = Header(default=None)) -> Optional[AuthContext]:
    if not settings.service_api_key and not auth_registry._entries:
        raise HTTPException(status_code=503, detail={"error": "auth_not_configured"})

    if not authorization:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})

    ctx = auth_registry.authenticate(authorization)
    if not ctx:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})

    return ctx


def require_scope(scope: str):
    def dependency(ctx: Optional[AuthContext] = Depends(require_auth_context)) -> Optional[AuthContext]:
        if ctx and not ctx.has_scope(scope):
            raise HTTPException(status_code=403, detail={"error": f"forbidden: requires scope {scope}"})
        return ctx

    return dependency


def require_internal_reload_token(x_config_auth_token: Optional[str] = Header(default=None)) -> None:
    if not reload_token:
        raise HTTPException(status_code=503, detail={"error": "reload_not_configured"})
    if x_config_auth_token != reload_token:
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


@app.post("/v1/internal/reload", include_in_schema=False)
def internal_reload(_: None = Depends(require_internal_reload_token)) -> Dict[str, Any]:
    reload_runtime_state()
    return {
        "ok": True,
        "providers": {
            pdef.name: {
                "models": list(pdef.models),
                "chunking": pdef.capabilities.chunking,
                "json_schema": pdef.capabilities.json_schema,
            }
            for pdef in settings.providers
        },
    }


def _openai_error_payload(
    message: str,
    *,
    error_type: str = "invalid_request_error",
    code: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"error": {"message": message, "type": error_type}}
    if code is not None:
        payload["error"]["code"] = code
    return payload


def _parse_provider_model_selector(model: str) -> tuple[Optional[str], str]:
    """
    Allow OpenAI-compatible clients to select a provider by encoding it into the model id:
    "<provider>:<model>" (e.g. "openai:gpt-4o-mini").
    """
    raw = (model or "").strip()
    if not raw:
        return None, raw
    if ":" not in raw:
        return None, raw
    provider_name, provider_model = raw.split(":", 1)
    return provider_name.strip().lower() or None, provider_model.strip()


def _should_retry_without_tools(exc: LLMError) -> bool:
    if exc.code != "provider_error":
        return False
    details = exc.details if isinstance(exc.details, dict) else {}
    status = details.get("upstream_status")
    if not isinstance(status, int):
        return False
    # 4xx validation failures are frequently caused by tool schema/size mismatches.
    if status not in {400, 404, 413, 422}:
        return False
    body = str(details.get("upstream_body") or "").lower()
    if not body:
        return True
    hints = (
        "tool",
        "function",
        "tool_choice",
        "invalid_request_error",
        "maximum context length",
        "context_length",
        "too many tokens",
    )
    return any(h in body for h in hints)


def _resolve_unique_provider_from_model(
    model: str,
    *,
    allowed_providers: Sequence[str] | None = None,
) -> tuple[Optional[str], bool]:
    raw = (model or "").strip()
    if not raw:
        return None, False

    matches: List[str] = []
    for provider_name in providers:
        if allowed_providers is not None and provider_name not in allowed_providers:
            continue
        listed = _listed_models_for_provider(provider_name)
        for known in listed:
            if raw == known or raw.startswith(known + "-"):
                matches.append(provider_name)
                break

    deduped = sorted(set(matches))
    if len(deduped) == 1:
        return deduped[0], False
    if len(deduped) > 1:
        return None, True
    return None, False


def _resolve_provider_and_model(
    requested_model: str,
    *,
    pipeline_ctx: PipelineResolution,
    task: str | None = None,
) -> tuple[str, str]:
    req_provider, req_model = _parse_provider_model_selector(requested_model)
    is_chunking = (task or "").strip().lower() == "chunking"
    allowed_for_model_inference = (
        pipeline_ctx.chunking.allowed_providers
        if is_chunking and pipeline_ctx.chunking.allowed_providers is not None
        else pipeline_ctx.allowed_providers
    )

    if req_provider:
        if not req_model:
            raise ServiceError(
                code="model_resolution_failed",
                message=f"Provider '{req_provider}' was specified without a model",
                status_code=422,
                details={"provider": req_provider},
            )
        return req_provider, req_model

    if req_model:
        inferred_provider, ambiguous = _resolve_unique_provider_from_model(
            req_model,
            allowed_providers=allowed_for_model_inference,
        )
        if ambiguous:
            raise ServiceError(
                code="ambiguous_provider",
                message=f"Model '{req_model}' matches multiple providers; specify provider:model explicitly",
                status_code=422,
            )
        if inferred_provider:
            return inferred_provider, req_model
        raise ServiceError(
            code="provider_resolution_failed",
            message=f"Could not resolve provider for model '{req_model}'",
            status_code=422,
            details={"model": req_model},
        )

    if is_chunking:
        if not pipeline_ctx.chunking.enabled:
            raise ServiceError(
                code="chunking_policy_disabled",
                message="Chunking is not enabled by the selected policy",
                status_code=403,
            )
        policy_provider = (pipeline_ctx.chunking.default_provider or "").strip().lower()
        policy_model = (pipeline_ctx.chunking.default_model or "").strip()
    else:
        policy_provider = (pipeline_ctx.default_provider or "").strip().lower()
        policy_model = (pipeline_ctx.default_model or "").strip()
    if policy_provider and policy_model:
        return policy_provider, policy_model

    raise ServiceError(
        code="provider_resolution_failed",
        message="No provider/model specified and no policy default is available",
        status_code=422,
    )


def _listed_models_for_provider(provider_name: str) -> List[str]:
    for pdef in settings.providers:
        if pdef.name == provider_name:
            return list(pdef.models)
    return []


def _get_provider_definition(provider_name: str):
    for pdef in settings.providers:
        if pdef.name == provider_name:
            return pdef
    return None


def _resolve_generation_controls(
    *,
    provider_name: str,
    body: OpenAIChatCompletionRequest,
    pipeline_ctx: PipelineResolution,
) -> tuple[Optional[float], Optional[int], Optional[int]]:
    provider_def = _get_provider_definition(provider_name)
    if provider_def is None:
        raise ServiceError(
            code="invalid_provider",
            message="Unsupported provider",
            status_code=422,
            details={"provider": provider_name},
        )

    controls = provider_def.client_controls
    provided_controls = {
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
    }
    for field_name, value in provided_controls.items():
        if value is None:
            continue
        if not getattr(controls, field_name):
            raise ServiceError(
                code="unsupported_client_control",
                message=f"Provider '{provider_name}' does not allow client-supplied '{field_name}'",
                status_code=400,
                details={"provider": provider_name, "field": field_name},
            )

    req_max_tokens = body.max_tokens
    if pipeline_ctx.max_output_tokens is not None:
        if req_max_tokens is None or req_max_tokens > pipeline_ctx.max_output_tokens:
            req_max_tokens = pipeline_ctx.max_output_tokens

    context_limit_candidates = [
        provider_def.capabilities.max_context_window,
        pipeline_ctx.max_input_tokens,
        pipeline_ctx.max_total_tokens,
    ]
    context_limits = [int(value) for value in context_limit_candidates if isinstance(value, int) and value > 0]
    requested_context_length = body.context_length
    req_context_length: Optional[int] = None
    if controls.context_length:
        provider_default_context = provider_def.capabilities.default_context_window
        if requested_context_length is not None:
            req_context_length = requested_context_length
        elif isinstance(provider_default_context, int) and provider_default_context > 0:
            req_context_length = provider_default_context
        else:
            req_context_length = provider_def.capabilities.max_context_window

    if req_context_length is not None and context_limits:
        req_context_length = min(req_context_length, *context_limits)

    logger.debug(
        "chat_completion_context_resolution %s",
        bounded_log_payload(
            provider=provider_name,
            client_context_allowed=controls.context_length,
            requested_context_length=requested_context_length,
            default_context_window=provider_def.capabilities.default_context_window,
            max_context_window=provider_def.capabilities.max_context_window,
            pipeline_max_input_tokens=pipeline_ctx.max_input_tokens,
            pipeline_max_total_tokens=pipeline_ctx.max_total_tokens,
            resolved_context_length=req_context_length,
            context_length_param=controls.context_length_param,
            max_chars=600,
        ),
    )

    return body.temperature, req_max_tokens, req_context_length


def _as_openai_chat_response(
    *, model: str, content: str, usage: Dict[str, Any], parsed: Optional[Any] = None
) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if parsed is not None:
        message["parsed"] = parsed

    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


def _content_chunks(text: str, chunk_size: int = 28) -> Iterable[str]:
    for idx in range(0, len(text), chunk_size):
        yield text[idx : idx + chunk_size]


def _as_openai_stream_chunk(model: str, completion_id: str, delta: Dict[str, Any], finish_reason: Optional[str]) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _to_provider_messages(messages: Sequence[OpenAIChatMessage]) -> List[ChatMessage]:
    out: List[ChatMessage] = []
    for m in messages:
        role = (m.role or "").strip()
        if not role:
            continue
        out.append(
            ChatMessage(
                role=role,  # provider adapters accept OpenAI-like roles
                content=m.content if m.content is not None else "",
                name=m.name,
                tool_call_id=m.tool_call_id,
            )
        )
    return out


def _client_tool_definitions_for_chunking(body: OpenAIChatCompletionRequest) -> List[ToolDefinition]:
    if (body.task or "").strip().lower() != "chunking" or not body.tools:
        return []

    out: List[ToolDefinition] = []
    for tool in body.tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        parameters = fn.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {}
        description = fn.get("description")
        out.append(
            ToolDefinition(
                name=name,
                description=description if isinstance(description, str) else None,
                parameters=parameters,
            )
        )
    return out


def _client_tool_choice_for_chunking(body: OpenAIChatCompletionRequest):
    if (body.task or "").strip().lower() != "chunking":
        return None
    choice = body.tool_choice
    if isinstance(choice, dict):
        fn = choice.get("function") if isinstance(choice.get("function"), dict) else {}
        name = fn.get("name")
        if choice.get("type") == "function" and isinstance(name, str) and name:
            return ToolChoiceFunction(name=name)
    if isinstance(choice, str) and choice in {"auto", "none"}:
        return choice
    return None


def _resolve_pipeline_context(
    *,
    pipeline_id: str | None,
    requested_corpus_id: str | None,
    requested_filters: Dict[str, Any] | None,
    auth_ctx: Optional[AuthContext] = None,
    task: str | None = None,
) -> PipelineResolution:
    try:
        # Auth context default pipeline overrides if none requested
        if not pipeline_id and auth_ctx and auth_ctx.default_pipeline_id:
            pipeline_id = auth_ctx.default_pipeline_id
        if not pipeline_id and (task or "").strip().lower() == "chunking" and "default" in pipeline_registry.by_id:
            pipeline_id = "default"

        resolution = pipeline_registry.resolve(
            pipeline_id=pipeline_id,
            requested_corpus_id=requested_corpus_id,
            requested_filters=requested_filters,
        )

        # Merge auth context limits (intersection)
        if auth_ctx:
            allowed_providers = resolution.allowed_providers
            if auth_ctx.allowed_providers is not None:
                if allowed_providers is None:
                    allowed_providers = auth_ctx.allowed_providers
                else:
                    allowed_providers = tuple(p for p in allowed_providers if p in auth_ctx.allowed_providers)

            allowed_models = resolution.allowed_models
            if auth_ctx.allowed_models is not None:
                if allowed_models is None:
                    allowed_models = auth_ctx.allowed_models
                else:
                    allowed_models = tuple(m for m in allowed_models if m in auth_ctx.allowed_models)

            def _min_opt(a: Optional[int], b: Optional[int]) -> Optional[int]:
                if a is None:
                    return b
                if b is None:
                    return a
                return min(a, b)

            resolution = PipelineResolution(
                pipeline_id=resolution.pipeline_id,
                resolved_corpus_id=resolution.resolved_corpus_id,
                effective_filters=resolution.effective_filters,
                allowed_tools=resolution.allowed_tools,
                allowed_corpus_ids=resolution.allowed_corpus_ids,
                allowed_providers=allowed_providers,
                allowed_models=allowed_models,
                default_provider=resolution.default_provider,
                default_model=resolution.default_model,
                max_input_tokens=_min_opt(auth_ctx.max_input_tokens, resolution.max_input_tokens),
                max_output_tokens=_min_opt(auth_ctx.max_output_tokens, resolution.max_output_tokens),
                max_total_tokens=_min_opt(auth_ctx.max_total_tokens, resolution.max_total_tokens),
                max_top_k=_min_opt(auth_ctx.max_top_k, resolution.max_top_k),
                chunking=resolution.chunking,
            )

        return resolution
    except ValueError as exc:
        raise ServiceError(
            code="invalid_pipeline",
            message=str(exc),
            status_code=422,
        )


async def _run_chat_completion(
    body: OpenAIChatCompletionRequest,
    *,
    trace_id: Optional[str] = None,
    auth_header: Optional[str] = None,
    pipeline_ctx: PipelineResolution | None = None,
) -> tuple[str, Dict[str, Any], str, Optional[Any]]:
    if pipeline_ctx is None:
        pipeline_ctx = _resolve_pipeline_context(
            pipeline_id=body.pipeline_id,
            requested_corpus_id=None,
            requested_filters=None,
            task=body.task,
        )

    provider_name, req_model = _resolve_provider_and_model(
        body.model,
        pipeline_ctx=pipeline_ctx,
        task=body.task,
    )
    if provider_name not in providers:
        raise ServiceError(
            code="invalid_provider",
            message="Unsupported provider",
            status_code=422,
            details={"provider": provider_name, "supported_providers": sorted(providers.keys())},
        )

    task = (body.task or "").strip().lower()
    if (
        task != "chunking"
        and pipeline_ctx.allowed_providers is not None
        and provider_name not in pipeline_ctx.allowed_providers
    ):
        raise ServiceError(
            code="forbidden_provider",
            message=f"Provider '{provider_name}' is not allowed by policy",
            status_code=403,
            details={"provider": provider_name, "allowed_providers": pipeline_ctx.allowed_providers},
        )

    client_model_id = f"{provider_name}:{req_model}"
    if (
        task != "chunking"
        and pipeline_ctx.allowed_models is not None
        and client_model_id not in pipeline_ctx.allowed_models
        and req_model not in pipeline_ctx.allowed_models
    ):
        raise ServiceError(
            code="forbidden_model",
            message=f"Model '{req_model}' is not allowed by policy",
            status_code=403,
            details={"model": req_model, "allowed_models": pipeline_ctx.allowed_models},
        )

    provider = providers[provider_name]
    provider_def = _get_provider_definition(provider_name)
    if provider_def is None:
        raise ServiceError(
            code="invalid_provider",
            message="Unsupported provider",
            status_code=422,
            details={"provider": provider_name},
        )
    if task == "chunking":
        if not pipeline_ctx.chunking.enabled:
            raise ServiceError(
                code="chunking_policy_disabled",
                message="Chunking is not enabled by the selected policy",
                status_code=403,
            )
        if not provider_def.capabilities.chunking:
            raise ServiceError(
                code="provider_chunking_disabled",
                message=f"Provider '{provider_name}' is not enabled for chunking",
                status_code=403,
                details={"provider": provider_name},
            )
        chunking_allowed_providers = pipeline_ctx.chunking.allowed_providers
        if chunking_allowed_providers is not None and provider_name not in chunking_allowed_providers:
            raise ServiceError(
                code="forbidden_chunking_provider",
                message=f"Provider '{provider_name}' is not allowed for chunking by policy",
                status_code=403,
                details={"provider": provider_name, "allowed_providers": chunking_allowed_providers},
            )
        chunking_allowed_models = pipeline_ctx.chunking.allowed_models
        if (
            chunking_allowed_models is not None
            and client_model_id not in chunking_allowed_models
            and req_model not in chunking_allowed_models
        ):
            raise ServiceError(
                code="forbidden_chunking_model",
                message=f"Model '{req_model}' is not allowed for chunking by policy",
                status_code=403,
                details={"model": req_model, "allowed_models": chunking_allowed_models},
            )
    req_temperature, req_max_tokens, req_context_length = _resolve_generation_controls(
        provider_name=provider_name,
        body=body,
        pipeline_ctx=pipeline_ctx,
    )
    started = time.perf_counter()

    logger.info(
        "chat_completion_request %s",
        bounded_log_payload(
            trace_id=trace_id,
            provider=provider_name,
            model=req_model,
            messages_count=len(body.messages),
            requested_stream=body.stream,
            provider_streaming_capable=provider_def.capabilities.streaming,
            max_chars=settings.log_text_max_chars,
        ),
    )

    messages: List[ChatMessage] = _to_provider_messages(body.messages)

    requested_tool_choice = body.tool_choice
    disable_tools = isinstance(requested_tool_choice, str) and requested_tool_choice.strip().lower() == "none"
    tools: List[Any] = []
    provider_supports_tools = provider_def.capabilities.tools
    client_tools = _client_tool_definitions_for_chunking(body)
    if provider_supports_tools and client_tools:
        tools.extend(client_tools)
    elif settings.enable_server_tools and provider_supports_tools and not disable_tools:
        tools.extend(rag_tooling.tool_definitions(allowed_tools=pipeline_ctx.allowed_tools))
        if mcp_registry is not None and mcp_registry.enabled:
            tools.extend(mcp_registry.tool_definitions(allowed_tools=pipeline_ctx.allowed_tools))
    # When tools are disabled, do NOT send tool_choice to providers that require tools to be set.
    client_tool_choice = _client_tool_choice_for_chunking(body)
    tool_choice = client_tool_choice if client_tools else (None if disable_tools else ("auto" if tools else None))

    usage_total: Dict[str, Any] = {}
    resp_model = req_model
    content = ""
    # Enforce strict capability if json_schema is requested
    if body.response_format and body.response_format.get("type") == "json_schema":
        if not _is_strict_schema_capable(provider_name, req_model):
            # If the provider is Anthropic, it currently ignores response_format anyway.
            # For others, we fail closed to prevent silent strictness downgrade.
            if provider_name.lower() == "anthropic":
                logger.warning("strict_schema_requested_for_anthropic provider=%s model=%s", provider_name, req_model)
            else:
                raise ServiceError(
                    code="unsupported_strict_schema_provider",
                    message=f"Provider '{provider_name}' with model '{req_model}' does not support strict JSON schema enforcement.",
                    status_code=400,
                    details={"provider": provider_name, "model": req_model},
                )

    provider_response_format = normalize_response_format_for_provider(
        body.response_format,
        provider_name=provider_name,
    )
    retried_without_tools = False

    for round_idx in range(max(1, settings.mcp_max_tool_rounds)):
        round_started = time.perf_counter()
        try:
            resp = await provider.chat(
                messages=messages,
                params=GenerationParams(
                    model=req_model,
                    temperature=req_temperature,
                    max_tokens=req_max_tokens,
                    context_length=req_context_length,
                    response_format=provider_response_format,
                ),
                tools=tools,
                tool_choice=tool_choice,
            )
        except LLMError as exc:
            if tools and not client_tools and not retried_without_tools and _should_retry_without_tools(exc):
                retried_without_tools = True
                logger.warning(
                    "chat_completion_retry_without_tools %s",
                    bounded_log_payload(
                        trace_id=trace_id,
                        provider=provider_name,
                        model=req_model,
                        upstream_status=(exc.details or {}).get("upstream_status")
                        if isinstance(exc.details, dict)
                        else None,
                        upstream_body=(exc.details or {}).get("upstream_body")
                        if isinstance(exc.details, dict)
                        else None,
                        max_chars=settings.log_text_max_chars,
                    ),
                )
                tools = []
                tool_choice = None
                continue
            raise

        resp_model = resp.model
        content = (
            (resp.message.content or "").strip() if isinstance(resp.message.content, str) else str(resp.message.content)
        )
        if (body.task or "").strip().lower() == "chunking" and resp.tool_calls:
            for tc in resp.tool_calls:
                if tc.name != "emit_chunk_offsets":
                    continue
                if isinstance(tc.arguments, dict):
                    content = json.dumps(tc.arguments, ensure_ascii=False)
                elif isinstance(tc.arguments_raw, str) and tc.arguments_raw.strip():
                    content = tc.arguments_raw.strip()
                if content:
                    resp.tool_calls = []
                    break
        if not content and not resp.tool_calls and tools and not client_tools and not retried_without_tools:
            retried_without_tools = True
            logger.warning(
                "chat_completion_empty_content_retry_without_tools %s",
                bounded_log_payload(
                    trace_id=trace_id,
                    provider=provider_name,
                    model=req_model,
                    finish_reason=resp.finish_reason,
                    max_chars=settings.log_text_max_chars,
                ),
            )
            tools = []
            tool_choice = None
            continue
        if not content and not resp.tool_calls:
            raw_snippet = ""
            if isinstance(resp.raw, dict):
                raw_snippet = str(resp.raw)[:600]
            raise ServiceError(
                code="empty_content",
                message="Provider returned empty assistant content",
                status_code=502,
                details={
                    "provider": provider_name,
                    "model": resp.model,
                    "finish_reason": resp.finish_reason,
                    "tool_calls": len(resp.tool_calls or []),
                    "raw_snippet": raw_snippet,
                },
            )

        # Best-effort usage aggregation for OpenAI-like usage dicts.
        if isinstance(resp.usage, dict):
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                v = resp.usage.get(k)
                if isinstance(v, int):
                    usage_total[k] = int(usage_total.get(k, 0)) + v

        # Preserve the assistant tool request in history (needed by providers).
        messages.append(ChatMessage(role="assistant", content=content, tool_calls=resp.tool_calls or None))
        round_latency_ms = int((time.perf_counter() - round_started) * 1000)
        logger.info(
            "chat_completion_round %s",
            bounded_log_payload(
                trace_id=trace_id,
                round=round_idx + 1,
                provider=provider_name,
                model=resp.model,
                finish_reason=resp.finish_reason,
                tool_calls=len(resp.tool_calls or []),
                assistant_chars=len(content),
                latency_ms=round_latency_ms,
                max_chars=settings.log_text_max_chars,
            ),
        )

        if not resp.tool_calls:
            break

        tool_msgs: List[ChatMessage] = []
        for tc in resp.tool_calls:
            if rag_tooling.can_handle(tc.name, allowed_tools=pipeline_ctx.allowed_tools):
                tool_started = time.perf_counter()
                # Pass correlation headers to tools
                tool_headers = {
                    "x-request-id": trace_id,
                    "x-correlation-id": trace_id,
                }
                if auth_header:
                    tool_headers["authorization"] = auth_header

                tool_msgs.append(await rag_tooling.execute(tc, headers=tool_headers, pipeline=pipeline_ctx))
                logger.info(
                    "rag_tool_call %s",
                    bounded_log_payload(
                        trace_id=trace_id,
                        round=round_idx + 1,
                        tool=tc.name,
                        tool_call_id=tc.id,
                        latency_ms=int((time.perf_counter() - tool_started) * 1000),
                        max_chars=settings.log_text_max_chars,
                    ),
                )
                continue

            if mcp_registry is not None and mcp_registry.enabled:
                tool_msgs.extend(
                    await mcp_registry.execute_tool_calls(
                        [tc],
                        trace_id=trace_id,
                        round_idx=round_idx + 1,
                        allowed_tools=pipeline_ctx.allowed_tools,
                    )
                )
                continue

            tool_msgs.append(
                ChatMessage(
                    role="tool",
                    content=f"ERROR: Tool '{tc.name}' is unavailable (MCP not configured)",
                    tool_call_id=tc.id,
                )
            )

        messages.extend(tool_msgs)

    else:
        logger.warning(
            "chat_completion_tool_loop_exceeded %s",
            bounded_log_payload(
                trace_id=trace_id,
                provider=provider_name,
                model=req_model,
                max_tool_rounds=settings.mcp_max_tool_rounds,
                max_chars=settings.log_text_max_chars,
            ),
        )
        raise ServiceError(
            code="tool_loop_exceeded",
            message="Exceeded maximum tool rounds",
            status_code=500,
            details={"max_tool_rounds": settings.mcp_max_tool_rounds},
        )

    latency_ms = int((time.perf_counter() - started) * 1000)

    # Validate response format if requested (strict enforcement)
    # This also parses the content for us to return in the 'parsed' field.
    parsed_content = None
    if body.response_format and (body.task or "").strip().lower() != "chunking":
        validate_response_format(body.response_format, content)
        if body.response_format.get("type") in ("json_object", "json_schema"):
            try:
                parsed_content = json.loads(content)
            except Exception:
                pass

    # Return model id in the same form the client sent (e.g. "openai:gpt-4o-mini") so
    # clients like Open WebUI keep using that selector on the next message. If we
    # returned only the upstream model (e.g. "gpt-4o-mini"), the next request would
    # lack the provider prefix and would be routed to default_provider, causing errors.
    request_used_prefixed_model = ":" in (body.model or "").strip()
    client_model = f"{provider_name}:{resp_model}" if request_used_prefixed_model else resp_model

    logger.info(
        "chat_completion_success %s",
        bounded_response_payload(
            trace_id=trace_id,
            provider=provider_name,
            model=client_model,
            latency_ms=latency_ms,
            content=content,
            max_chars=settings.log_text_max_chars,
        ),
    )

    return content, usage_total or (resp.usage or {}), client_model, parsed_content


@app.get("/health", summary="Liveness probe")
def health() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/v1/health", include_in_schema=False)
def health_v1() -> Dict[str, bool]:
    return {"ok": True}


@app.get("/v1/openapi.json", include_in_schema=False)
def openapi_v1() -> Dict[str, Any]:
    # Open WebUI probes this path when API base URL ends with /v1.
    return app.openapi()


@app.post(
    "/v1/rag/query",
    summary="Deterministic RAG retrieval (structured)",
    response_model=RetrievalQueryResponse,
)
async def rag_query_v1(
    body: RagQueryRequest,
    request: Request,
    response: Response,
    auth_ctx: Optional[AuthContext] = Depends(require_scope("rag:query")),
) -> RetrievalQueryResponse:
    trace_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or request.headers.get("x-trace-id")
        or uuid4().hex
    )
    response.headers["x-request-id"] = trace_id
    auth_header = request.headers.get("authorization")
    pipeline_ctx = _resolve_pipeline_context(
        pipeline_id=body.pipeline_id,
        requested_corpus_id=body.corpus_id,
        requested_filters=body.filters,
        auth_ctx=auth_ctx,
    )

    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail=_error_payload("invalid_request", "Missing required field: query"))

    try:
        corpus_id = pipeline_ctx.enforce_corpus(body.corpus_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_error_payload("invalid_pipeline", str(exc))) from exc
    top_k = int(body.top_k) if body.top_k is not None else int(settings.default_top_k)
    top_k = max(top_k, 1)

    if pipeline_ctx.max_top_k is not None:
        top_k = min(top_k, pipeline_ctx.max_top_k)

    filters = dict(pipeline_ctx.effective_filters)

    req = RetrievalQueryRequest(query=query, corpus_id=corpus_id, filters=filters, top_k=top_k)
    # Pass correlation headers to retrieval-api
    headers = {
        "x-request-id": trace_id,
        "x-correlation-id": trace_id,
    }
    if auth_header:
        headers["authorization"] = auth_header

    try:
        return await _call_retrieval_api(
            base_url=settings.retrieval_api_url,
            req=req,
            timeout_s=20.0,
            headers=headers,
        )
    except Exception as exc:
        logger.warning(
            "rag_query_failed %s",
            bounded_log_payload(
                trace_id=trace_id,
                corpus_id=corpus_id,
                top_k=top_k,
                filter_keys=sorted(filters.keys()),
                error=str(exc),
                max_chars=settings.log_text_max_chars,
            ),
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail=_error_payload("retrieval_upstream_error", str(exc)))


@app.post(
    "/v1/rag/lookup",
    summary="Deterministic RAG lexical/exact lookup (structured)",
    response_model=RetrievalQueryResponse,
)
async def rag_lookup_v1(
    body: RagLookupRequest,
    request: Request,
    response: Response,
    auth_ctx: Optional[AuthContext] = Depends(require_scope("rag:query")),
) -> RetrievalQueryResponse:
    trace_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or request.headers.get("x-trace-id")
        or uuid4().hex
    )
    response.headers["x-request-id"] = trace_id
    auth_header = request.headers.get("authorization")
    pipeline_ctx = _resolve_pipeline_context(
        pipeline_id=body.pipeline_id,
        requested_corpus_id=body.corpus_id,
        requested_filters=body.filters,
        auth_ctx=auth_ctx,
    )

    terms = [str(term).strip() for term in body.terms if str(term).strip()]
    if not terms:
        raise HTTPException(status_code=422, detail=_error_payload("invalid_request", "Missing required field: terms"))

    try:
        corpus_id = pipeline_ctx.enforce_corpus(body.corpus_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_error_payload("invalid_pipeline", str(exc))) from exc

    top_k = int(body.top_k) if body.top_k is not None else 5
    top_k = max(top_k, 1)
    max_results = int(body.max_results) if body.max_results is not None else 20
    max_results = max(max_results, 1)

    if pipeline_ctx.max_top_k is not None:
        top_k = min(top_k, pipeline_ctx.max_top_k)
        max_results = min(max_results, pipeline_ctx.max_top_k)

    filters = dict(pipeline_ctx.effective_filters)
    req = RetrievalLookupRequest(
        terms=terms,
        corpus_id=corpus_id,
        filters=filters,
        top_k=top_k,
        max_results=max_results,
    )
    headers = {
        "x-request-id": trace_id,
        "x-correlation-id": trace_id,
    }
    if auth_header:
        headers["authorization"] = auth_header

    try:
        return await _call_retrieval_lookup_api(
            base_url=settings.retrieval_api_url,
            req=req,
            timeout_s=20.0,
            headers=headers,
        )
    except Exception as exc:
        logger.warning(
            "rag_lookup_failed %s",
            bounded_log_payload(
                trace_id=trace_id,
                corpus_id=corpus_id,
                top_k=top_k,
                max_results=max_results,
                filter_keys=sorted(filters.keys()),
                error=str(exc),
                max_chars=settings.log_text_max_chars,
            ),
            exc_info=True,
        )
        raise HTTPException(status_code=502, detail=_error_payload("retrieval_lookup_upstream_error", str(exc)))


@app.get("/v1/models")
def models_v1(
    pipeline_id: Optional[str] = Query(
        default=None,
        description="Optional pipeline identifier used to policy-filter the returned model catalog.",
    ),
    auth_ctx: Optional[AuthContext] = Depends(require_scope("models:list")),
) -> Dict[str, Any]:
    now = int(time.time())
    seen: set[str] = set()
    models: List[Dict[str, Any]] = []

    pipeline_ctx = _resolve_pipeline_context(
        pipeline_id=pipeline_id,
        requested_corpus_id=None,
        requested_filters=None,
        auth_ctx=auth_ctx,
    )
    allowed_providers = pipeline_ctx.allowed_providers
    allowed_models = pipeline_ctx.allowed_models

    provider_names = sorted(providers.keys())
    use_prefix = len(provider_names) > 1
    for provider_name in provider_names:
        if allowed_providers is not None and provider_name not in allowed_providers:
            continue

        for provider_model in _listed_models_for_provider(provider_name):
            model_id = f"{provider_name}:{provider_model}" if use_prefix else provider_model

            if allowed_models is not None and model_id not in allowed_models and provider_model not in allowed_models:
                continue

            if model_id in seen:
                continue
            seen.add(model_id)
            models.append({"id": model_id, "object": "model", "created": now, "owned_by": provider_name})
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions_v1(
    body: OpenAIChatCompletionRequest,
    request: Request,
    auth_ctx: Optional[AuthContext] = Depends(require_scope("chat:invoke")),
):
    user_agent = request.headers.get("user-agent", "")
    trace_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or request.headers.get("x-trace-id")
        or uuid4().hex
    )
    auth_header = request.headers.get("authorization")
    pipeline_ctx = _resolve_pipeline_context(
        pipeline_id=body.pipeline_id,
        requested_corpus_id=None,
        requested_filters=None,
        auth_ctx=auth_ctx,
        task=body.task,
    )
    try:
        content, usage, model, parsed = await _run_chat_completion(
            body,
            trace_id=trace_id,
            auth_header=auth_header,
            pipeline_ctx=pipeline_ctx,
        )
    except (LLMError, ServiceError) as exc:
        logger.warning(
            "chat_completion_error %s",
            bounded_log_payload(
                trace_id=trace_id,
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                details=getattr(exc, "details", None),
                max_chars=settings.log_text_max_chars,
            ),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, getattr(exc, "details", None)),
            headers={"x-request-id": trace_id},
        )

    logger.info(
        "openai_chat_completion %s",
        bounded_log_payload(
            trace_id=trace_id,
            model=model,
            requested_stream=body.stream,
            response_stream=body.stream,
            user_agent=user_agent,
            max_chars=settings.log_text_max_chars,
        ),
    )

    if not body.stream:
        return JSONResponse(
            content=_as_openai_chat_response(
                model=model,
                content=content,
                usage=usage,
                parsed=parsed,
            ),
            headers={"x-request-id": trace_id},
        )

    completion_id = f"chatcmpl-{uuid4().hex}"

    async def stream_events():
        yield _as_openai_stream_chunk(model, completion_id, {"role": "assistant"}, None)
        for piece in _content_chunks(content):
            yield _as_openai_stream_chunk(model, completion_id, {"content": piece}, None)
        yield _as_openai_stream_chunk(model, completion_id, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "x-request-id": trace_id},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    if isinstance(exc.detail, dict) and exc.detail == {"error": "unauthorized"}:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            code="http_error",
            message=str(exc.detail),
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(ServiceError)
async def service_error_handler(_, exc: ServiceError):
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.code, exc.message, exc.details),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception):
    logger.exception("Unhandled exception in orchestrator-api")
    return JSONResponse(
        status_code=500,
        content=_error_payload(
            code="internal_error",
            message="Internal server error",
        ),
    )
