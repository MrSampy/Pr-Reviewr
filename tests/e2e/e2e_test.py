import sys
import time

from ado_client.ado_client import get_pr_diff, get_pr_threads, post_review_comments
from reviewer.pipeline import review_diff

_BOT_TAG = "<!-- pr-reviewr -->"


def _step(label: str) -> float:
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    return time.time()


def _elapsed(t0: float) -> str:
    return f"{time.time() - t0:.2f}s"


def main(pr_id: int) -> None:
    total_start = time.time()

    # ------------------------------------------------------------------
    t0 = _step("Step 1 — Fetching PR diff")
    diff = get_pr_diff(pr_id)
    print(f"Diff size: {len(diff)} chars")
    if diff:
        changed = {
            line[6:].split("\t")[0]
            for line in diff.splitlines()
            if line.startswith("+++ b/")
        }
        print("Changed files:")
        for f in sorted(changed):
            print(f"  {f}")
    else:
        print("Diff is empty — nothing to review.")
        print(f"\nTotal time: {_elapsed(total_start)}")
        return
    print(f"[{_elapsed(t0)}]")

    # ------------------------------------------------------------------
    t0 = _step("Step 2 — Running review")
    comments = review_diff(diff)
    print(f"Found {len(comments)} comment(s):\n")
    if comments:
        col = {"critical": "CRIT", "warning": "WARN", "suggestion": "SUGG"}
        for c in comments:
            sev = c.get("severity", "suggestion").lower()
            tag = col.get(sev, sev.upper()[:4])
            file_ = c.get("file", "?")
            line = c.get("line", "?")
            problem = c.get("problem", "")
            print(f"  [{tag}] {file_}:{line} — {problem}")
    else:
        print("  (no issues found)")
    print(f"[{_elapsed(t0)}]")

    if not comments:
        print(f"\nTotal time: {_elapsed(total_start)}")
        return

    # ------------------------------------------------------------------
    _step("Step 3 — Confirm posting")
    answer = (
        input(f"Post {len(comments)} comment(s) to PR #{pr_id}? [y/N] ").strip().lower()
    )
    if answer != "y":
        print("Aborted.")
        print(f"\nTotal time: {_elapsed(total_start)}")
        return

    # ------------------------------------------------------------------
    t0 = _step("Step 4 — Posting comments")
    post_review_comments(pr_id, comments)
    print(f"[{_elapsed(t0)}]")

    # ------------------------------------------------------------------
    t0 = _step("Step 5 — Verifying comments in PR")
    threads = get_pr_threads(pr_id)
    bot_threads = [
        t
        for t in threads
        if any(_BOT_TAG in (c.get("content") or "") for c in t.get("comments", []))
    ]
    print(f"Found {len(bot_threads)} bot comment(s) in PR #{pr_id}")
    print(f"[{_elapsed(t0)}]")

    print(f"\n{'='*60}")
    print(f"  Total time: {_elapsed(total_start)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tests/e2e/e2e_test.py <pr_id>")
        sys.exit(1)
    main(int(sys.argv[1]))
