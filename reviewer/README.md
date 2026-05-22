# Reviewer Module Reference

This folder contains the Python modules that implement the RAG-based LLM code review pipeline.

## Overview

The reviewer pipeline is designed to:

- Split a unified diff into per-file, per-hunk chunks
- Retrieve semantically similar code and past review comments from ChromaDB (RAG)
- Build a structured prompt and call a local LLM via Ollama
- Parse and validate the LLM JSON response
- Return a deduplicated, severity-sorted list of review comments

Supported source file types:

- `.cs`, `.cshtml`
- `.js`

---

## `pipeline.py`

Purpose:

- Orchestrates the full review flow from diff string to validated comment list

Key logic:

- Splits the diff into per-file chunks with `_split_diff_by_file`
- If a file's diff exceeds `_MAX_HUNK_CHARS` (3000 chars), splits further by `@@` hunk boundaries using `_split_by_hunks`
- Reviews all chunks in parallel using `ThreadPoolExecutor(max_workers=4)`
- Each chunk is reviewed by `_review_chunk`, which strictly validates that returned comments reference only the file in that chunk — cross-file attributions are dropped with a warning
- Deduplicates results by `(file, line)` across chunks
- Sorts by severity: critical → warning → suggestion

Functions:

- `review_diff(diff: str) -> list[dict]` — main entry point
- `detect_language(diff: str) -> str` — returns `"csharp"` or `"javascript"` based on file extension counts
- `_split_diff_by_file(diff: str) -> list[str]` — splits on `diff --git` headers
- `_split_by_hunks(file_diff: str, max_chars: int) -> list[str]` — splits by `@@` boundaries, prepends file header to each chunk
- `_extract_file_path(chunk: str) -> str` — extracts ADO-style path from `+++ b/...` line, normalises with leading `/`
- `_build_work_units(diff: str) -> list[tuple[str, str]]` — returns `(chunk_diff, file_path)` pairs
- `_review_chunk(chunk, file_path, language, rag_chunks) -> list[dict]` — calls LLM for one chunk, validates file attribution

---

## `retriever.py`

Purpose:

- Embeds diff content and queries ChromaDB for semantically similar code chunks and past PR comments

Key logic:

- Parses the diff and extracts added lines per file using `parse_diff`
- Embeds added lines with Ollama via `indexer.embedder.embed_texts`
- Queries ChromaDB with `n_results=10`, filtered by language
- Deduplicates results by `method_name|file` key
- Sorts results by cosine distance (closest first)

Environment variables:

- `CHROMA_HOST` (default: `localhost`)
- `CHROMA_PORT` (default: `8000`)
- `CHROMA_COLLECTION_NAME` (default: `pr-reviewr`)

Example output item:

```python
{
  'content': 'public Order GetOrder(int id) { ... }',
  'file': '/src/Services/OrderService.cs',
  'method_name': 'GetOrder',
  'type': 'code',
  'distance': 0.18,
}
```

---

## `prompt_builder.py`

Purpose:

- Assembles the full LLM prompt from a diff chunk and RAG context

Key logic:

- System prompt instructs the model to output raw JSON only with a fixed schema
- RAG context is split into two sections: past PR comments and similar code chunks
- Each section is capped at `_MAX_CHUNKS` (5) items, each item at `_MAX_CHUNK_CHARS` (300) chars
- Diff is capped at `_MAX_DIFF_CHARS` (5000 chars) as a safety net — pipeline splits before this threshold

Prompt rules embedded in the system prompt:

- Comment on added lines (`+`) and on removed lines (`-`) if removal introduces a bug or security issue
- Do not flag changes where old code is replaced by cleaner equivalent code
- Use single-quoted strings in the `fix` field to avoid JSON escaping issues
- Output raw JSON only — no markdown, no prose outside the JSON object

Functions:

- `build(diff, chunks, language) -> str` — assembles and returns the full prompt string
- `format_chunks(chunks) -> str` — formats RAG items into readable context blocks
- `estimate_tokens(text) -> int` — rough estimate at 4 chars per token

---

## `llm_client.py`

Purpose:

- Sends prompts to a local Ollama instance via the OpenAI-compatible API

Key logic:

- Reads configuration from environment variables:
  - `OLLAMA_URL` (default: `http://localhost:11434`)
  - `OLLAMA_MODEL` (default: `qwen2.5-coder:1.5b`)
- Uses `openai.OpenAI` with `base_url` pointed at Ollama's `/v1` endpoint
- `temperature=0` for deterministic output
- Timeout: 120 seconds

Functions:

- `review(prompt: str) -> str` — sends prompt, returns raw model response string
- `is_available() -> bool` — checks `/api/tags` to verify Ollama is running and the model is loaded

Error handling:

- `APITimeoutError` → `TimeoutError`
- `APIConnectionError` → `RuntimeError("Ollama is not running at {url}")`
- Empty response → `RuntimeError`

---

## `response_parser.py`

Purpose:

- Cleans, parses, and validates the raw LLM JSON response

Key logic:

- `clean_json` strips markdown fences and surrounding noise, extracts the outermost `{...}` block
- Primary parse uses `json.loads`; on failure falls back to `json_repair` (if installed) which handles common LLM failure modes such as unescaped double quotes in code snippets
- `validate_comment` enforces structural rules on each comment:
  - `file` must match one of the known diff files (suffix-path comparison)
  - `line` must be a positive integer
  - `severity` must be `critical`, `warning`, or `suggestion`
  - `problem` must be a string longer than 10 characters
  - `fix` must be a non-empty string
- `extract_diff_files` parses `+++ b/...` lines to build the list of files in a diff

Functions:

- `parse_response(raw: str, diff_files: list[str]) -> list[dict]`
- `clean_json(raw: str) -> str`
- `validate_comment(comment: dict, diff_files: list[str]) -> bool`
- `extract_diff_files(diff: str) -> list[str]`

---

## Notes

- If ChromaDB is unavailable, `retriever.retrieve` raises `RuntimeError`. The pipeline catches this and continues with an empty RAG context.
- `json_repair` is an optional dependency. If not installed, broken JSON responses are logged and dropped rather than repaired.
- The `_MAX_WORKERS=4` parallelism setting is meaningful with remote LLM APIs. With local Ollama, requests are queued sequentially on the Ollama side but the main thread remains unblocked.
