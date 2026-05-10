import base64
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

SYSTEM_AUTHOR_KEYWORDS = {
    "system",
    "bot",
    "azure devops",
    "build",
    "release",
    "automation",
}

SUPPORTED_EXTENSIONS = {
    ".cs": "csharp",
    ".cshtml": "csharp",
    ".js": "javascript",
}


def _get_env_value(*names):
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def get_ado_config():
    organization = _get_env_value(
        "ADO_ORGANIZATION", "AZURE_DEVOPS_ORGANIZATION", "ADO_ORG", "AZURE_DEVOPS_ORG"
    )
    project = _get_env_value("ADO_PROJECT", "AZURE_DEVOPS_PROJECT")
    org_url = _get_env_value("ADO_ORG_URL", "AZURE_DEVOPS_ORG_URL")
    pat = _get_env_value("ADO_PAT", "AZURE_DEVOPS_EXT_PAT", "AZURE_DEVOPS_PAT")

    if not pat:
        raise EnvironmentError(
            "ADO_PAT or AZURE_DEVOPS_EXT_PAT environment variable is required"
        )

    if org_url:
        base_url = org_url.rstrip("/")
    elif organization:
        base_url = f"https://dev.azure.com/{organization.strip()}"
    else:
        raise EnvironmentError(
            "ADO_ORGANIZATION or ADO_ORG_URL environment variable is required"
        )

    return base_url, project, pat


def get_auth_header(pat: str) -> dict:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def determine_language(file_path: str) -> str | None:
    extension = Path(file_path).suffix.lower()
    return SUPPORTED_EXTENSIONS.get(extension)


def is_human_reviewer(comment: dict) -> bool:
    if comment.get("isSystem"):
        return False

    author = comment.get("author") or {}
    display_name = str(author.get("displayName", "")).strip().lower()
    if not display_name:
        return False

    return not any(keyword in display_name for keyword in SYSTEM_AUTHOR_KEYWORDS)


def extract_line_from_context(thread_context: dict) -> int | None:
    if not isinstance(thread_context, dict):
        return None

    for key in (
        "rightFileStart",
        "leftFileStart",
        "rightStartLine",
        "leftStartLine",
        "line",
    ):
        value = thread_context.get(key)
        if isinstance(value, dict) and value.get("line") is not None:
            return int(value["line"])
        if isinstance(value, int):
            return value

    return None


def normalize_file_path(file_path: str) -> str:
    if not file_path:
        return file_path
    return file_path if file_path.startswith("/") else "/" + file_path.lstrip("/")


def fetch_json(url: str, headers: dict, params: dict | None = None) -> dict:
    with httpx.Client(timeout=30.0, headers=headers) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def get_closed_prs(base_url: str, project: str | None, headers: dict) -> list[dict]:
    params = {
        "searchCriteria.status": "all",
        "api-version": "7.1-preview.1",
        "$top": 200,
    }
    project_path = f"{project}/" if project else ""
    url = f"{base_url}/{project_path}_apis/git/pullrequests"
    payload = fetch_json(url, headers, params=params)
    prs = payload.get("value", [])
    return [pr for pr in prs if pr.get("status") in {"completed", "abandoned"}]


def get_pr_threads(
    base_url: str, project: str | None, repository_id: str, pr_id: int, headers: dict
) -> list[dict]:
    project_path = f"{project}/" if project else ""
    url = f"{base_url}/{project_path}_apis/git/repositories/{repository_id}/pullRequests/{pr_id}/threads"
    params = {"api-version": "7.1-preview.1", "$top": 200}
    payload = fetch_json(url, headers, params=params)
    return payload.get("value", [])


def extract_pr_comments_from_ado(
    repo_path: str = ".", min_pr_id: int = 0
) -> list[dict]:
    base_url, project, pat = get_ado_config()
    headers = get_auth_header(pat)

    all_comments = []
    for pr in get_closed_prs(base_url, project, headers):
        pr_id = pr.get("pullRequestId") or pr.get("id")
        if pr_id is None or pr_id <= min_pr_id:
            continue

        repository = pr.get("repository") or {}
        repository_id = repository.get("id") or repository.get("name")
        if not repository_id:
            continue

        for thread in get_pr_threads(base_url, project, repository_id, pr_id, headers):
            thread_context = thread.get("threadContext") or {}
            file_path = normalize_file_path(thread_context.get("filePath", ""))
            if not file_path:
                continue

            line = extract_line_from_context(thread_context)

            for comment in thread.get("comments", []):
                if not is_human_reviewer(comment):
                    continue

                content = str(comment.get("content", "") or "").strip()
                if not content:
                    continue

                author = None
                comment_author = comment.get("author") or {}
                display_name = comment_author.get("displayName") or comment_author.get(
                    "uniqueName"
                )
                if display_name:
                    author = str(display_name).strip()

                language = determine_language(file_path)
                if language is None:
                    continue

                all_comments.append(
                    {
                        "file": file_path,
                        "line": line,
                        "content": content,
                        "pr_id": int(pr_id),
                        "language": language,
                        "type": "pr_comment",
                        "author": author,
                    }
                )

    return all_comments


if __name__ == "__main__":
    comments = extract_pr_comments_from_ado()
    for comment in comments[:20]:
        print(comment)
