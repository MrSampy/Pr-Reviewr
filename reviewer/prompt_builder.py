from typing import Any

_MAX_CHUNK_CHARS = 300
_MAX_DIFF_CHARS = 2000
_MAX_COMMENTS = 10
_MAX_CHUNKS = 5

_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert {language} code reviewer.

Analyze the provided diff and return a code review strictly as JSON — no markdown, no prose, no code fences.

Response schema:
{{
  "comments": [
    {{
      "file": "/path/to/file.cs",
      "line": 42,
      "severity": "critical|warning|suggestion",
      "problem": "description of the problem in English",
      "fix": "corrected code snippet"
    }}
  ]
}}

Rules:
- Comment only on lines present in the diff (lines starting with "+").
- Do not invent problems. If you are not confident, omit the comment.
- Return at most {max_comments} comments ordered by severity (critical first).
- If there are no problems, return {{"comments": []}}.
- Output raw JSON only — no markdown wrapper, no explanation outside the JSON object.\
"""


def format_chunks(chunks: list[dict[str, Any]]) -> str:
    """Format retriever chunks into a readable context block for the prompt."""
    if not chunks:
        return ""

    chunks = chunks[:_MAX_CHUNKS]
    pr_comments = [c for c in chunks if c.get("type") == "pr_comment"]
    code_chunks = [c for c in chunks if c.get("type") != "pr_comment"]

    parts: list[str] = []

    if pr_comments:
        parts.append("=== Past Review Comments ===")
        for chunk in pr_comments:
            header = f"// [pr_comment] File: {chunk.get('file', '')} | Line: {chunk.get('line', '?')}"
            content = chunk.get("content", "")
            if len(content) > _MAX_CHUNK_CHARS:
                content = content[:_MAX_CHUNK_CHARS] + "..."
            parts.append(f"{header}\n{content}")

    if code_chunks:
        parts.append("=== Similar Code in Codebase ===")
        for chunk in code_chunks:
            header = f"// [code] File: {chunk.get('file', '')} | Method: {chunk.get('method_name', '')}"
            content = chunk.get("content", "")
            if len(content) > _MAX_CHUNK_CHARS:
                content = content[:_MAX_CHUNK_CHARS] + "..."
            parts.append(f"{header}\n{content}")

    return "\n\n".join(parts)


def build(diff: str, chunks: list[dict[str, Any]], language: str) -> str:
    """Assemble the full LLM prompt for code review."""
    system = _SYSTEM_PROMPT_TEMPLATE.format(
        language=language,
        max_comments=_MAX_COMMENTS,
    )

    if len(diff) > _MAX_DIFF_CHARS:
        diff = diff[:_MAX_DIFF_CHARS] + "\n... [diff truncated]"

    context = format_chunks(chunks)

    sections: list[str] = [system]

    if context:
        sections.append("### Retrieved Context\n\n" + context)

    sections.append("### Diff to Review\n\n" + diff)

    return "\n\n---\n\n".join(sections)


def estimate_tokens(text: str) -> int:
    """Rough token count estimate (1 token ≈ 4 characters)."""
    return len(text) // 4


if __name__ == "__main__":
    _sample_chunks = [
        {
            "type": "pr_comment",
            "file": "/src/Services/OrderService.cs",
            "line": 47,
            "content": "This method does not validate the input before passing it to the repository.",
            "method_name": "",
            "distance": 0.12,
        },
        {
            "type": "code",
            "file": "/src/Services/OrderService.cs",
            "method_name": "CreateOrder",
            "content": "public Order CreateOrder(OrderDto dto)\n{\n    return _repo.Save(new Order(dto));\n}",
            "distance": 0.18,
        },
    ]

    _sample_diff = """\
diff --git a/src/Services/OrderService.cs b/src/Services/OrderService.cs
index 0000000..1111111 100644
--- a/src/Services/OrderService.cs
+++ b/src/Services/OrderService.cs
@@ -10,6 +10,11 @@ public class OrderService
+    public Order GetOrder(int id)
+    {
+        return _repository.FindById(id);
+    }
"""

    prompt = build(_sample_diff, _sample_chunks, language="csharp")
    print(prompt)
    print(f"\n--- estimated tokens: {estimate_tokens(prompt)} ---")
