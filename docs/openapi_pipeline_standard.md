# OpenAPI Standard for LLM/RAG Pipelines

To ensure our OpenAPI contracts are effective for LLM and RAG pipeline use, all key endpoints must meet the following quality bar:

## 1. Description Style
- **Operation Descriptions**: Must clearly state *when* a pipeline should use the endpoint, not just what it does.
- **Field Descriptions**: Must explain the semantic meaning of the field, default behaviors if omitted, and any constraints (e.g., mutually exclusive fields).

## 2. Example Coverage
Every critical endpoint must include:
- **Minimal Request Example**: The simplest valid payload.
- **Representative Request Example**: A realistic, fully populated payload typical of pipeline usage.
- **Success Response Example**: A representative `200 OK` payload.
- **Error Response Examples**: Concrete examples for the most likely runtime error modes (e.g., `400`, `404`, `422`, `502`).

## 3. Schema Precision
- **Conditional Rules**: Use `oneOf`, `anyOf`, or `allOf` to encode mutually exclusive fields or conditional requirements (e.g., `inline_content` vs `source_uri`).
- **Discriminators**: Use discriminators for large unions (e.g., Graph nodes and edges) to allow reliable parsing.
- **Strict Types**: Avoid generic `object` or `array` without `items` where possible. Define explicit schemas for nested structures.
- **Error Envelopes**: Ensure error response schemas match the actual runtime behavior (e.g., structured `detail` objects vs. plain strings).
