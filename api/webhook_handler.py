import logging
import os
import sys
import traceback

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

# Make project root importable when run directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ado_client.ado_client import get_pr_diff, post_review_comments
from reviewer.pipeline import review_diff

load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

_WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
_SUPPORTED_EVENTS = {"git.pullrequest.created", "git.pullrequest.updated"}

app = FastAPI()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0"}


def validate_secret(request: Request) -> bool:
    if not _WEBHOOK_SECRET:
        return True
    candidate = request.headers.get("X-Hub-Signature") or request.headers.get(
        "Authorization", ""
    )
    return candidate == _WEBHOOK_SECRET


def process_pr_event(payload: dict) -> None:
    try:
        event_type: str = payload["eventType"]
        resource: dict = payload["resource"]
        pr_id: int = resource["pullRequestId"]
        pr_status: str = resource.get("status", "")

        logger.info(
            "Processing event=%s pr_id=%d status=%s", event_type, pr_id, pr_status
        )

        if pr_status != "active":
            logger.info("Skipping PR %d — status is %r", pr_id, pr_status)
            return

        diff = get_pr_diff(pr_id)
        if not diff:
            logger.info("PR %d produced an empty diff — nothing to review", pr_id)
            return

        logger.info("PR %d diff size: %d chars", pr_id, len(diff))

        comments = review_diff(diff)
        logger.info("PR %d review produced %d comment(s)", pr_id, len(comments))

        if comments:
            post_review_comments(pr_id, comments)

    except Exception:
        logger.error("Error processing PR event:\n%s", traceback.format_exc())


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    if not validate_secret(request):
        return JSONResponse(status_code=403, content={"error": "Invalid secret"})

    payload: dict = await request.json()
    event_type: str = payload.get("eventType", "")

    logger.info("Received webhook event=%r", event_type)

    if event_type not in _SUPPORTED_EVENTS:
        return JSONResponse(content={"status": "ignored"})

    background_tasks.add_task(process_pr_event, payload)
    return JSONResponse(content={"status": "accepted"})


if __name__ == "__main__":
    uvicorn.run("webhook_handler:app", host="0.0.0.0", port=8080, reload=True)
