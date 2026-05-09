import argparse
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List

import chromadb

from . import ado_extractor
from . import chunker
from . import code_extractor
from . import embedder
from . import incremental_indexer
from . import state

DEFAULT_COLLECTION_NAME = "pr-reviewr"
DEFAULT_BATCH_SIZE = 16


def _expand_items(items: Iterable[dict]) -> List[dict]:
    return list(items)


def _group_by_file(items: List[dict]) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for item in items:
        grouped.setdefault(item["file"], []).append(item)
    return grouped


def get_chroma_client(persist_directory: str | None = None) -> Any:
    if persist_directory:
        return chromadb.PersistentClient(path=persist_directory)
    return chromadb.HttpClient(host="localhost", port=8000)


def get_collection(client: Any, name: str) -> Any:
    if hasattr(client, "get_or_create_collection"):
        return client.get_or_create_collection(name=name)

    try:
        return client.get_collection(name=name)
    except Exception:
        return client.create_collection(name=name)


def get_current_commit(repo_path: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo_path
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _make_document_id(item: dict, index: int) -> str:
    return f"{item['type']}|{item['file']}|{item['chunk_index']}|{index}"


def _prepare_code_chunks(repo_path: str) -> List[dict]:
    items = []
    code_files = code_extractor.extract_code_from_repo(repo_path)
    for file_data in code_files:
        file_path = Path(repo_path) / file_data["path"]
        extension = Path(file_data["path"]).suffix.lower()
        language = chunker.determine_language(extension)
        if language is None:
            continue

        if file_path.exists():
            chunks = chunker.chunk_source(str(file_path), language)
        else:
            chunks = chunker.chunk_code_from_string(
                file_data["content"], language, file_path=file_data["path"]
            )

        for chunk_index, chunk in enumerate(chunks):
            items.append(
                {
                    "file": normalize_file_path(file_data["path"]),
                    "content": chunk["content"],
                    "author": file_data.get("author"),
                    "type": "code",
                    "chunk_index": chunk_index,
                    "language": language,
                    "method_name": chunk.get("method_name"),
                    "start_line": chunk.get("start_line"),
                    "end_line": chunk.get("end_line"),
                }
            )
    return items


def normalize_file_path(file_path: str) -> str:
    path = file_path.replace("\\", "/").lstrip("/")
    return f"/{path}" if not path.startswith("/") else path


def _prepare_pr_comment_items() -> List[dict]:
    items = []
    pr_comments = ado_extractor.extract_pr_comments_from_ado()
    grouped = _group_by_file(pr_comments)
    for file_path, comments in grouped.items():
        for chunk_index, comment in enumerate(comments):
            items.append(
                {
                    "file": normalize_file_path(file_path),
                    "content": comment["content"],
                    "author": comment.get("author"),
                    "type": "pr_comment",
                    "chunk_index": chunk_index,
                    "language": comment.get("language"),
                    "pr_id": comment.get("pr_id"),
                    "method_name": None,
                    "start_line": comment.get("line"),
                    "end_line": comment.get("line"),
                }
            )
    return items


def index_repository(
    repo_path: str = ".",
    collection_name: str = DEFAULT_COLLECTION_NAME,
    persist_directory: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    repo_path = str(Path(repo_path).resolve())

    code_items = _prepare_code_chunks(repo_path)
    pr_items = _prepare_pr_comment_items()
    all_items = code_items + pr_items

    if not all_items:
        raise RuntimeError(
            "No indexable items found. Check repository path and ADO configuration."
        )

    client = get_chroma_client(persist_directory)
    collection = get_collection(client, collection_name)

    documents = [item["content"] for item in all_items]
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
        for item in all_items
    ]
    ids = [_make_document_id(item, idx) for idx, item in enumerate(all_items)]

    embeddings = embedder.embed_chunks(all_items, batch_size=batch_size)
    collection.add(
        ids=ids, metadatas=metadatas, documents=documents, embeddings=embeddings
    )

    if persist_directory and hasattr(client, "persist"):
        client.persist()

    current_commit = get_current_commit(repo_path)
    if current_commit:
        state.set_last_commit(current_commit)

    max_pr_id = max((i.get("pr_id") or 0 for i in pr_items), default=0)
    if max_pr_id:
        state.set_last_pr_id(max_pr_id)


def index_repository_to_chroma_from_env(repo_path: str = ".") -> None:
    persist_directory = os.getenv("CHROMA_PERSIST_DIR")
    collection_name = os.getenv("CHROMA_COLLECTION_NAME", DEFAULT_COLLECTION_NAME)
    index_repository(
        repo_path=repo_path,
        collection_name=collection_name,
        persist_directory=persist_directory,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "incremental"], default="full")
    args = parser.parse_args()

    repo_path = os.getenv("REPO_PATH", ".")
    persist_directory = os.getenv("CHROMA_PERSIST_DIR")
    collection_name = os.getenv("CHROMA_COLLECTION_NAME", DEFAULT_COLLECTION_NAME)

    if args.mode == "incremental":
        last_commit = state.get_last_commit()
        if not last_commit:
            print("No previous index found, running full indexing...")
            index_repository(
                repo_path=repo_path,
                collection_name=collection_name,
                persist_directory=persist_directory,
            )
        else:
            client = get_chroma_client(persist_directory)
            collection = get_collection(client, collection_name)
            incremental_indexer.run(repo_path, collection)
    else:
        index_repository(
            repo_path=repo_path,
            collection_name=collection_name,
            persist_directory=persist_directory,
        )

    print("Indexing finished.")
