# Market Gap Finder — task runner (https://github.com/casey/just)
# Run `just` or `just --list` to see all recipes.

set shell := ["bash", "-uc"]

# Directories
backend := "backend"
frontend := "frontend"

# Backend port. Override if :8000 is taken (e.g. Docker): `API_PORT=8010 just dev`.
# The frontend proxy follows this automatically.
api_port := env_var_or_default("API_PORT", "8000")

# --- default: show recipes --------------------------------------------------
default:
    @just --list

# --- setup ------------------------------------------------------------------

# Install everything (backend venv + deps, frontend node_modules).
setup: setup-backend setup-frontend
    @echo "✅ Setup complete. Run 'just dev' to start both servers."

# Create the backend venv and install the package (agent-sdk is a core dep).
setup-backend:
    cd {{backend}} && python3 -m venv .venv && \
      ./.venv/bin/pip install -U pip && \
      ./.venv/bin/pip install -e ".[dev]"

# Install frontend dependencies.
setup-frontend:
    cd {{frontend}} && npm install

# Copy .env.example -> .env if it doesn't exist (all keys optional).
env:
    @test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

# --- run --------------------------------------------------------------------

# Run backend + frontend together (Ctrl-C stops both).
dev:
    @echo "Backend :{{api_port}} · Frontend :5173 — Ctrl-C to stop"
    @trap 'kill 0' INT TERM; just backend & just frontend & wait

# Run the FastAPI backend with autoreload.
backend:
    cd {{backend}} && ./.venv/bin/uvicorn app.main:app --reload --port {{api_port}}

# Run the Vite dev server (proxies /api -> the backend on API_PORT).
frontend:
    cd {{frontend}} && VITE_API_TARGET=http://127.0.0.1:{{api_port}} npm run dev

# --- quality ----------------------------------------------------------------

# Run the backend test suite (uses the fixture LLM backend + mock sources).
test:
    cd {{backend}} && LLM_BACKEND=fixture ./.venv/bin/pytest -q

# Typecheck + production-build the frontend.
build:
    cd {{frontend}} && npm run build

# Lint the backend with ruff.
lint:
    cd {{backend}} && ./.venv/bin/ruff check app

# One-shot health check against a running backend.
health:
    ./{{backend}}/.venv/bin/python -c "import urllib.request,json; print(json.dumps(json.load(urllib.request.urlopen('http://localhost:{{api_port}}/api/health')), indent=2))"

# Smoke-test a full analysis against a running backend.
smoke:
    ./{{backend}}/.venv/bin/python -c "import urllib.request,json; req=urllib.request.Request('http://localhost:{{api_port}}/api/analyze', data=json.dumps({'area':'personal finance for freelancers'}).encode(), headers={'content-type':'application/json'}); print(json.dumps(json.load(urllib.request.urlopen(req, timeout=180)), indent=2)[:1200])"

# --- housekeeping -----------------------------------------------------------

# Remove caches, build artifacts, and the local SQLite cache.
clean:
    rm -rf {{backend}}/cache.db {{backend}}/cache.db-wal {{backend}}/cache.db-shm
    rm -rf {{frontend}}/dist {{frontend}}/.vite
    find {{backend}} -type d -name __pycache__ -prune -exec rm -rf {} +
    @echo "🧹 Cleaned."
