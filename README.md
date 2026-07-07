# Market Gap Finder

Surfaces **market gaps worth building a product around** by cross-referencing three
signals and letting Claude synthesize the whitespace:

- **Reddit** — real user pain & demand ("I wish…", "is there an app that…", 1–3★ complaints)
- **Hacker News** — tech demand (Ask HN pain), momentum (front-page velocity), and launches (Show HN)
- **arXiv** — research momentum = a *capability tailwind* (what just became feasible)
- **GitHub** — star velocity = what developers are actually adopting
- **Newsletters** — curated "what's new" across tech/AI (RSS)

The core thesis it operationalizes: a real gap sits where **demand is rising** but
**supply hasn't caught up**, usually unlocked by a **recent shift**.

You enter an area (e.g. *"personal finance for freelancers"*) and get a ranked,
visualized set of gap opportunities — each with its evidence, top-5 competitors, a
wedge, and the riskiest assumption to validate.

---

## Architecture

```
backend/                 FastAPI + Pydantic
  app/
    schemas.py           the data contract (LLM output shape + API payloads)
    config.py            env / credential detection (nothing fails on a missing key)
    cache.py             SQLite TTL cache (re-run / reweight without re-fetch)
    sources/             pluggable adapters — add a signal without a rewrite
      base.py            Source ABC + FetchResult
      reddit.py  hackernews.py  arxiv.py  github.py  newsletters.py
      registry.py        get_sources()
      fixtures/          realistic mock data (used when a source is blocked/absent)
    analysis/
      scope.py           area -> sub-segments + keywords
      extract.py         raw items -> demand / capability / supply signals
      synthesize.py      the creative engine (LLM + on-demand fetch tools)
      rank.py            weighted composite scoring
    llm/
      client.py          Claude via subscription (agent-sdk -> cli -> api -> fixture)
      fixture_synthesis.py   zero-dependency demo gaps
    pipeline.py          scope -> ingest -> extract -> synthesize -> rank
    routers/analyze.py   POST /api/analyze, /api/rerank, GET /api/health
    main.py
frontend/                React + Vite + Recharts
  src/
    App.tsx              the full flow
    components/          OpportunityMap (2×2 hero), GapTable, GapDetail,
                         AreaInput, SourceStatus, WeightControls, ModelPicker
```

## The LLM runs on your Claude *subscription* — no API key required

The synthesis step resolves a backend in this order and degrades gracefully:

1. **`api`** — Anthropic API, *only* if `ANTHROPIC_API_KEY` is set.
2. **`agent-sdk`** — [`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/),
   authenticated via your local **Claude Code** login. Supports **tool calling**, so
   Claude can pull more Reddit/arXiv/FRED data mid-synthesis to corroborate a gap.
3. **`cli`** — `claude -p` headless (single pass, subscription auth).
4. **`fixture`** — canned JSON, so the whole loop runs with **zero** dependencies.

The UI reports which backend actually served each run (`llm_mode`) and lets you
**pick the model** (Opus 4.8 / Sonnet 5 / Haiku 4.5) per run.

## Environment variables — all optional

Copy `.env.example` to `.env`. With **nothing set**, every source serves realistic
mock fixtures (labeled `mock` in the UI) and synthesis uses your Claude subscription,
so the full loop works out of the box. Add credentials to promote a source to `live`:

| Var | Source | Get it |
|-----|--------|--------|
| — | Hacker News | no key — keyless Algolia API |
| — | arXiv | no key needed |
| — | Reddit | works **keyless** (public JSON + web search); some IPs get 403 → falls back to mock |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Reddit (higher limits) | https://www.reddit.com/prefs/apps (type: script) |
| `GITHUB_TOKEN` | GitHub (higher limits) | keyless works; token raises the rate limit |
| `NEWSLETTER_FEEDS` | Newsletters | optional comma-separated RSS list (has sensible defaults) |
| `ANTHROPIC_API_KEY` | LLM (optional) | only if you'd rather bill the API than use the subscription |

## Run it

**Backend**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # add ".[api]" only if using ANTHROPIC_API_KEY
uvicorn app.main:app --reload     # http://localhost:8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev                        # http://localhost:5173  (proxies /api -> :8000)
```

Open http://localhost:5173, type an area, and read the 2×2.

## Design principles

- **Every gap traces to real evidence** with working source links — no fabricated demand.
- **Skeptical synthesis** — a short list of real gaps beats a long list of maybes; each
  gap names its *weakest link* and flags whether it's *empty-for-a-reason*.
- **Graceful degradation** — missing keys, rate limits, and empty results never fail the run.
- **Pluggable sources** — adding a fourth signal is one adapter + one registry line.
