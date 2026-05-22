import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .llm_client import review as llm_review
from .prompt_builder import build as build_prompt
from .prompt_builder import estimate_tokens
from .response_parser import extract_diff_files, parse_response
from .retriever import retrieve

logger = logging.getLogger(__name__)

_CS_SUFFIXES = {".cs", ".cshtml"}
_JS_SUFFIXES = {".js"}
_SUPPORTED_SUFFIXES = _CS_SUFFIXES | _JS_SUFFIXES
_MAX_HUNK_CHARS = 3000  # max diff chars per LLM call; oversized files split by hunks
_MAX_WORKERS = 4
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "suggestion": 2}


def detect_language(diff: str) -> str:
    cs_count = 0
    js_count = 0
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split(" b/", 1)
        if len(parts) < 2:
            continue
        suffix = Path(parts[1].strip()).suffix.lower()
        if suffix in _CS_SUFFIXES:
            cs_count += 1
        elif suffix in _JS_SUFFIXES:
            js_count += 1
    return "javascript" if js_count > cs_count else "csharp"


def _split_diff_by_file(diff: str) -> list[str]:
    """Split a unified diff string into one string per changed file."""
    files: list[str] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            files.append("".join(current))
            current = []
        current.append(line)
    if current:
        files.append("".join(current))
    return files


def _split_by_hunks(file_diff: str, max_chars: int) -> list[str]:
    """Split one file's diff into hunk groups that each fit within max_chars.

    Every chunk gets the file header lines (diff --git / index / --- / +++)
    prepended so the LLM always knows which file it is reviewing.
    """
    lines = file_diff.splitlines(keepends=True)

    header_lines: list[str] = []
    hunk_lines: list[str] = []
    in_hunks = False
    for line in lines:
        if line.startswith("@@"):
            in_hunks = True
        (hunk_lines if in_hunks else header_lines).append(line)

    header = "".join(header_lines)

    if not hunk_lines:
        return [file_diff]

    # Group lines into individual hunks (each starts at a @@ line)
    hunks: list[str] = []
    current: list[str] = []
    for line in hunk_lines:
        if line.startswith("@@") and current:
            hunks.append("".join(current))
            current = []
        current.append(line)
    if current:
        hunks.append("".join(current))

    # Pack hunks into chunks that respect max_chars
    chunks: list[str] = []
    batch: list[str] = []
    batch_size = len(header)
    for hunk in hunks:
        if batch and batch_size + len(hunk) > max_chars:
            chunks.append(header + "".join(batch))
            batch = []
            batch_size = len(header)
        batch.append(hunk)
        batch_size += len(hunk)
    if batch:
        chunks.append(header + "".join(batch))

    return chunks or [file_diff]


def _extract_file_path(chunk: str) -> str:
    """Return the ADO-style file path from a diff chunk's +++ b/... header line.

    ADO paths start with '/'. The diff header is built as f"b{path}", so stripping
    '+++ b' leaves the path intact — but only if the original path started with '/'.
    If it doesn't (shouldn't happen), we add it to stay consistent with ADO.
    """
    for line in chunk.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            return path if path.startswith("/") else f"/{path}"
    return ""


def _build_work_units(diff: str) -> list[tuple[str, str]]:
    """Return (chunk_diff, file_path) pairs, one per file (split by hunks if needed)."""
    units: list[tuple[str, str]] = []
    for file_diff in _split_diff_by_file(diff):
        file_path = _extract_file_path(file_diff)
        if not file_path or Path(file_path).suffix.lower() not in _SUPPORTED_SUFFIXES:
            continue
        chunks = (
            [file_diff]
            if len(file_diff) <= _MAX_HUNK_CHARS
            else _split_by_hunks(file_diff, _MAX_HUNK_CHARS)
        )
        for chunk in chunks:
            units.append((chunk, file_path))
    return units


def _review_chunk(
    chunk: str,
    file_path: str,
    language: str,
    rag_chunks: list[dict],
) -> list[dict]:
    prompt = build_prompt(chunk, rag_chunks, language)
    logger.debug("Chunk %s ~%d tokens", file_path, estimate_tokens(prompt))
    try:
        raw = llm_review(prompt)
    except Exception as exc:
        logger.error("LLM call failed for %s: %s", file_path, exc)
        return []

    comments = parse_response(raw, [file_path])

    # Strict: drop any comment the model assigned to a different file
    valid = [c for c in comments if c.get("file") == file_path]
    dropped = len(comments) - len(valid)
    if dropped:
        logger.warning(
            "Dropped %d comment(s) with wrong file path in chunk for %s",
            dropped,
            file_path,
        )
    return valid


def review_diff(diff: str) -> list[dict]:
    """Split diff by file and hunk, review chunks in parallel, return deduplicated comments."""
    if not diff or not diff.strip():
        return []

    diff_files = extract_diff_files(diff)
    if not any(Path(f).suffix.lower() in _SUPPORTED_SUFFIXES for f in diff_files):
        return []

    rag_chunks = retrieve(diff)
    logger.info("RAG retrieved %d chunks", len(rag_chunks))

    language = detect_language(diff)
    work_units = _build_work_units(diff)
    logger.info(
        "Reviewing %d chunk(s) with max_workers=%d", len(work_units), _MAX_WORKERS
    )

    seen: set[tuple[str, int]] = set()
    all_comments: list[dict] = []

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_review_chunk, chunk, file_path, language, rag_chunks): (
                chunk,
                file_path,
            )
            for chunk, file_path in work_units
        }
        for future in as_completed(futures):
            for comment in future.result():
                key = (comment.get("file", ""), int(comment.get("line", 0)))
                if key not in seen:
                    seen.add(key)
                    all_comments.append(comment)

    all_comments.sort(
        key=lambda c: _SEVERITY_ORDER.get(c.get("severity", "suggestion"), 2)
    )
    logger.info(
        "Review produced %d valid comment(s) across %d chunk(s)",
        len(all_comments),
        len(work_units),
    )
    return all_comments


if __name__ == "__main__":  # run as: python -m reviewer.pipeline [diff_file]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    _TEST_DIFF = """\
diff --git a/src/Services/OrderService.cs b/src/Services/OrderService.cs
index 0000000..1111111 100644
--- a/src/Services/OrderService.cs
+++ b/src/Services/OrderService.cs
@@ -10,6 +10,10 @@ public class OrderService
+    public Order GetOrder(int id)
+    {
+        return _repository.FindById(id);
+    }
"""

    if len(sys.argv) > 1:
        diff_path = Path(sys.argv[1])
        if not diff_path.exists():
            print(f"File not found: {diff_path}", file=sys.stderr)
            sys.exit(1)
        raw_bytes = diff_path.read_bytes()
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                diff_text = raw_bytes.decode(enc)
                break
            except (UnicodeDecodeError, ValueError):
                continue
        else:
            print(
                f"Cannot decode file {diff_path}: unsupported encoding", file=sys.stderr
            )
            sys.exit(1)
    else:
        print("No diff file specified — using built-in test diff.\n")
        diff_text = _TEST_DIFF

    results = review_diff(diff_text)

    if not results:
        print("No issues found.")
    else:
        print(f"\n{'─' * 60}")
        for comment in results:
            severity = comment.get("severity", "?").upper()
            file_ = comment.get("file", "")
            line = comment.get("line", "?")
            problem = comment.get("problem", "")
            fix = comment.get("fix", "")
            print(f"[{severity}] {file_}:{line} — {problem}")
            print(f"  Fix: {fix}")
            print(f"{'─' * 60}")
