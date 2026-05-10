# Indexer Module Reference

This folder contains the Python modules used to extract repository code, transform it into searchable chunks, embed it with Ollama, and index it into ChromaDB.

## Overview

The indexer pipeline is designed to:

- Extract source files and metadata from a Git repository
- Parse code into method-level chunks using Tree-sitter
- Fetch Azure DevOps PR review comments and normalize them
- Embed chunks using Ollama embeddings
- Store vectors and metadata in ChromaDB
- Support incremental updates based on git diff and new ADO PR comments

Supported source file types:

- `.cs`
- `.cshtml`
- `.js`

---

## `code_extractor.py`

Purpose:

- Recursively scans the repository for supported source files
- Reads file contents
- Extracts Git metadata for each file: last modified date and author

Key logic:

- Ignores common directories: `node_modules`, `bin`, `obj`, and `.git`
- Uses `git log -1` to retrieve the latest commit date and author for each file
- Determines language based on file extension
- Returns a list of file dictionaries with path, content, language, author, and last modified date

Example output item:

```python
{
  'path': 'src/MyFile.cs',
  'content': '...',
  'last_modified': '2026-05-09',
  'language': 'csharp',
  'author': 'Jane Doe',
}
```

---

## `chunker.py`

Purpose:

- Parses source code into smaller, semantic chunks centered around method/function definitions
- Supports C# and JavaScript via Tree-sitter

Key logic:

- Loads the appropriate Tree-sitter language module dynamically
- Builds a parser and generates an AST from file source
- Finds method/function nodes by language-specific node types
- Extracts method name, source text, and line numbers
- Tokenizes methods and splits large methods into overlapping chunks
- Merges very small chunks into adjacent content to avoid overly fragmented vectors

Chunking behavior:

- Methods with <= 300 tokens stay in a single chunk
- Larger methods are split using a sliding window of 300 tokens with 50-token overlap
- Small chunks below 50 tokens are merged with their neighbor

This module also provides `chunk_code_from_string`, which is useful for indexing content without requiring a local file.

---

## `embedder.py`

Purpose:

- Converts text chunks into dense embeddings using Ollama

Key logic:

- Reads Ollama configuration from environment variables:
  - `OLLAMA_API_URL` or `OLLAMA_URL`
  - `OLLAMA_EMBED_MODEL`
- Sends chunks to Ollama in batches
- Validates the response format and ensures the returned embedding count matches the input
- Exposes convenience functions:
  - `embed_chunks(chunks, batch_size)`
  - `embed_texts(texts, batch_size)`

Important:

- Each chunk must include a `content` field
- Default batch size is `32`

---

## `ado_extractor.py`

Purpose:

- Fetches Azure DevOps pull request review comments
- Filters out bot/system authors
- Normalizes comment metadata for indexing

Key logic:

- Reads ADO configuration from environment variables:
  - `ADO_ORGANIZATION` / `ADO_ORG_URL`
  - `ADO_PROJECT`
  - `ADO_PAT`
- Uses `httpx` to query Azure DevOps PR and thread APIs
- Filters comments by human reviewers only
- Extracts file path, line number, PR ID, author, and content
- Determines the target language by file extension
- Returns a list of normalized PR comment items

Example output item:

```python
{
  'file': '/src/MyFile.cs',
  'line': 42,
  'content': 'Please refactor this loop.',
  'pr_id': 123,
  'language': 'csharp',
  'type': 'pr_comment',
  'author': 'Reviewer Name',
}
```

---

## `state.py`

Purpose:

- Persists indexing state between runs using SQLite

Key logic:

- Uses a local database file: `indexer_state.db`
- Creates a `state` table if it does not exist
- Stores and retrieves:
  - `last_commit` — last git commit hash indexed
  - `last_pr_id` — highest ADO PR ID indexed

Functions:

- `get_last_commit()` / `set_last_commit(commit_hash)`
- `get_last_pr_id()` / `set_last_pr_id(pr_id)`

---

## `incremental_indexer.py`

Purpose:

- Supports incremental updates to the ChromaDB index
- Re-indexes only changed source files and new ADO comments

Key logic:

- Computes changed files since `last_commit` using `git diff --name-only`
- Deletes old entries for changed files in the collection
- Re-chunks changed files and re-embeds them
- Fetches new PR comments since `last_pr_id`
- Adds only new PR comment items to the collection
- Uses the same metadata shape as the full pipeline

This module is useful when running in incremental mode to avoid full re-indexing.

---

## `pipeline.py`

Purpose:

- Coordinates the full indexing and incremental indexing workflows
- Builds and stores embeddings in ChromaDB

Key logic:

- Extracts code file items via `code_extractor`
- Chunks code with `chunker`
- Extracts PR comments via `ado_extractor`
- Embeds all items via `embedder`
- Writes data into a Chroma collection
- Persists state in `state.py`
- Supports two modes:
  - `full` — index all code and PR comments
  - `incremental` — update only changed files and new comments

Environment variables used:

- `REPO_PATH` — repository root path
- `CHROMA_PERSIST_DIR` — ChromaDB persistence directory
- `CHROMA_COLLECTION_NAME` — collection name
- ADO variables from `ado_extractor.py`
- Ollama variables from `embedder.py`

Usage example:

```bash
python indexer/pipeline.py --mode full
python indexer/pipeline.py --mode incremental
```

---

## Notes

- The `indexer` package is designed to be runnable as a standalone pipeline.
- If `CHROMA_PERSIST_DIR` is not configured, the code uses a default Chroma client behavior.
- The current implementation assumes the repository is a Git repo and that valid ADO credentials are available for PR comment extraction.
