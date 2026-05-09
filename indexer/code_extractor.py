import os
import subprocess
from pathlib import Path

IGNORED_DIRS = {"node_modules", "bin", "obj", ".git"}


def get_git_info(file_path, repo_path="."):
    """Get last modified date and author from git log for a file."""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", "--", file_path],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        last_modified = result.stdout.strip() if result.returncode == 0 else None

        result = subprocess.run(
            ["git", "log", "-1", "--format=%an", "--", file_path],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        author = result.stdout.strip() if result.returncode == 0 else None

        return last_modified, author
    except Exception:
        return None, None


def determine_language(extension):
    """Determine language based on file extension."""
    if extension in [".cs", ".cshtml"]:
        return "csharp"
    elif extension == ".js":
        return "javascript"
    return None


def extract_code_from_repo(repo_path="."):
    """Extract code from repository files."""
    repo_path = Path(repo_path)
    files_data = []

    for root, dirs, files in os.walk(repo_path):
        # Remove ignored directories
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]

        for file in files:
            file_path = Path(root) / file
            extension = file_path.suffix.lower()

            if extension in [".cs", ".js", ".cshtml"]:
                try:
                    # Read content
                    with open(
                        file_path, "r", encoding="utf-8-sig", errors="ignore"
                    ) as f:
                        content = f.read()

                    # Get relative path
                    relative_path = file_path.relative_to(repo_path)
                    normalized_path = str(relative_path).replace(os.sep, "/")

                    # Get git info
                    last_modified, author = get_git_info(
                        str(relative_path), repo_path=str(repo_path)
                    )

                    # Determine language
                    language = determine_language(extension)

                    file_data = {
                        "path": normalized_path,
                        "content": content,
                        "last_modified": last_modified,
                        "language": language,
                        "author": author,
                    }

                    files_data.append(file_data)

                except Exception as e:
                    print(f"Error processing {file_path}: {e}")

    return files_data


if __name__ == "__main__":
    # Example usage
    files = extract_code_from_repo()
    for file in files[:5]:  # Print first 5 for testing
        print(file)
