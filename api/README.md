# API Module Reference

This folder contains the FastAPI webhook handler that receives Azure DevOps pull request events and triggers the review pipeline.

## Overview

The webhook handler is designed to:

- Receive push events from Azure DevOps Service Hooks
- Validate an optional shared secret
- Return an immediate HTTP response to ADO (within the 5-second timeout)
- Run the full review pipeline asynchronously in the background

---

## `webhook_handler.py`

### Endpoints

#### `GET /health`

Returns service status. Used by uptime monitors and load balancers.

```json
{"status": "ok", "version": "1.0.0"}
```

#### `POST /webhook`

Receives Azure DevOps webhook payloads.

Supported event types:

- `git.pullrequest.created`
- `git.pullrequest.updated`

Any other `eventType` value returns `{"status": "ignored"}` immediately.

Request flow:

1. Validates the secret header (if `WEBHOOK_SECRET` is set).
2. Returns `403` if the secret does not match.
3. Checks `eventType` — returns `{"status": "ignored"}` for unsupported events.
4. Adds `process_pr_event` to `BackgroundTasks`.
5. Returns `{"status": "accepted"}` immediately without waiting for the review to complete.

---

### `validate_secret(request: Request) -> bool`

Validates the incoming request against `WEBHOOK_SECRET`.

- Reads `X-Hub-Signature` or `Authorization` header.
- Compares the value directly to `WEBHOOK_SECRET`.
- If `WEBHOOK_SECRET` is not set, always returns `True` (development mode).

---

### `process_pr_event(payload: dict) -> None`

Background task that runs the full review pipeline for one PR event.

Algorithm:

1. Extracts `eventType`, `pullRequestId`, and `status` from the payload.
2. Skips PRs with `status != "active"` (closed, abandoned, etc.).
3. Calls `get_pr_diff(pr_id)` — skips if diff is empty.
4. Calls `review_diff(diff)` from `reviewer.pipeline`.
5. If comments were produced, calls `post_review_comments(pr_id, comments)`.
6. Logs outcome at each step.

All exceptions are caught, logged with full traceback, and do not crash the server process.

---

### Configuration

Environment variables:

| Variable | Required | Description |
|---|---|---|
| `WEBHOOK_SECRET` | No | Shared secret for request validation. Skip validation if not set. |
| `ADO_PAT` | Yes | Azure DevOps Personal Access Token (used by `ado_client`) |
| `ADO_ORG` | Yes | ADO organisation name |
| `ADO_PROJECT` | Yes | ADO project name |
| `OLLAMA_URL` | No | Ollama base URL (default: `http://localhost:11434`) |
| `OLLAMA_MODEL` | No | Model name (default: `qwen2.5-coder:1.5b`) |

---

## Running the server

```bash
python -m uvicorn api.webhook_handler:app --host 0.0.0.0 --port 8080
```

For local development with live reload:

```bash
python api/webhook_handler.py
```

---

## Local development with ngrok

ADO cannot reach `localhost` directly. Use ngrok to expose a public URL:

```bash
ngrok http 8080
```

Copy the `https://....ngrok.io` URL and set it as the webhook URL in:

**ADO Project Settings → Service Hooks → Create Subscription → Web Hooks**

Register two subscriptions: one for `Pull request created` and one for `Pull request updated`.

---

## Notes

- `process_pr_event` is a regular (non-async) function. FastAPI runs `BackgroundTasks` in the same process after the response is sent, so ADO always receives a response within milliseconds.
- The server logs at `INFO` level by default. Set `LOG_LEVEL=DEBUG` in the environment to see per-chunk token estimates from the reviewer pipeline.
