# Retrieval API Contract (v1)

## Versioning

- Canonical hybrid endpoint: `POST /v1/query`
- Lexical/exact lookup endpoint: `POST /v1/lookup`
- Legacy alias: `POST /query` (deprecated, same contract)
- Response includes `api_version: "v1"`

## Compatibility policy

- Within `v1`, changes must be additive only.
- Breaking changes (removing/renaming fields, changing field meaning/type, incompatible behavior) require a new major path such as `POST /v2/query`.

## Request schema (`QueryRequest`)

Required:
- `query` (string)
- `corpus_id` (string)

Optional:
- `filters` (object, default `{}`)
- `top_k` (integer, default `8`, must be `> 0`)

Invalid request examples:
- `top_k <= 0` -> `422` validation error

## Lookup request schema (`LookupRequest`)

`POST /v1/lookup` is for exact lexical terms, endpoint paths, symbols, and configuration keys. It does not call the embedder or Qdrant.

Required:
- `terms` (array of strings)
- `corpus_id` (string)

Optional:
- `filters` (object, default `{}`)
- `top_k` (integer, default `5`, per lookup term, must be `> 0`)
- `max_results` (integer, default `20`, across all terms, must be `> 0`)

## Response schema (`QueryResponse`)

Required envelope fields:
- `api_version` (string)
- `answer` (string)
- `citations` (array)
- `chunks` (array of `RetrievedChunk`)

### Portable chunk shape (`RetrievedChunk`)

Always present:
- `chunk_id` (string)
- `text` (string)
- `score` (number)
- `doc_id` (string)
- `doc_type` (string)
- `source_url` (string)

Present when available:
- `section_id` (string)
- `title` (string)
- `tags` (array of strings)
- `version_date` (string)
- `metadata` (object)

## Error contract

- Unknown `corpus_id` -> `404`:

```json
{
  "error": {
    "code": "corpus_not_found",
    "message": "Unknown corpus_id",
    "details": {
      "corpus_id": "..."
    }
  }
}
```

- Invalid request -> `422` with validation `detail`.

## Determinism and filtering

- Retrieval is corpus-scoped; results are never retrieved from another corpus.
- Filters are applied to hybrid, lexical, and exact lookup retrieval.
- If filters match nothing, API returns `200` with `"chunks": []`.
- Ranking uses deterministic tie-break (`score desc`, then `chunk_id asc`).
- `/v1/lookup` ranks exact field, metadata, FTS, and graph-alias matches without dense vector retrieval.
