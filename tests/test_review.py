import json
import unittest
from unittest.mock import patch

from reviewer.pipeline import detect_language, review_diff

_DIFF_CS = """\
diff --git a/src/Program.cs b/src/Program.cs
index 0000000..1111111 100644
--- a/src/Program.cs
+++ b/src/Program.cs
@@ -5,6 +5,7 @@ public class Program
 {
+    private string _apiKey = "sk_live_abc123secretkey";
 }
"""

_DIFF_JS = """\
diff --git a/src/app.js b/src/app.js
index 0000000..1111111 100644
--- a/src/app.js
+++ b/src/app.js
@@ -1,3 +1,6 @@
+function getUser(id) {
+    return db.users.find(id).name;
+}
"""

_DIFF_MIXED = """\
diff --git a/src/Program.cs b/src/Program.cs
index 0000000..1111111 100644
--- a/src/Program.cs
+++ b/src/Program.cs
@@ -1,3 +1,4 @@
+// cs file
diff --git a/src/app.js b/src/app.js
index 0000000..1111111 100644
--- a/src/app.js
+++ b/src/app.js
@@ -1,3 +1,4 @@
+// js file
"""

_DIFF_UNSUPPORTED = """\
diff --git a/script.py b/script.py
index 0000000..1111111 100644
--- a/script.py
+++ b/script.py
@@ -1,3 +1,4 @@
+print("hello")
"""

_VALID_COMMENT = {
    "file": "src/Program.cs",
    "line": 6,
    "severity": "critical",
    "problem": "Hardcoded API key found in source code.",
    "fix": '_apiKey = Environment.GetEnvironmentVariable("API_KEY");',
}

_LLM_JSON = json.dumps({"comments": [_VALID_COMMENT]})


class DetectLanguageTests(unittest.TestCase):

    def test_csharp_diff(self):
        self.assertEqual(detect_language(_DIFF_CS), "csharp")

    def test_javascript_diff(self):
        self.assertEqual(detect_language(_DIFF_JS), "javascript")

    def test_tie_defaults_to_csharp(self):
        self.assertEqual(detect_language(_DIFF_MIXED), "csharp")

    def test_unsupported_defaults_to_csharp(self):
        self.assertEqual(detect_language(_DIFF_UNSUPPORTED), "csharp")


class ReviewDiffInputValidationTests(unittest.TestCase):

    def test_empty_diff_returns_empty(self):
        self.assertEqual(review_diff(""), [])

    def test_whitespace_diff_returns_empty(self):
        self.assertEqual(review_diff("   \n  "), [])

    def test_unsupported_extension_returns_empty(self):
        self.assertEqual(review_diff(_DIFF_UNSUPPORTED), [])


class ReviewDiffPipelineTests(unittest.TestCase):

    @patch("reviewer.pipeline.llm_review", return_value=_LLM_JSON)
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_returns_comments_on_valid_diff(self, *_):
        comments = review_diff(_DIFF_CS)
        self.assertIsInstance(comments, list)
        self.assertGreater(len(comments), 0)

    @patch("reviewer.pipeline.llm_review", return_value=_LLM_JSON)
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_comment_has_required_fields(self, *_):
        comments = review_diff(_DIFF_CS)
        self.assertTrue(len(comments) > 0)
        comment = comments[0]
        for field in ("file", "line", "severity", "problem", "fix"):
            self.assertIn(field, comment, msg=f"Missing field: {field}")

    @patch("reviewer.pipeline.llm_review", return_value=_LLM_JSON)
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_severity_is_lowercase(self, *_):
        comments = review_diff(_DIFF_CS)
        for c in comments:
            self.assertEqual(c["severity"], c["severity"].lower())

    @patch(
        "reviewer.pipeline.llm_review",
        side_effect=RuntimeError("Ollama is not running"),
    )
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_llm_error_returns_empty(self, *_):
        self.assertEqual(review_diff(_DIFF_CS), [])

    @patch(
        "reviewer.pipeline.llm_review",
        side_effect=TimeoutError("LLM timeout after 120s"),
    )
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_llm_timeout_returns_empty(self, *_):
        self.assertEqual(review_diff(_DIFF_CS), [])

    @patch("reviewer.pipeline.llm_review", return_value='{"comments": []}')
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_no_issues_returns_empty_list(self, *_):
        self.assertEqual(review_diff(_DIFF_CS), [])

    @patch("reviewer.pipeline.llm_review", return_value="not json at all")
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_malformed_llm_response_returns_empty(self, *_):
        self.assertEqual(review_diff(_DIFF_CS), [])

    @patch("reviewer.pipeline.llm_review", return_value=_LLM_JSON)
    @patch("reviewer.pipeline.retrieve", return_value=[])
    def test_javascript_diff_runs(self, _, mock_llm):
        js_comment = {**_VALID_COMMENT, "file": "src/app.js"}
        mock_llm.return_value = json.dumps({"comments": [js_comment]})
        comments = review_diff(_DIFF_JS)
        self.assertIsInstance(comments, list)


if __name__ == "__main__":
    unittest.main()
