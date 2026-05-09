import subprocess
from pathlib import Path
from typing import List

import ado_extractor
import chunker
import code_extractor
import embedder
import state

SUPPORTED_EXTENSIONS = {".cs", ".js", ".cshtml"}


def get_changed_files(last_commit: str, repo_path: str) -> List[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", last_commit, "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    files = result.stdout.strip().splitlines()
    return [f for f in files if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS]


def reindex_files(changed_files: List[str], repo_path: str, collection):
    for file in changed_files:
        normalized_file = f'/{file.replace("\\", "/").lstrip("/")}'
        collection.delete(where={"file": normalized_file})

        file_path = Path(repo_path) / file
        if not file_path.exists():
            continue

        language = chunker.determine_language(file_path.suffix.lower())
        if not language:
            continue

        chunks = chunker.chunk_source(str(file_path), language)
        last_modified, author = code_extractor.get_git_info(str(file_path))

        items = []
        for chunk_index, chunk in enumerate(chunks):
            items.append(
                {
                    "file": normalized_file,
                    "content": chunk["content"],
                    "author": author,
                    "type": "code",
                    "chunk_index": chunk_index,
                    "language": language,
                    "method_name": chunk.get("method_name"),
                    "start_line": chunk.get("start_line"),
                    "end_line": chunk.get("end_line"),
                    "pr_id": 0,
                }
            )

        if not items:
            continue

        embeddings = embedder.embed_chunks(items)
        ids = [
            f"code|{normalized_file}|{item['chunk_index']}|{idx}"
            for idx, item in enumerate(items)
        ]
        metadatas = [
            {
                "file": item["file"],
                "author": item.get("author") or "",
                "type": item["type"],
                "chunk_index": item["chunk_index"],
                "language": item.get("language") or "",
                "method_name": item.get("method_name") or "",
                "start_line": item.get("start_line") or 0,
                "end_line": item.get("end_line") or 0,
                "pr_id": item.get("pr_id") or 0,
            }
            for item in items
        ]
        documents = [item["content"] for item in items]
        collection.add(
            ids=ids, metadatas=metadatas, documents=documents, embeddings=embeddings
        )


def update_pr_comments(collection):
    last_pr_id = state.get_last_pr_id()
    all_comments = ado_extractor.extract_pr_comments_from_ado(min_pr_id=last_pr_id)
    new_comments = [c for c in all_comments if c["pr_id"] > last_pr_id]
    if not new_comments:
        return

    items = []
    for chunk_index, comment in enumerate(new_comments):
        items.append(
            {
                "file": comment["file"],
                "content": comment["content"],
                "author": comment.get("author"),
                "type": "pr_comment",
                "chunk_index": chunk_index,
                "language": comment.get("language"),
                "method_name": None,
                "start_line": comment.get("line"),
                "end_line": comment.get("line"),
                "pr_id": comment.get("pr_id"),
            }
        )

    embeddings = embedder.embed_chunks(items)
    ids = [
        f"pr_comment|{item['file']}|{item['chunk_index']}|{idx}"
        for idx, item in enumerate(items)
    ]
    metadatas = [
        {
            "file": item["file"],
            "author": item.get("author") or "",
            "type": item["type"],
            "chunk_index": item["chunk_index"],
            "language": item.get("language") or "",
            "method_name": item.get("method_name") or "",
            "start_line": item.get("start_line") or 0,
            "end_line": item.get("end_line") or 0,
            "pr_id": item.get("pr_id") or 0,
        }
        for item in items
    ]
    documents = [item["content"] for item in items]
    collection.add(
        ids=ids, metadatas=metadatas, documents=documents, embeddings=embeddings
    )

    max_pr_id = max(item["pr_id"] or 0 for item in items)
    if max_pr_id:
        state.set_last_pr_id(max_pr_id)


def run(repo_path: str, collection):
    last_commit = state.get_last_commit()
    if last_commit:
        changed_files = get_changed_files(last_commit, repo_path)
        if changed_files:
            reindex_files(changed_files, repo_path, collection)
    update_pr_comments(collection)
