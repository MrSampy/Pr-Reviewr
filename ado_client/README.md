# ADO Client Module Reference

This folder contains the Python module for interacting with the Azure DevOps REST API — fetching pull request diffs and posting review comments.

## Overview

The ADO client is designed to:

- Authenticate with Azure DevOps using a Personal Access Token
- Retrieve pull request metadata and unified diffs
- Post inline review comments to pull requests
- Deduplicate bot comments to prevent double-posting
- Resolve existing threads when issues are fixed

Configuration is read from environment variables (`.env`):

- `ADO_PAT` — Personal Access Token
- `ADO_ORG` — Organisation name (e.g. `mynameisserzheo`)
- `ADO_PROJECT` — Project name

---

## `ado_client.py`

### Authentication

All requests use HTTP Basic Auth. The token is derived as:

```python
base64(f":{ADO_PAT}")
```

A 401 response raises `RuntimeError("ADO authentication failed. Check ADO_PAT.")`.

---

### `get_pr_info(pr_id: int) -> dict`

Fetches raw pull request metadata.

- **Endpoint**: `GET /git/pullrequests/{pr_id}`
- **Returns**: full ADO JSON response
- **Key fields used by other functions**:
  - `repository.id` — repository GUID
  - `repository.name` — repository display name
  - `sourceRefName` — source branch (`refs/heads/...`)
  - `targetRefName` — target branch (`refs/heads/...`)

---

### `get_file_content(repo_id: str, file_path: str, branch: str) -> str`

Fetches the raw text content of a file at a given branch.

- **Endpoint**: `GET /git/repositories/{repo_id}/items`
- Returns an empty string if the file does not exist in that branch (404).

---

### `get_pr_diff(pr_id: int) -> str`

Builds a unified diff string for a pull request.

Algorithm:

1. Calls `get_pr_info` to get `repo_id`, source branch, and target branch.
2. Fetches the list of changed files via the commits diff endpoint.
3. Filters to `.cs` and `.js` files only (controlled by `_EXTENSIONS`).
4. For each file, fetches content in both branches and builds a unified diff with `difflib.unified_diff`.
5. Prepends a `diff --git a{path} b{path}` header to each file diff so downstream parsers can split by file.
6. Returns all file diffs joined as a single string.

---

### `get_pr_threads(pr_id: int) -> list[dict]`

Returns all review threads on a pull request.

- **Endpoint**: `GET /git/repositories/{repo_id}/pullRequests/{pr_id}/threads`
- Each thread contains `threadContext.filePath`, `threadContext.rightFileStart.line`, and a list of `comments`.

---

### `comment_exists(threads: list[dict], file_path: str, line: int, bot_tag: str) -> bool`

Checks whether the bot has already posted a comment at a given file and line.

- Matches by exact `filePath` and `rightFileStart.line`.
- Searches comment content for `bot_tag` (`<!-- pr-reviewr -->`).
- Used by `post_review_comments` to prevent duplicate comments.

---

### `post_comment(pr_id: int, file_path: str, line: int, content: str) -> dict`

Posts a new inline review thread to a pull request.

- **Endpoint**: `POST /git/repositories/{repo_id}/pullRequests/{pr_id}/threads`
- Prepends `<!-- pr-reviewr -->` to every comment body for deduplication.
- Error handling:
  - **403** — logs warning, returns `{}` (no permission)
  - **404** — logs warning, returns `{}` (PR or file not found)
  - **other** — logs error, returns `{}`

---

### `resolve_comment(pr_id: int, thread_id: int) -> None`

Resolves a thread by setting its status to `4` (fixed in ADO API).

- **Endpoint**: `PATCH /git/repositories/{repo_id}/pullRequests/{pr_id}/threads/{thread_id}`
- Used to close bot comments when the underlying issue has been addressed.

---

### `post_review_comments(pr_id: int, comments: list[dict]) -> None`

Orchestrates posting a batch of review comments.

Algorithm:

1. Fetches existing threads via `get_pr_threads`.
2. For each comment, checks `comment_exists` — skips if already posted.
3. Formats the comment body:

```
{emoji} **{SEVERITY}**: {problem}

**Fix:**
```
{fix}
```
```

4. Calls `post_comment`.
5. Logs a summary: how many comments were posted vs skipped.

Severity emoji mapping:

| Severity | Emoji |
|---|---|
| critical | 🔴 |
| warning | 🟡 |
| suggestion | 🔵 |

---

## Running directly

```bash
python -m ado_client.ado_client <pr_id>
```

Prints the first 2000 characters of the unified diff for the given PR.
