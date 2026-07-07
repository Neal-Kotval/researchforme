#!/usr/bin/env bash
# Launch Market Gap Finder in a persistent tmux session so explorations keep
# running even after you close the terminal (in-process workers live as long as
# the backend does — and the backend runs WITHOUT --reload here, so editing a
# file never kills a running exploration mid-flight).
#
#   scripts/dev-tmux.sh            # start (or restart) the session
#   tmux attach -t gapfinder       # watch it
#   tmux kill-session -t gapfinder # stop everything
#
# Env: API_PORT (default 8030), AP_MAX_CONCURRENCY (parallel agent cap).
set -euo pipefail

SESSION="gapfinder"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${API_PORT:-8030}"
MAXC="${AP_MAX_CONCURRENCY:-12}"

# Fresh start: tear down any existing session so we don't stack servers.
tmux kill-session -t "$SESSION" 2>/dev/null || true

# Backend window: no --reload (so a file save can't kill a live run).
tmux new-session -d -s "$SESSION" -n backend -c "$ROOT/backend"
tmux send-keys -t "$SESSION:backend" \
  "AP_MAX_CONCURRENCY=$MAXC ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $API_PORT" C-m

# Frontend window: Vite dev server proxied at the backend.
tmux new-window -t "$SESSION" -n frontend -c "$ROOT/frontend"
tmux send-keys -t "$SESSION:frontend" \
  "VITE_API_TARGET=http://127.0.0.1:$API_PORT npm run dev" C-m

echo "▸ tmux session '$SESSION' started."
echo "  backend  : http://127.0.0.1:$API_PORT  (window: backend, no --reload)"
echo "  frontend : Vite dev server              (window: frontend)"
echo "  attach   : tmux attach -t $SESSION"
echo "  stop     : tmux kill-session -t $SESSION"
