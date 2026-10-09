import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.v1.router import api_router
from app.models import Product  # noqa: F401
from shared.database import get_engine, init_db


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.title,
        description=settings.description,
        version="1.0.0",
        contact=settings.contact,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(
        api_router,
        prefix=f"/api/{settings.api_version}",
    )
    application.include_router(
        api_router,
        include_in_schema=False,
    )

    return application


app = create_app()


@app.on_event("startup")
def on_startup() -> None:
    engine = get_engine(settings.db_name)
    init_db(engine)


@app.get("/health", tags=["Health"], include_in_schema=True)
def health() -> dict:
    return {
        "service": settings.service_name,
        "version": settings.api_version,
        "status": "ok",
    }


def start() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    start()
