import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import indexer.ado_extractor as ado_extractor
import indexer.chunker as chunker
import indexer.code_extractor as code_extractor
import indexer.embedder as embedder
import indexer.incremental_indexer as incremental_indexer
import indexer.pipeline as pipeline
import indexer.state as state

HAS_GIT = shutil.which("git") is not None


def init_git_repo(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo_path, check=True
    )


def create_file(repo_path: Path, file_name: str, content: str) -> Path:
    file_path = repo_path / file_name
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


class CodeExtractorTests(unittest.TestCase):

    @unittest.skipUnless(HAS_GIT, "git is required for this test")
    def test_extract_code_from_repo_reads_files_and_git_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            init_git_repo(repo_path)
            create_file(repo_path, "src/Example.cs", "public class Example { }\n")
            subprocess.run(
                ["git", "add", "."], cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

            files = code_extractor.extract_code_from_repo(repo_path=str(repo_path))

            self.assertEqual(len(files), 1)
            item = files[0]
            self.assertEqual(item["path"], "src/Example.cs")
            self.assertEqual(item["language"], "csharp")
            self.assertEqual(item["content"].strip(), "public class Example { }")
            self.assertIsNotNone(item["author"])
            self.assertIsNotNone(item["last_modified"])

    def test_determine_language(self):
        self.assertEqual(code_extractor.determine_language(".cs"), "csharp")
        self.assertEqual(code_extractor.determine_language(".js"), "javascript")
        self.assertIsNone(code_extractor.determine_language(".py"))


class ChunkerTests(unittest.TestCase):

    def test_determine_language(self):
        self.assertEqual(chunker.determine_language(".cs"), "csharp")
        self.assertEqual(chunker.determine_language(".cshtml"), "csharp")
        self.assertEqual(chunker.determine_language(".js"), "javascript")
        self.assertIsNone(chunker.determine_language(".txt"))

    def test_chunk_code_from_string_csharp(self):
        code = """
public class Example
{
    public void DoWork()
    {
        var x = 1;
    }
}
"""
        chunks = chunker.chunk_code_from_string(code, "csharp", "<test>")
        self.assertTrue(any("DoWork" in chunk["method_name"] for chunk in chunks))
        self.assertTrue(all(chunk["language"] == "csharp" for chunk in chunks))
        self.assertTrue(all(chunk["content"].strip() for chunk in chunks))


class EmbedderTests(unittest.TestCase):

    def test_get_ollama_config_defaults(self):
        env = os.environ.copy()
        os.environ.pop("OLLAMA_API_URL", None)
        os.environ.pop("OLLAMA_URL", None)
        os.environ.pop("OLLAMA_EMBED_MODEL", None)

        config = embedder.get_ollama_config()
        self.assertEqual(config["api_url"], "http://127.0.0.1:11434")
        self.assertEqual(config["model"], "nomic-embed-text")

        os.environ.update(env)

    def test_batch_helper(self):
        self.assertEqual(
            list(embedder._batch([1, 2, 3, 4, 5], 2)), [[1, 2], [3, 4], [5]]
        )

    @patch("embedder.httpx.Client")
    def test_embed_chunks_posts_to_ollama(self, mock_client_class):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"embedding": [0.1]}, {"embedding": [0.2]}]
        }
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value.__enter__.return_value = mock_client

        chunks = [{"content": "hello"}, {"content": "world"}]
        embeddings = embedder.embed_chunks(chunks, batch_size=2)

        self.assertEqual(embeddings, [[0.1], [0.2]])
        mock_client.post.assert_called_once()


class ADOExtractorTests(unittest.TestCase):

    def test_is_human_reviewer_filters_system_authors(self):
        comment = {"isSystem": True, "author": {"displayName": "Bot User"}}
        self.assertFalse(ado_extractor.is_human_reviewer(comment))

        comment = {"isSystem": False, "author": {"displayName": "Build Bot"}}
        self.assertFalse(ado_extractor.is_human_reviewer(comment))

        comment = {"isSystem": False, "author": {"displayName": "Jane Reviewer"}}
        self.assertTrue(ado_extractor.is_human_reviewer(comment))

    def test_normalize_file_path(self):
        self.assertEqual(
            ado_extractor.normalize_file_path("src/example.cs"), "/src/example.cs"
        )
        self.assertEqual(
            ado_extractor.normalize_file_path("/src/example.cs"), "/src/example.cs"
        )
        self.assertEqual(ado_extractor.normalize_file_path(""), "")

    def test_extract_line_from_context(self):
        self.assertEqual(
            ado_extractor.extract_line_from_context({"rightFileStart": {"line": 10}}),
            10,
        )
        self.assertEqual(ado_extractor.extract_line_from_context({"line": 5}), 5)
        self.assertIsNone(ado_extractor.extract_line_from_context({"other": 1}))

    def test_determine_language(self):
        self.assertEqual(ado_extractor.determine_language("/path/file.cs"), "csharp")
        self.assertEqual(
            ado_extractor.determine_language("/path/file.js"), "javascript"
        )
        self.assertIsNone(ado_extractor.determine_language("/path/file.txt"))


