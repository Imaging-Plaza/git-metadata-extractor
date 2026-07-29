"""
API
"""

import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as package_version


def _normalize_github_token_pool() -> None:
    """Split a comma-separated GME_GITHUB_TOKEN into a per-process token pool.

    Runs before any gimie-related import so module-level `os.environ["GME_GITHUB_TOKEN"]`
    reads see a single valid token. The full list (deduped, order preserved) is
    exported as GME_GITHUB_TOKEN_POOL for v2 REST hot paths to round-robin over.
    """
    raw = os.environ.get("GME_GITHUB_TOKEN", "")
    if "," not in raw:
        return
    seen: set[str] = set()
    tokens: list[str] = []
    for piece in raw.split(","):
        token = piece.strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    if not tokens:
        return
    os.environ["GME_GITHUB_TOKEN_POOL"] = ",".join(tokens)
    os.environ["GME_GITHUB_TOKEN"] = tokens[0]


_normalize_github_token_pool()


def _resolve_package_version(name: str) -> str:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return "unknown"


from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from git_metadata_extractor.api import v2_router

from .log_context import AsyncRequestContext, setup_logging
from .rate_limit import rate_limit_middleware

# Setup enhanced logging with colors
# Allow LOG_LEVEL environment variable to override (DEBUG, INFO, WARNING, ERROR)
log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
setup_logging(level=log_level, use_colors=True)


logger = logging.getLogger(__name__)






async def startup_event(app: FastAPI | None = None):
    """Initialize resources on application startup."""
    logger.info("🚀 Application startup - initializing resources")

    # Pre-warm the v2 provider cache so the SQLite file exists in WAL mode
    # before any request hits a worker. Without this, multi-worker uvicorn
    # against a cold .cache/ races on first-request to create+init the
    # file and the losing workers raise `database is locked`, surfacing
    # as 500s for the first 1-2 jobs of a batch run.
    if app is not None:
        try:
            from git_metadata_extractor.dependencies import _resolve_provider_cache

            cache = _resolve_provider_cache(app.state)
            if cache is not None:
                logger.info("✅ v2 provider cache initialized")
        except Exception as exc:  # noqa: BLE001 — startup must not crash on cache issues
            logger.warning(f"v2 provider cache pre-warm skipped: {exc}")


async def shutdown_event():
    """Cleanup resources on application shutdown"""
    logger.info("🛑 Application shutdown - cleaning up resources")

    # Run garbage collection
    import gc

    gc.collect()
    logger.info("✅ Garbage collection completed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup_event(app)
    try:
        yield
    finally:
        await shutdown_event()


app = FastAPI(
    title="Git Metadata Extractor API",
    description="""
This API has been developed by the **Swiss Data Science Center (SDSC)** in collaboration with the **EPFL Center for Imaging** for use on [imaging-plaza.epfl.ch](https://imaging-plaza.epfl.ch) and in collaboration with the **EPFL Open Science Office** for the **Open Pulse** project.

Turn a GitHub URL (repository / user / organization) into JSON-LD aligned
with the Open Pulse Ontology.

## Features

- **`POST /v2/extract`** — async extraction job (poll `GET /v2/jobs/{id}`);
  `GET /v2/extract/{path}` for synchronous single-repo runs
- **Multi-stage pipeline** — deterministic rules + provider lookups
  (GitHub REST, GIMIE sidecar, ROR, ORCID, Infoscience) + optional LLM
  agents (`agent_runtime`: `rule_based` | `hybrid` | `llm`)
- **RAG-assisted agents** — Qdrant indices built by the
  [open-pulse-sources](https://github.com/sdsc-ordes/open-pulse-sources)
  service enrich LLM reasoning
- **JSON-LD output** — `schema:SoftwareSourceCode`, `schema:Person`,
  `org:Organization`, `org:Membership`, `pulse:Contribution` graphs

The legacy v1 API was removed in 3.0.0 — see the CHANGELOG and
`docs/migration-v1-to-v2.md`.
    """,
    version="3.0.0",
    contact={
        "name": "EPFL Center for Imaging / SDSC",
        "url": "https://imaging-plaza.epfl.ch",
    },
    license_info={
        "name": "Apache-2.0 License",
        "url": "https://github.com/Imaging-Plaza/git-metadata-extractor/blob/main/LICENSE",
    },
    openapi_tags=[
        {"name": "System", "description": "System information and health checks"},
    ],
    lifespan=lifespan,
    docs_url=None,
)


_SWAGGER_DARK_CSS = (
    "https://cdn.jsdelivr.net/gh/Amoenus/SwaggerDark@master/SwaggerDark.css"
)
_SWAGGER_LIGHT_CSS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
_SWAGGER_BUNDLE_JS = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
)
_FAVICON_URL = "https://fastapi.tiangolo.com/img/favicon.png"


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui_html() -> HTMLResponse:
    title = f"{app.title} - Swagger UI"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="shortcut icon" href="{_FAVICON_URL}">
<script>
  (function () {{
    // Resolve theme override before stylesheets are parsed to avoid FOUC.
    var override = null;
    try {{ override = localStorage.getItem('docs-theme'); }} catch (e) {{}}
    if (override === 'dark') document.documentElement.classList.add('dark');
    if (override === 'light') document.documentElement.classList.add('light');
    window.__docsThemeOverride = override;
  }})();
</script>
<link rel="stylesheet" href="{_SWAGGER_LIGHT_CSS}">
<link id="swagger-dark-css" rel="stylesheet" href="{_SWAGGER_DARK_CSS}"
      media="(prefers-color-scheme: dark)">
