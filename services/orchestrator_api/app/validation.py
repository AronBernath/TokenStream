import copy
import json
import logging
from typing import Any, Dict, Optional

try:
    from jsonschema import validate, ValidationError
except ImportError:
    validate = None

    class ValidationError(Exception):
        pass


from .errors import ServiceError

logger = logging.getLogger("orchestrator-api.validation")


def _normalize_json_schema_node(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize_json_schema_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    normalized = {key: _normalize_json_schema_node(value) for key, value in node.items()}
    node_type = normalized.get("type")
    is_object = node_type == "object"
    if isinstance(node_type, list):
        is_object = "object" in node_type
    if "properties" in normalized or is_object:
        normalized["additionalProperties"] = False
    return normalized


def normalize_response_format_for_provider(
    response_format: Optional[Dict[str, Any]],
    *,
    provider_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Normalize provider-facing json_schema response formats so strict OpenAI-compatible
    providers receive a schema that complies with their closed-object requirements.
    """
    if not response_format or response_format.get("type") != "json_schema":
        return response_format

    schema_def = response_format.get("json_schema") or {}
    schema = schema_def.get("schema")
    if not isinstance(schema, dict):
        return response_format

    normalized = copy.deepcopy(response_format)
    normalized.setdefault("json_schema", {})["schema"] = _normalize_json_schema_node(schema)
    provider_label = provider_name or "unknown"
    logger.debug("normalized_response_format provider=%s schema_name=%s", provider_label, schema_def.get("name"))
    return normalized


def validate_response_format(response_format: Optional[Dict[str, Any]], content: str) -> None:
    """
    Validates that the assistant content conforms to the requested response_format.

    Currently supports:
    - type: "json_object" (ensures valid JSON)
    - type: "json_schema" (ensures valid JSON + schema adherence)

    Raises ServiceError if validation fails.
    """
    if not response_format:
        return

    fmt_type = response_format.get("type")
    if fmt_type not in ("json_object", "json_schema"):
        return

    # 1. Parse JSON
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise ServiceError(
            code="response_json_decode_failed",
            message=f"Failed to decode assistant content as JSON: {str(e)}",
            status_code=422,
            details={"error": str(e), "content_snippet": content[:200]},
        )

    # 2. Validate Schema if requested
    if fmt_type == "json_schema":
        schema_def = response_format.get("json_schema", {})
        schema = schema_def.get("schema")
        if not schema:
            logger.warning("json_schema requested but no schema provided in response_format")
            return

        if validate is None:
            raise ServiceError(
                code="response_schema_validation_unavailable",
                message="JSON schema response validation is unavailable because jsonschema is not installed.",
                status_code=500,
                details={"schema_name": schema_def.get("name")},
            )

        try:
            validate(instance=parsed, schema=schema)
        except ValidationError as e:
            raise ServiceError(
                code="response_schema_validation_failed",
                message=f"Assistant content failed schema validation: {e.message}",
                status_code=422,
                details={"error": e.message, "path": list(e.path), "schema_name": schema_def.get("name")},
            )
        except Exception as e:
            # Catch-all for jsonschema internal issues.
            logger.error("jsonschema_validation_error %s", str(e), exc_info=True)
            raise
