import logging
import sys
from pathlib import Path

from llm_client import review as llm_review
from prompt_builder import build as build_prompt
from prompt_builder import estimate_tokens
from response_parser import extract_diff_files, parse_response
from retriever import retrieve

logger = logging.getLogger(__name__)

_CS_SUFFIXES = {".cs", ".cshtml"}
_JS_SUFFIXES = {".js"}


def detect_language(diff: str) -> str:
    """Return the dominant language in the diff; csharp wins ties."""
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


def review_diff(diff: str) -> list[dict]:
    """Run the full RAG + LLM code-review pipeline and return validated comments."""
    # Step 1: validate input
    if not diff or not diff.strip():
        return []

    diff_files = extract_diff_files(diff)
    supported = [
        f for f in diff_files
        if Path(f).suffix.lower() in _CS_SUFFIXES | _JS_SUFFIXES
    ]
    if not supported:
        return []

    # Step 2: RAG retrieval
    chunks = retrieve(diff)
    logger.info("RAG retrieved %d chunks", len(chunks))

    # Step 3: build prompt
    language = detect_language(diff)
    prompt = build_prompt(diff, chunks, language)
    logger.info("Prompt size: ~%d tokens", estimate_tokens(prompt))

    # Step 4: call LLM
    try:
        raw = llm_review(prompt)
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return []

    # Step 5: parse and validate
    comments = parse_response(raw, diff_files)
    logger.info("Review produced %d valid comment(s)", len(comments))
    return comments


if __name__ == "__main__":
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
            print(f"Cannot decode file {diff_path}: unsupported encoding", file=sys.stderr)
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
