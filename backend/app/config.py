"""Runtime configuration & credential detection.

Nothing here raises on a missing key. Each source and the LLM degrade
gracefully; the UI is told what's live vs mock.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    # --- Reddit (public API, OAuth app creds) ---
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "market-gap-finder/0.1 (by u/anonymous)"

    # --- arXiv needs no key (open API) ---

    # --- Hacker News (Algolia API, keyless) ---
    hackernews_hits: int = 40

    # --- GitHub trending (keyless; optional token raises the rate limit) ---
    github_token: str | None = None
    github_max_repos: int = 30

    # --- Newsletters (RSS via feedparser; configurable feed list) ---
    newsletter_feeds: list[str] = [
        "https://tldr.tech/api/rss/tech",
        "https://bensbites.beehiiv.com/feed",
        "https://newsletter.pragmaticengineer.com/feed",
        "https://importai.substack.com/feed",
        "https://simonwillison.net/atom/everything/",
        "https://a16z.com/feed/",
        "https://www.lennysnewsletter.com/feed",
    ]
    newsletter_max_items: int = 40

    # --- Jobs (RemoteOK JSON + WeWorkRemotely RSS + HN who-is-hiring; keyless) ---
    jobs_max_items: int = 40
    jobs_max_requests: int = 5          # hard ceiling on external calls per fetch
    jobs_request_delay: float = 1.0     # seconds between calls (politeness)

    # --- App-store reviews (iTunes Search + customer-review RSS; keyless) ---
    appreviews_max_apps: int = 3        # top apps (by rating count) per segment
    appreviews_pages_per_app: int = 1   # RSS pages per app (50 reviews/page; API max 10)
    appreviews_max_items: int = 40
    appreviews_request_delay: float = 1.0

    # --- Regulatory (Federal Register API; keyless, ~1000 req/hr soft limit) ---
    regulatory_max_items: int = 20      # per_page (API max 1000; stay tiny + cache)

    # --- Outcomes (yc-oss static YC directory; ~2 MB JSON on GitHub Pages) ---
    outcomes_max_items: int = 25        # filtered companies surfaced per fetch

    # --- Post-mortems (curated corpus; no keyless live source exists) ---
    postmortems_max_items: int = 12

    # --- LLM ---
    anthropic_api_key: str | None = None
    # Force a specific LLM backend for testing: 'agent-sdk' | 'cli' | 'api' | 'fixture'
    llm_backend: str | None = None
    claude_cli_path: str = "claude"
    llm_model: str = "claude-opus-4-8"
    # Sampling temperature for the 'api' backend (env LLM_TEMPERATURE). None =
    # provider default. NOTE: recent models (Opus 4.7+/Fable 5) reject the
    # parameter outright, so it is only sent when explicitly set.
    llm_temperature: float | None = None

    # --- Cache ---
    cache_path: str = "cache.db"
    cache_ttl_hours: int = 24

    # --- Ingestion knobs ---
    reddit_subreddit_limit: int = 6
    reddit_post_limit: int = 60
    arxiv_max_results: int = 40
    arxiv_months_back: int = 18
    reddit_months_back: int = 18  # freshness floor: live posts older than this are dropped

    # --- Keyless Reddit (no OAuth app): public JSON + web-search discovery ---
    # Rate-limit discipline: a hard cap on external requests per run, a polite
    # delay between them, and immediate stop on HTTP 429. Results are cached, so
    # reruns/reweights cost nothing.
    reddit_max_requests: int = 6         # hard ceiling on external calls per fetch
    reddit_request_delay: float = 1.5    # seconds between requests (politeness)
    reddit_use_web_search: bool = True   # blend DuckDuckGo site:reddit.com discovery
    reddit_web_results: int = 2          # thread .json fetches from web-search hits

    # --- Space Watch (C2) — opt-in background sweep, default OFF ---
    # Hours between background source-only sweeps of watched nodes. None (the
    # default) means NO background task ever starts; sweeps are manual via
    # POST /api/watch/sweep. Env: WATCH_SWEEP_HOURS.
    watch_sweep_hours: float | None = None

    # --- Behavior ---
    allow_mock: bool = True  # when a key is absent, serve fixtures instead of failing

    # ---- convenience flags -------------------------------------------------
    @property
    def reddit_live(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    @property
    def arxiv_live(self) -> bool:
        return True  # no key required

    @property
    def hackernews_live(self) -> bool:
        return True  # keyless Algolia API

    @property
    def github_live(self) -> bool:
        return True  # keyless search API (token only raises the limit)

    @property
    def newsletter_live(self) -> bool:
        return bool(self.newsletter_feeds)

    @property
    def jobs_live(self) -> bool:
        return True  # keyless (RemoteOK JSON, WeWorkRemotely RSS, HN Algolia)

    @property
    def appreviews_live(self) -> bool:
        return True  # keyless iTunes Search + customer-review RSS

    @property
    def regulatory_live(self) -> bool:
        return True  # keyless Federal Register API

    @property
    def outcomes_live(self) -> bool:
        return True  # static yc-oss JSON on GitHub Pages, keyless

    @property
    def postmortems_live(self) -> bool:
        # No keyless, stable public source exists (Failory is JS-rendered,
        # CB Insights is one fragile editorial page). Curated corpus only —
        # honestly reported MOCK until a real live source appears.
        return False

    def resolve_llm_backend(self) -> str:
        """Pick the LLM backend. Order: explicit override -> API key -> agent SDK
        (Claude subscription) -> raw CLI -> fixture."""
        if self.llm_backend:
            return self.llm_backend
        if self.anthropic_api_key:
            return "api"
        # Prefer the Agent SDK (subscription auth + tool calling); the client
        # falls back to the raw CLI, then to a canned fixture, at call time.
        return "agent-sdk"


# --------------------------------------------------------------------------- #
# Selectable models. The synthesis step can run on any of these; the UI exposes #
# them in a picker. All run on the user's Claude subscription (agent-sdk / cli) #
# unless ANTHROPIC_API_KEY is set, in which case the same ids hit the API.      #
# --------------------------------------------------------------------------- #
AVAILABLE_MODELS: list[dict[str, str]] = [
    {"id": "claude-opus-4-8", "label": "Opus 4.8", "sub": "Deepest reasoning"},
    {"id": "claude-sonnet-5", "label": "Sonnet 5", "sub": "Balanced"},
    {"id": "claude-haiku-4-5-20251001", "label": "Haiku 4.5", "sub": "Fastest"},
]
_MODEL_IDS = {m["id"] for m in AVAILABLE_MODELS}


def resolve_model(requested: str | None) -> str:
    """Return a valid model id: the requested one if known, else the default."""
    if requested and requested in _MODEL_IDS:
        return requested
    return get_settings().llm_model


def _env_float(name: str) -> float | None:
    """Parse an optional float env var; a malformed value degrades to None."""
    raw = os.getenv(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings(
        reddit_client_id=os.getenv("REDDIT_CLIENT_ID"),
        reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        reddit_user_agent=os.getenv("REDDIT_USER_AGENT", Settings().reddit_user_agent),
        github_token=os.getenv("GITHUB_TOKEN"),
        newsletter_feeds=(
            [f.strip() for f in os.getenv("NEWSLETTER_FEEDS", "").split(",") if f.strip()]
            or Settings().newsletter_feeds
        ),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        llm_backend=os.getenv("LLM_BACKEND"),
        claude_cli_path=os.getenv("CLAUDE_CLI_PATH", "claude"),
        llm_model=os.getenv("LLM_MODEL", Settings().llm_model),
        llm_temperature=_env_float("LLM_TEMPERATURE"),
        cache_path=os.getenv("CACHE_PATH", "cache.db"),
        watch_sweep_hours=_env_float("WATCH_SWEEP_HOURS"),
        reddit_use_web_search=os.getenv("REDDIT_USE_WEB_SEARCH", "1") not in ("0", "false", "False"),
    )
