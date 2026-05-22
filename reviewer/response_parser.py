import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = {"critical", "warning", "suggestion"}


def clean_json(raw: str) -> str:
    """Strip markdown fences and surrounding noise, leaving only the JSON object."""
    text = raw.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        # drop opening fence (```json or ```)
        lines = lines[1:]
        # drop closing fence if present
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text

    return text[start : end + 1]


def validate_comment(comment: dict, diff_files: list[str]) -> bool:
    """Return True if a comment dict passes all structural and content checks."""
    file_val = comment.get("file")
    if not file_val or not isinstance(file_val, str):
        return False

    # match by suffix: "/src/Services/OrderService.cs" matches "OrderService.cs"
    comment_path = Path(file_val)
    matched = any(
        comment_path == Path(df)
        or comment_path.parts[-len(Path(df).parts) :] == Path(df).parts
        for df in diff_files
    )
    if not matched:
        return False

    line = comment.get("line")
    if not isinstance(line, (int, float)) or int(line) <= 0:
        return False

    severity = comment.get("severity")
    if not isinstance(severity, str) or severity.lower() not in _VALID_SEVERITIES:
        return False

    problem = comment.get("problem")
    if not isinstance(problem, str) or len(problem) <= 10:
        return False

    fix = comment.get("fix")
    if not fix or not isinstance(fix, str) or not fix.strip():
        return False

    return True


def parse_response(raw: str, diff_files: list[str]) -> list[dict]:
    """Clean, parse, validate, and normalise the LLM JSON response."""
    cleaned = clean_json(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(
            "Failed to parse LLM response as JSON: %s\nRaw (cleaned): %r", exc, cleaned
        )
        return []

    if not isinstance(data, dict):
        logger.error(
            "Expected a JSON object at the top level, got %s", type(data).__name__
        )
        return []

    comments = data.get("comments")
    if not isinstance(comments, list):
        logger.error("'comments' field is missing or not a list")
        return []

    valid: list[dict] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        comment["severity"] = str(comment.get("severity", "")).lower()
        if validate_comment(comment, diff_files):
            valid.append(comment)

    return valid


def extract_diff_files(diff: str) -> list[str]:
    """Return the list of file paths modified in a unified diff."""
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[len("+++ b/") :].strip()
            if path:
                files.append(path)
    return files


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    _raw = """
Some preamble the model added by mistake.

```json
{
  "comments": [
    {
      "file": "/src/Services/OrderService.cs",
      "line": 15,
      "severity": "Warning",
      "problem": "The method does not validate the input parameter before use.",
      "fix": "if (id <= 0) throw new ArgumentException(nameof(id));"
    },
    {
      "file": "/src/Missing/Ghost.cs",
      "line": 0,
      "severity": "critical",
      "problem": "Short",
      "fix": ""
    }
  ]
}
```
"""

    _diff = """\
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

    diff_files = extract_diff_files(_diff)
    print(f"Files in diff: {diff_files}\n")

    results = parse_response(_raw, diff_files)
    print(f"Valid comments ({len(results)}):")
    for c in results:
        print(f"  [{c['severity']}] {c['file']}:{c['line']} — {c['problem']}")