<style>
  #theme-toggle {{
    position: fixed; top: 12px; right: 16px; z-index: 9999;
    background: rgba(255,255,255,0.85); color: #222;
    border: 1px solid #ccc; border-radius: 999px;
    padding: 6px 12px; font-size: 14px; cursor: pointer;
    box-shadow: 0 1px 4px rgba(0,0,0,0.15); user-select: none;
  }}
  @media (prefers-color-scheme: dark) {{
    html:not(.light) #theme-toggle {{
      background: rgba(40,40,40,0.85); color: #eee; border-color: #555;
    }}
  }}
  html.dark #theme-toggle {{
    background: rgba(40,40,40,0.85); color: #eee; border-color: #555;
  }}
</style>
</head>
<body>
<button id="theme-toggle" type="button" aria-label="Toggle dark mode">Theme</button>
<div id="swagger-ui"></div>
<script src="{_SWAGGER_BUNDLE_JS}"></script>
<script>
  const ui = SwaggerUIBundle({{
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    layout: 'BaseLayout',
    deepLinking: true,
    showExtensions: true,
    showCommonExtensions: true,
    oauth2RedirectUrl: window.location.origin + '/docs/oauth2-redirect',
    presets: [
      SwaggerUIBundle.presets.apis,
      SwaggerUIBundle.SwaggerUIStandalonePreset
    ],
  }});
  (function () {{
    var darkLink = document.getElementById('swagger-dark-css');
    var btn = document.getElementById('theme-toggle');
    var html = document.documentElement;
    var systemDarkQuery = window.matchMedia
      ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    function activeTheme() {{
      if (html.classList.contains('dark')) return 'dark';
      if (html.classList.contains('light')) return 'light';
      return systemDarkQuery && systemDarkQuery.matches ? 'dark' : 'light';
    }}

    function apply(override) {{
      html.classList.remove('dark');
      html.classList.remove('light');
      if (override === 'dark') html.classList.add('dark');
      if (override === 'light') html.classList.add('light');

      // Force-on / force-off / system: tweak the media attribute on the
      // dark CSS so the override works without unloading the stylesheet.
      if (darkLink) {{
        if (override === 'dark') darkLink.media = 'all';
        else if (override === 'light') darkLink.media = 'not all';
        else darkLink.media = '(prefers-color-scheme: dark)';
      }}
      btn.textContent = activeTheme() === 'dark' ? '☀️ Light' : '🌙 Dark';
    }}

    apply(window.__docsThemeOverride);

    btn.addEventListener('click', function () {{
      var next = activeTheme() === 'dark' ? 'light' : 'dark';
      try {{ localStorage.setItem('docs-theme', next); }} catch (e) {{}}
      apply(next);
    }});

    if (systemDarkQuery && systemDarkQuery.addEventListener) {{
      systemDarkQuery.addEventListener('change', function () {{
        var stored = null;
        try {{ stored = localStorage.getItem('docs-theme'); }} catch (e) {{}}
        if (!stored) apply(null);
      }});
    }}
  }})();
</script>
</body>
</html>"""
    return HTMLResponse(html)

app.include_router(v2_router)

# Opt-in per-client rate limiting on the compute-heavy extraction routes.
# Disabled unless V2_RATE_LIMIT_PER_MINUTE is set. Registered
# before the context middleware so it short-circuits abusive requests early.
app.middleware("http")(rate_limit_middleware)


# Add middleware to automatically set request context for all endpoints
@app.middleware("http")
async def add_request_context(request: Request, call_next):
    """Middleware to add request ID to all endpoint logs"""
    # Determine prefix based on endpoint path
    path = request.url.path
    if "/org/" in path:
        prefix = "org"
    elif "/user/" in path:
        prefix = "user"
    elif "/repository/" in path or "/extract/" in path:
        prefix = "repo"
    elif "/cache/" in path:
        prefix = "cache"
    else:
        prefix = "api"

    async with AsyncRequestContext(prefix=prefix):
        # Log the incoming request with method, path, and query params
        query_string = f"?{request.url.query}" if request.url.query else ""
        logger.info(f"📥 {request.method} {path}{query_string}")

        response = await call_next(request)

        # Log the response status
        logger.info(f"📤 Response: {response.status_code}")
        return response


@app.get("/", tags=["System"])
def index():
    """
    Get API welcome message and system information.

    Versions are resolved at runtime from installed package metadata
    (`git-metadata-extractor`, `gimie`). The LLM model line reports what the
    v2 runtime would resolve right now from `MODEL_CONFIGS`
    (`git_metadata_extractor/agents/llm/model_config.py`) given the credentials available in the
    environment.
    """
    from git_metadata_extractor.agents.llm.runtime import LLMRuntimeConfigError, V2LLMRuntime

    try:
        resolved = V2LLMRuntime()._resolve_model_config()  # noqa: SLF001
        provider = resolved.get("provider", "unknown")
        model_name = resolved.get("model", "unknown")
        llm_model = f"{provider}:{model_name}"
    except LLMRuntimeConfigError as exc:
        llm_model = f"unconfigured ({exc})"

    return {
        "title": (
            "Hello, welcome to the Git Metadata Extractor "
            f"v{_resolve_package_version('git-metadata-extractor')}. "
            f"Gimie Version {_resolve_package_version('gimie')}. "
            f"LLM Model {llm_model}"
        ),
    }


@app.exception_handler(ValueError)
async def value_error_exception_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"message": str(exc)},
    )
