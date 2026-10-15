"""ReclaimAI API: authorization, workflow and orchestration."""


def main() -> None:
    import uvicorn

    from reclaim_api.config import get_settings

    settings = get_settings()
    uvicorn.run("reclaim_api.main:app", host=settings.api_host, port=settings.api_port)
