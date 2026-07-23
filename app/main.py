import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

import app.services.embeddings as emb
import app.services.wikipedia as wiki
from app.config import settings
from app.routes import api, pages


class NoCacheStaticFiles(StaticFiles):
    """Serve static assets with `Cache-Control: no-cache` so browsers always
    revalidate before use. StaticFiles still returns 304 when a file is
    unchanged, so this stays cheap — but a changed JS/CSS file is picked up
    immediately, preventing stale scripts from running against fresh markup.
    """

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


async def _daily_popularity_refresh() -> None:
    """Background task: refresh the popularity cache every 12 hours.
    Yesterday's daily pageview data doesn't change, but the date rolls over
    at midnight — refreshing twice a day ensures we pick up the new yesterday
    within 12 hours of each midnight.
    """
    while True:
        await asyncio.sleep(43_200)  # 12 hours
        await wiki.fetch_top_articles()


@asynccontextmanager
async def lifespan(app: FastAPI):
    wiki._client = httpx.AsyncClient(
        timeout=10.0,
        headers={"User-Agent": "RabbitHoleApp/1.0 (self-hosted Wikipedia recommender)"},
    )
    wiki.init_semaphore()
    emb.load_model()
    wiki.load_candidate_cache()        # warm search/category cache from disk before serving
    await wiki.fetch_top_articles()   # warm the popularity cache before serving traffic
    refresh_task = asyncio.create_task(_daily_popularity_refresh())
    yield
    refresh_task.cancel()
    wiki.save_candidate_cache()        # persist search/category cache for next startup
    await wiki._client.aclose()
    wiki._rest_sem = None
    wiki._action_sem = None


def create_app() -> FastAPI:
    application = FastAPI(title="Rabbit Hole", lifespan=lifespan, debug=settings.debug)
    application.mount("/static", NoCacheStaticFiles(directory="app/static"), name="static")
    application.mount("/media",  NoCacheStaticFiles(directory="app/media"),  name="media")
    application.include_router(pages.router)
    application.include_router(api.router)
    return application


app = create_app()
