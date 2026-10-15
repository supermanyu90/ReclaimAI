"""ReclaimAI worker. Consumes application events; Prompt 6 adds idempotent handlers."""


def main() -> None:
    import os

    import uvicorn

    uvicorn.run("reclaim_worker.app:app", host="127.0.0.1", port=int(os.environ.get("WORKER_PORT", "8101")))
