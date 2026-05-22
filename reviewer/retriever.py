import os
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv

from indexer.embedder import embed_texts

load_dotenv()

_LANGUAGE_MAP: dict[str, str] = {
    ".cs": "csharp",
    ".cshtml": "csharp",
    ".js": "javascript",
}


def _get_collection() -> chromadb.Collection:
    host = os.getenv("CHROMA_HOST", "localhost")
    port = int(os.getenv("CHROMA_PORT", "8000"))
    name = os.getenv("CHROMA_COLLECTION_NAME", "pr-reviewr")
    try:
        client = chromadb.HttpClient(host=host, port=port)
        return client.get_collection(name)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot connect to ChromaDB at {host}:{port} (collection '{name}'): {exc}"
        ) from exc


def parse_diff(diff: str) -> list[dict[str, Any]]:
    """Parse a unified diff and return per-file chunks for supported languages."""
    if not diff or not diff.strip():
        return []

    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current is not None and current["added_lines"]:
                files.append(current)
            current = None

            # extract the b/ path
            parts = line.split(" b/", 1)
            if len(parts) < 2:
                continue
            file_path = parts[1].strip()
            ext = Path(file_path).suffix.lower()
            language = _LANGUAGE_MAP.get(ext)
            if language is None:
                continue

            current = {
                "file": file_path,
                "language": language,
                "added_lines": [],
                "diff_chunk": [],
            }

        elif current is not None:
            current["diff_chunk"].append(line)
            if line.startswith("+") and not line.startswith("+++"):
                current["added_lines"].append(line[1:])

    if current is not None and current["added_lines"]:
        files.append(current)

    for f in files:
        f["diff_chunk"] = "\n".join(f["diff_chunk"])

    return files


def retrieve(diff: str) -> list[dict[str, Any]]:
    """Embed each changed file and query ChromaDB for similar code chunks."""
    parsed = parse_diff(diff)
    if not parsed:
        return []

    collection = _get_collection()

    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    for file_entry in parsed:
        query_text = "\n".join(file_entry["added_lines"])
        language = file_entry["language"]

        vectors = embed_texts([query_text])
        query_vector = vectors[0]

        response = collection.query(
            query_embeddings=[query_vector],  # type: ignore[arg-type]
            n_results=10,
            where={"language": language},
            include=["documents", "metadatas", "distances"],
        )

        documents = response.get("documents") or [[]]
        metadatas = response.get("metadatas") or [[]]
        distances = response.get("distances") or [[]]

        for doc, meta, dist in zip(documents[0], metadatas[0], distances[0]):
            key = f"{meta.get('method_name', '')}|{meta.get('file', '')}"
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "content": doc,
                    "file": meta.get("file", ""),
                    "method_name": meta.get("method_name", ""),
                    "type": meta.get("type", "code"),
                    "distance": dist,
                }
            )

    results.sort(key=lambda r: r["distance"])
    return results


if __name__ == "__main__":
    _TEST_DIFF = """\
diff --git a/src/OrderService.cs b/src/OrderService.cs
index 0000000..1111111 100644
--- a/src/OrderService.cs
+++ b/src/OrderService.cs
@@ -1,5 +1,10 @@
 public class OrderService
 {
+    public Order GetOrder(int id)
+    {
+        return _repository.FindById(id);
+    }
+
     public void PlaceOrder(Order order)
     {
         _repository.Save(order);
"""

    chunks = retrieve(_TEST_DIFF)
    print(f"Retrieved {len(chunks)} unique chunks\n")
    for chunk in chunks[:5]:
        print(
            f"[{chunk['distance']:.4f}] {chunk['file']} :: {chunk['method_name']}\n"
            f"  {chunk['content'][:120]!r}\n"
        )
