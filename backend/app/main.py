"""
Cerebro API — application entry point.

Serves the JSON API plus three built-in screens:

* ``/``         dashboard: live context, recent events, knowledge, logs
* ``/setup``    first-run wizard
* ``/settings`` full configuration centre
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import ROUTERS
from app.api.system import VERSION
from app.core import init_db, logger, settings
from app.services import watchers
from app.core.paths import WEB_DIR

STARTUP_BANNER = r"""
   ___                _
  / __|___ _ _ ___ _ | |_ _ _ ___
 | (__/ -_) '_/ -_) '_| '_/ _ \_/
  \___\___|_| \___|_| |_| \___/
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Sweeps the Power Automate inbox folder in the background when the bridge
    # is enabled, so nobody has to keep an importer console open.
    watchers.start()

    url = f"http://{'localhost' if settings.HOST in ('0.0.0.0', '127.0.0.1') else settings.HOST}:{settings.PORT}"
    print(STARTUP_BANNER)
    print(f"  Cerebro {VERSION} — {settings.ENVIRONMENT}")
    print(f"  Dashboard   {url}/")
    print(f"  {'Setup' if not settings.SETUP_COMPLETED else 'Settings'}       {url}/{'setup' if not settings.SETUP_COMPLETED else 'settings'}")
    print(f"  API docs    {url}/docs")
    print(f"  Database    {'SQLite (built-in)' if settings.using_sqlite else settings.DATABASE_URL.split('@')[-1]}")
    if not settings.SETUP_COMPLETED:
        print("\n  First run? Open the setup page above — it takes about a minute.\n")
    else:
        print()

    logger.info("startup", "Cerebro started", {
        "version": VERSION,
        "host": settings.HOST,
        "port": settings.PORT,
        "database": "sqlite" if settings.using_sqlite else "external",
    })

    yield

    watchers.stop()
    logger.info("shutdown", "Cerebro stopped")


app = FastAPI(
    title=f"{settings.APP_NAME} API",
    description=(
        "Local-first operational copilot for technical support.\n\n"
        "Open [/setup](/setup) to configure Cerebro, or [/](/) for the dashboard."
    ),
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ROUTERS:
    app.include_router(router)

if (WEB_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception):
    """Return actionable JSON instead of an opaque stack trace."""
    logger.error("http", "Unhandled error", {"path": str(request.url.path), "error": str(exc)})
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc),
            "hint": "Check the log at /api/system/logs or run: python cerebro.py doctor",
        },
    )


def _page(name: str) -> FileResponse:
    return FileResponse(str(WEB_DIR / name), media_type="text/html")


@app.get("/", include_in_schema=False)
async def dashboard():
    if not settings.SETUP_COMPLETED:
        return RedirectResponse("/setup")
    return _page("dashboard.html")


@app.get("/setup", include_in_schema=False)
async def setup_page():
    return _page("setup.html")


@app.get("/settings", include_in_schema=False)
async def settings_page():
    return _page("settings.html")


@app.get("/health", tags=["system"])
async def health():
    """Liveness probe. Kept at the root path for load balancers and the widget."""
    return {"status": "healthy", "version": VERSION}


def run() -> None:
    """Entry point used by ``python cerebro.py start``."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )


if __name__ == "__main__":
    run()