class StateTests(unittest.TestCase):

    def test_state_persistence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_db = Path(temp_dir) / "indexer_state.db"
            original_db = state.DB_PATH
            state.DB_PATH = temp_db
            try:
                state.set_last_commit("abc123")
                self.assertEqual(state.get_last_commit(), "abc123")
                state.set_last_pr_id(42)
                self.assertEqual(state.get_last_pr_id(), 42)
            finally:
                state.DB_PATH = original_db


class IncrementalIndexerTests(unittest.TestCase):

    @unittest.skipUnless(HAS_GIT, "git is required for this test")
    def test_get_changed_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            init_git_repo(repo_path)
            file_path = create_file(
                repo_path, "Example.cs", "public class Example { }\n"
            )
            subprocess.run(
                ["git", "add", "."], cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            last_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            file_path.write_text(
                "public class Example { public void DoWork() { } }\n", encoding="utf-8"
            )
            subprocess.run(
                ["git", "add", "Example.cs"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Update file"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

            changed = incremental_indexer.get_changed_files(last_commit, str(repo_path))
            self.assertIn("Example.cs", changed)

    def test_reindex_files_deletes_and_adds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            file_name = "Example.cs"
            create_file(repo_path, file_name, "public class Example { }\n")

            collection = MagicMock()
            collection.delete = MagicMock()
            collection.add = MagicMock()

            with (
                patch(
                    "incremental_indexer.chunker.determine_language",
                    return_value="csharp",
                ),
                patch(
                    "incremental_indexer.chunker.chunk_source",
                    return_value=[
                        {
                            "content": "public void DoWork() {}",
                            "method_name": "DoWork",
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                ),
                patch(
                    "incremental_indexer.code_extractor.get_git_info",
                    return_value=("2026-01-01", "Test User"),
                ),
                patch(
                    "incremental_indexer.embedder.embed_chunks",
                    return_value=[[0.1, 0.2]],
                ),
            ):
                incremental_indexer.reindex_files(
                    [file_name], str(repo_path), collection
                )

            collection.delete.assert_called_once_with(where={"file": "/Example.cs"})
            collection.add.assert_called_once()
            args, kwargs = collection.add.call_args
            self.assertEqual(kwargs["ids"][0], "code|/Example.cs|0|0")
            self.assertEqual(kwargs["documents"][0], "public void DoWork() {}")
            self.assertEqual(kwargs["metadatas"][0]["file"], "/Example.cs")
            self.assertEqual(kwargs["metadatas"][0]["language"], "csharp")

    def test_update_pr_comments_adds_new_comments(self):
        collection = MagicMock()
        collection.add = MagicMock()

        with (
            patch("incremental_indexer.state.get_last_pr_id", return_value=5),
            patch(
                "incremental_indexer.ado_extractor.extract_pr_comments_from_ado",
                return_value=[
                    {
                        "file": "/Example.cs",
                        "content": "Review this",
                        "author": "Reviewer",
                        "language": "csharp",
                        "line": 10,
                        "pr_id": 6,
                    },
                ],
            ),
            patch(
                "incremental_indexer.embedder.embed_chunks", return_value=[[0.1, 0.2]]
            ),
            patch("incremental_indexer.state.set_last_pr_id") as mock_set_last_pr_id,
        ):
            incremental_indexer.update_pr_comments(collection)

        collection.add.assert_called_once()
        mock_set_last_pr_id.assert_called_once_with(6)


class PipelineTests(unittest.TestCase):

    def test_normalize_file_path(self):
        self.assertEqual(
            pipeline.normalize_file_path("src\\Example.cs"), "/src/Example.cs"
        )
        self.assertEqual(
            pipeline.normalize_file_path("/src/Example.cs"), "/src/Example.cs"
        )

    @unittest.skipUnless(HAS_GIT, "git is required for this test")
    def test_get_current_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_path = Path(temp_dir)
            init_git_repo(repo_path)
            create_file(repo_path, "Example.cs", "public class Example { }\n")
            subprocess.run(
                ["git", "add", "."], cwd=repo_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

            commit_hash = pipeline.get_current_commit(str(repo_path))
            self.assertTrue(len(commit_hash) == 40)


if __name__ == "__main__":
    unittest.main()
