import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import app.services.embeddings as emb
import app.services.wikipedia as wiki
from app.config import settings
from app.routes import api, pages


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
    await wiki.fetch_top_articles()   # warm the popularity cache before serving traffic
    refresh_task = asyncio.create_task(_daily_popularity_refresh())
    yield
    refresh_task.cancel()
    await wiki._client.aclose()
    wiki._rest_sem = None
    wiki._action_sem = None


def create_app() -> FastAPI:
    application = FastAPI(title="Rabbit Hole", lifespan=lifespan, debug=settings.debug)
    application.mount("/static", StaticFiles(directory="app/static"), name="static")
    application.include_router(pages.router)
    application.include_router(api.router)
    return application


app = create_app()
