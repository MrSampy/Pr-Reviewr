import base64
import difflib
import logging
import os
import sys

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

_PAT = os.environ.get("ADO_PAT", "")
_ORG = os.environ.get("ADO_ORG", "")
_PROJECT = os.environ.get("ADO_PROJECT", "")
_BASE = f"https://dev.azure.com/{_ORG}/{_PROJECT}/_apis"

_EXTENSIONS = {".cs", ".js"}
_BOT_TAG = "<!-- pr-reviewr -->"
_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "suggestion": "🔵"}


def _headers() -> dict[str, str]:
    token = base64.b64encode(f":{_PAT}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _get(url: str, params: dict | None = None) -> httpx.Response:
    resp = httpx.get(url, headers=_headers(), params=params)
    if resp.status_code == 401:
        raise RuntimeError("ADO authentication failed. Check ADO_PAT.")
    return resp


def get_pr_info(pr_id: int) -> dict:
    url = f"{_BASE}/git/pullrequests/{pr_id}"
    resp = _get(url, params={"api-version": "7.1"})
    if resp.status_code == 404:
        raise RuntimeError(f"PR {pr_id} not found.")
    if resp.status_code != 200:
        raise RuntimeError(resp.text)
    return resp.json()


def get_file_content(repo_id: str, file_path: str, branch: str) -> str:
    url = f"{_BASE}/git/repositories/{repo_id}/items"
    params = {
        "path": file_path,
        "versionDescriptor.version": branch,
        "versionDescriptor.versionType": "branch",
        "api-version": "7.1",
    }
    resp = _get(url, params=params)
    if resp.status_code == 404:
        return ""
    if resp.status_code != 200:
        raise RuntimeError(resp.text)
    return resp.text


def get_pr_diff(pr_id: int) -> str:
    info = get_pr_info(pr_id)
    repo_id: str = info["repository"]["id"]
    source_branch = info["sourceRefName"].removeprefix("refs/heads/")
    target_branch = info["targetRefName"].removeprefix("refs/heads/")

    url = f"{_BASE}/git/repositories/{repo_id}/diffs/commits"
    params = {
        "baseVersion": target_branch,
        "baseVersionType": "branch",
        "targetVersion": source_branch,
        "targetVersionType": "branch",
        "api-version": "7.1",
    }
    resp = _get(url, params=params)
    if resp.status_code == 404:
        raise RuntimeError(f"PR {pr_id} not found.")
    if resp.status_code != 200:
        raise RuntimeError(resp.text)

    changes: list[dict] = resp.json().get("changes", [])

    parts: list[str] = []
    for change in changes:
        item = change.get("item", {})
        path: str = item.get("path", "")
        if not any(path.endswith(ext) for ext in _EXTENSIONS):
            continue

        change_type: str = change.get("changeType", "")
        old_content = (
            ""
            if change_type == "add"
            else get_file_content(repo_id, path, target_branch)
        )
        new_content = (
            ""
            if change_type == "delete"
            else get_file_content(repo_id, path, source_branch)
        )

        diff_lines = list(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a{path}",
                tofile=f"b{path}",
            )
        )
        if diff_lines:
            header = f"diff --git a{path} b{path}\n"
            parts.append(header + "".join(diff_lines))

    return "\n".join(parts)


def _repo_id(pr_id: int) -> str:
    return get_pr_info(pr_id)["repository"]["id"]


def get_pr_threads(pr_id: int) -> list[dict]:
    repo_id = _repo_id(pr_id)
    url = f"{_BASE}/git/repositories/{repo_id}/pullRequests/{pr_id}/threads"
    resp = _get(url, params={"api-version": "7.1"})
    if resp.status_code == 404:
        raise RuntimeError(f"PR {pr_id} not found.")
    if resp.status_code != 200:
        raise RuntimeError(resp.text)
    return resp.json().get("value", [])


def comment_exists(
    threads: list[dict], file_path: str, line: int, bot_tag: str = _BOT_TAG
) -> bool:
    for thread in threads:
        ctx = thread.get("threadContext") or {}
        if ctx.get("filePath") != file_path:
            continue
        start = (ctx.get("rightFileStart") or {}).get("line")
        if start != line:
            continue
        for comment in thread.get("comments", []):
            if bot_tag in (comment.get("content") or ""):
                return True
    return False


def post_comment(pr_id: int, file_path: str, line: int, content: str) -> dict:
    repo_id = _repo_id(pr_id)
    url = f"{_BASE}/git/repositories/{repo_id}/pullRequests/{pr_id}/threads"
    body = {
        "comments": [
            {
                "parentCommentId": 0,
                "content": f"{_BOT_TAG}\n{content}",
                "commentType": 1,
            }
        ],
        "threadContext": {
            "filePath": file_path,
            "rightFileStart": {"line": line, "offset": 1},
            "rightFileEnd": {"line": line, "offset": 1000},
        },
        "status": 1,
    }
    resp = httpx.post(url, headers=_headers(), params={"api-version": "7.1"}, json=body)
    if resp.status_code == 401:
        raise RuntimeError("ADO authentication failed. Check ADO_PAT.")
    if resp.status_code == 403:
        logger.warning("No permission to post comments on PR %d", pr_id)
        return {}
    if resp.status_code == 404:
        logger.warning("PR or file not found: PR %d, %s", pr_id, file_path)
        return {}
    if resp.status_code not in (200, 201):
        logger.error("Failed to post comment: %s", resp.text)
        return {}
    return resp.json()


def resolve_comment(pr_id: int, thread_id: int) -> None:
    repo_id = _repo_id(pr_id)
    url = f"{_BASE}/git/repositories/{repo_id}/pullRequests/{pr_id}/threads/{thread_id}"
    resp = httpx.patch(
        url,
        headers=_headers(),
        params={"api-version": "7.1"},
        json={"status": 4},
    )
    if resp.status_code == 401:
        raise RuntimeError("ADO authentication failed. Check ADO_PAT.")
    if resp.status_code not in (200, 201):
        raise RuntimeError(resp.text)


def post_review_comments(pr_id: int, comments: list[dict]) -> None:
    threads = get_pr_threads(pr_id)
    posted = 0
    skipped = 0
    for comment in comments:
        file_path: str = comment.get("file", "")
        line: int = comment.get("line", 1)
        severity: str = comment.get("severity", "suggestion").lower()
        problem: str = comment.get("problem", "")
        fix: str = comment.get("fix", "")

        if comment_exists(threads, file_path, line):
            skipped += 1
            continue

        emoji = _SEVERITY_EMOJI.get(severity, "🔵")
        text = f"{emoji} **{severity.upper()}**: {problem}\n\n**Fix:**\n```\n{fix}\n```"

        result = post_comment(pr_id, file_path, line, text)
        if result:
            posted += 1
            logger.info("Posted comment on %s:%d", file_path, line)
        else:
            skipped += 1

    logger.info("Done — posted: %d, skipped: %d", posted, skipped)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m ado_client.ado_client <pr_id>")
        sys.exit(1)

    pr_id = int(sys.argv[1])
    diff = get_pr_diff(pr_id)
    print(diff[:2000])
