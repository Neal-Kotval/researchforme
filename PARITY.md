# PARITY.md — feature-parity contract for the v3 redesign

**Rule zero.** The redesign may re-layout, collapse, clamp, or hide-behind-disclosure any capability
below, but may **never delete or permanently hide** one. Migrate a page → tick every box here for it.
Progressive disclosure (popover / tooltip / expand / clamp) counts as "kept". Deletion does not.

Legend: `[ ]` not yet migrated · `[x]` migrated **and** re-verified present.

---

## Global chrome

### Sidebar nav (`PlatformShell.tsx`) — keep all 9 items + 2 groups + logo
Group **Flow**: `- [ ]` Home (`#/`) · `- [ ]` Explore (`#/explore`, shows **live** badge when any run running) ·
`- [ ]` Autonomous research (`#/autonomous`) · `- [ ]` Pressure-test (`#/pressure-test`) · `- [ ]` Compare (`#/compare`).
Group **Workspace**: `- [ ]` Library (`#/library`) · `- [ ]` Starred (`#/starred`) · `- [ ]` Graveyard (`#/graveyard`) ·
`- [ ]` Assistant (`#/assistant`, shows `/` kbd chip).
- [ ] Logo block: rising-signal mark + "Gap Finder" + "v3 · light" sub.
- [ ] Active state (tint pill + `aria-current="page"`), inactive (slate), hover.
- [ ] Exploration route (`#/e/...`) lights up **Explore** item (no own nav button).

### Top bar (`PlatformShell.tsx`) — per-view title + subtitle + 2 actions
- [ ] `<h1>` per-view title + `pf-view-sub` subtitle (VIEW_META copy — move long copy to "How it works" popover, keep one-line sub).
- [ ] "agent live" pulsing pill when any run running.
- [ ] **Ask** button (secondary) + `/` kbd chip → `navView("assistant")`.
- [ ] **New exploration** button (primary) + `N` kbd chip → opens NewExplorationDialog.
- [ ] Fullscreen mode (library project open) suppresses the top bar.

### Routes (`useHashRoute.ts`) — every hash must still resolve
- [ ] `#/`→home · `#/explore` · `#/autonomous` · `#/pressure-test`→pressure · `#/compare` · `#/assistant` · `#/graveyard` · `#/starred`.
- [ ] `#/library` (list) · `#/library/{slug}` (fullscreen project).
- [ ] `#/e/{pid}` (canvas) · `#/e/{pid}/evolution` · `#/e/{pid}/list` · `#/e/{pid}/n/{nodeId}`. (canvas default, omitted from URL.)
- [ ] Nav helpers: navHome, navProject, navNode, navMode, navView.

### Global keyboard shortcuts (`App.tsx`) — render as subtle `kbd` chips
- [ ] **⌘K / Ctrl+K** toggles Command Palette (works even while typing/dialog open).
- [ ] **N** → New exploration (suppressed while typing / palette / dialog open).
- [ ] **/** → Assistant view (same suppression).
- [ ] `typingInField()` guard for INPUT/TEXTAREA/SELECT/contentEditable.

### Command Palette (`CommandPalette.tsx`)
- [ ] Opens via ⌘K; input "Jump to an exploration or run a command…"; `esc` chip.
- [ ] "New exploration" command + one row per project (domain, status word, gaps, ★).
- [ ] Fuzzy filter on label/hint; ↑/↓ select, Enter run, Esc close; "No matches." empty.

### Token-pace footer (sidebar `pf-usage`, polls `getUsage()` 6s) — compress to 1 row + popover, **keep every number**
- [ ] Label "Token pace" + mode chip (`sprinting`/`curbing`/`backoff {n}s`/raw mode).
- [ ] tok/min now (level-colored) · ~projected/day.
- [ ] usage track/fill (hot when high/heavy) at `pct`.
- [ ] Cap line: capped → "{spent} / {cap} today · {pct}%"; else "{spent} today · no cap set"; no snapshot → "usage unavailable".

### Concurrency + dynamic limit (`GlobalUsageBar.tsx` popover, polls 2s) — **must stay reachable** (fold into footer popover)
- [ ] Level word (Low/Medium/High/Heavy usage) + mode sub (Sprinting/Curbing usage/Idle) + status dot.
- [ ] Gauge (pctFill) + metrics: {spent} today · {rate}/min · ~{proj}/day proj. · {running} running · {active}/{max} agents · resumes ~{n}s (backoff).
- [ ] ⚙ button "{conc} agents · {pct}%".
- [ ] Concurrency **slider 1–100 step 1** + quick-set **8/16/32/64/100**; commits via setConcurrency.
- [ ] Daily token cap number field + percent slider **5–100 step 5**; Cancel / Apply → setUsagePolicy.

### Run controls (`RunControls.tsx`) — surface on the shared RunCard (compact) + full elsewhere
- [ ] Transport (contextual): "Keep going" (milestone_paused) / "Pause" (running) / "Resume" (else).
- [ ] Stop-reason chip (full, when stopped).
- [ ] Pace dial: **Eco** / **Balanced** / **Sprint** (set_pace). ("Curb spend" = set_pace eco — Explore's label.)
- [ ] Star-threshold slider 0–100 (full).
- [ ] Budget & caps (full): Max tokens · Daily cap · Max nodes · Time limit (min) · Milestone tokens + "Apply budget"/"Saved".
- [ ] Model policy read-only (full): Decompose / Synthesize / Pressure-test models.

---

## Home (`HomeView.tsx`, `#/`)

**Order (redesign):** launch field → Worth your attention → Active exploration (RunCard) → Suggested spaces → Recent signals.

### Controls
- [ ] Quick-explore input: placeholder "Explore a market… (e.g. domestic semiconductor packaging) — Enter to launch"; Enter/`Explore ▸` launches (createProject autostart, max_nodes 70, balanced); "Launching…"; disabled <2 chars.
- [ ] `steer…` → opens full NewExplorationDialog (tooltip "Open the full drawer to add steering, intake, and budget"). → becomes sliders icon per brief.
- [ ] Idea card (whole button) → onOpenNode.
- [ ] Run card (whole button) → onOpenProject.
- [ ] "＋ New exploration" (active-empty state).
- [ ] Scout brief input: placeholder "Optional: who you are / what you're good at — sharpens the suggestions"; Enter → scout.
- [ ] "Scout for spaces" → runScout ("Scouting…"); "Retry" on error.
- [ ] "Explore this space" per scout candidate → onExploreCandidate.
- [ ] Scout candidate signal links (≤2, target=_blank).

### Stats / status
- [ ] Idea meta: {domain} · {confidence} confidence · red team {survived}/{lenses}; `unverified` chip on untrusted; `why_now` line.
- [ ] Run card stats: nodes · gaps · starred · elapsed (running/ran); spend bar + "{spent} of {cap} tok cap" / "{spent} tok · no cap".
- [ ] Run mode pill: sprinting(live,pulse)/curbing/paused/done; "now" line variants (curbing/hunting/paused/usage_paused/milestone_paused/stopped).

### Empty states
- [ ] Worth-attention loading "Checking the latest runs…" / empty "No idea has survived a red team yet…".
- [ ] Active empty "Nothing is running." + New exploration.
- [ ] Scout idle / ready-zero copy (see brief: idle copy → scout input helper text).

### Sub-components (self-gating — keep intact)
- [ ] `PreferenceDistillCard`: Distill CTA ("Distill what your passes say" + "Distill preferences") ↔ proposed (editable textarea + "Confirm & apply" / "Dismiss" + "pending" badge) ↔ applied/dismissed closes ↔ error. Threshold 8.
- [ ] `RecentSignals`: "Recent signals"; error+Retry / loading / empty / rows (name·domain, moved/quiet badge, summary + new-items + regulatory + when) → onOpenNode.

---

## Explore (`ExploreView.tsx`, `#/explore`)

### Controls
- [ ] Active-run tabs (when >1 active) → setSelectedPid.
- [ ] "Pause run" (running) / "Resume run" (paused) — act(pause/resume).
- [ ] "Curb spend" (running||paused) → set_pace eco; disabled when busy or already eco; tooltip.
- [ ] "Full digest ▸" (done) → onOpenProject.
- [ ] "Open full tree ▸" (candidate header) → onOpenProject. → secondary button per brief.
- [ ] Gap card (whole button) → onOpenNode.
- [ ] "＋ New exploration" (no-pid empty).

### Stats / status
- [ ] Summary tiles: nodes mapped · candidate gaps · starred · {spent} "of {cap} tok cap"/"tok · no cap".
- [ ] Gap card: ViabChip(value,trust,star) · FitChip(value) · status word · title. (list cap 6.)
- [ ] Run pill sprinting/curbing/paused/done; streaming indicator (pulse "streaming") when running.
- [ ] gapStatus words: "in red team" / "red team {s}/{n}" / "queued for red team" / "synthesizing" / "mapping".
- [ ] Feed rows: source chip (reddit/github/arxiv/hn/newsletter/engine) + ago(at); 8 rows shown.
- [ ] Digest strip (done): "Top: …" / "Kill pattern: …" + "· deterministic fallback" degraded badge.

### Empty states
- [ ] No pid "No active run to watch…" + New exploration.
- [ ] No gaps "No candidate gaps yet…".
- [ ] No logs: running "Waiting for the next event…" / paused / finished variants.
- [ ] Live activity idle → EmptyState (per brief).

---

## Autonomous research (`AutonomousView.tsx`, `#/autonomous`)

**Redesign:** hero launch input (embedded Launch + sliders-icon steering popover); "How it works" → 4-step strip behind toggle once ≥1 run; recent runs = RunCards.

### Controls
- [ ] Quick-launch input "Point the engine at a market… (e.g. onboarding tooling for solo law firms) — Enter to launch"; Enter/`Launch ▸` → createProject autostart (max_nodes 70, balanced); "Launching…"; disabled <2 chars. → **hero field w/ embedded button**.
- [ ] `steer…` → NewExplorationDialog. → **sliders icon inside field**.
- [ ] "＋ Launch a run" (live-runs empty state).
- [ ] RunCard (whole button) → open project.

### Stats / status (RunCard — unify with shared RunCard)
- [ ] name · mode pill (sprinting/curbing/paused/done) · now-line variants · nodes/gaps/starred/elapsed · Spend bar + caption.

### Sections / empty
- [ ] "Live autonomous runs" (+ sub) → RunCard stack or empty "No autonomous run is going right now." + "＋ Launch a run".
- [ ] "How autonomous research works" 4 steps: Map the space / Synthesize gaps / Red-team / Rank & pause. → **collapsible strip once ≥1 run; inline first-run**.
- [ ] "Recent runs" (only when history) — max 4 RunCards.
- [ ] Launch error "Could not launch that run.".

## Pressure-test (`PressureTestView.tsx`, `#/pressure-test`)

**Redesign:** candidate chips → horizontal segmented row; lens list w/ verdict clamp + "Not evaluated" chip; survival summary card + score badge + "Open in explorer" primary.

### Controls
- [ ] Candidate picker tabs (≤6 tested ideas; only when >1) → select. → **segmented scroll row (selected = ink on tint)**.
- [ ] "Open in explorer ▸" (primary) → onOpenNode.
- [ ] "＋ New exploration" (empty).

### Stats / status
- [ ] Eyebrow "Red team · {domain}" + triage badge (interested/passed).
- [ ] Score block: viability number or "—" + "viability · {rigor} rigor". → **ScoreBadge**.
- [ ] Six lenses: verdict pill (survives/weakens/kills/**not evaluated**) + dot · lens name · finding (l.argument, clamp 2) · meta ("no rigor · runs next window" / "{rigor} rigor · {n} signal(s) cited").
- [ ] Foot summary: "Survived {s} of {n}" (+ "· {k} kill(s)" + summary); ViabChip + FitChip(labeled).

### Empty states
- [ ] loading "Loading pressure-test results…" / error / none "No candidate has reached the red team yet…" + New exploration.

## Compare (`CompareView.tsx`, `#/compare`)

**Redesign:** empty 2×2 → EmptyState CTA "Re-run with steering"; unscored list → Explore row style; shortlist Table 11px uppercase headers, 44px rows, tabular, **all six columns**, "Choose" small primary.

### Controls
- [ ] "Choose" per row (lead = primary) → onOpenNode.
- [ ] "Hide occupied (N)" checkbox filter (only when occupiedCount>0; removes novelty=occupied).
- [ ] "＋ New exploration" (no-survivors empty).
- [ ] PortfolioScatter dots/strip actions (see shared).

### Stats / columns (keep all 6 + rank)
- [ ] Portfolio section (eyebrow/title/sub) + scatter.
- [ ] Shortlist intro "{N} survivor(s)" + copy.
- [ ] Table cols in order: **Viab**(ViabChip) · **Fit**(FitChip) · **Novelty**(NoveltyChip) · **Idea**(#rank + title + domain) · **Worth**(ROI chip/—, full tooltip) · **Provenance**(signals + rt s/n) · Choose. Lead row tint. Top 8.
- [ ] Bottom note copy.

### Empty states (shortlist)
- [ ] loading "Ranking the survivors…" / error "Couldn't load the shortlist" / none "No survivors yet" + New exploration. → EmptyState pattern; empty 2×2 CTA "Re-run with steering".

## New-exploration dialog (`NewExplorationDialog.tsx`) — launched from Home/Autonomous/top-bar/`N`. **Every field must survive.**
- [ ] Close ×, scrim click, Escape.
- [ ] Depth segmented: Quick scan / Standard / Deep (re-seeds budget presets, fields stay editable) + depth hint.
- [ ] Domain input (required) "e.g. embedded AI"; Enter submits.
- [ ] "⤵ Paste my research" toggle → research textarea; "↯ Sort into a job" (Sorting…) → sortResearch prefill.
- [ ] "✎ Add context & steering" toggle → Founder brief / Unfair advantages / Hard constraints (+hint) / Avoid / Time horizon.
- [ ] "✦ Refine with questions" / "↻ Regenerate" (Thinking…) → intake questions + suggestion chips + free input.
- [ ] Seed sub-segments chips (Enter/comma add, Backspace remove).
- [ ] Pace segmented eco/balanced/sprint.
- [ ] Star threshold slider 0–100 "≥ N" (default 75).
- [ ] Model policy: Decompose/Synthesize/Pressure-test ModelPickers (Opus 4.8 / Sonnet 5 / Haiku 4.5).
- [ ] Budget & caps: Max tokens / Daily cap / Max nodes / Time limit (min) / Milestone every (blank=null).
- [ ] "Start exploring immediately" checkbox (default true) → "Explore ▸"/"Create" ("Starting…"); "Cancel".
- [ ] Error line (validation / API).

---

## Graveyard (`GraveyardView.tsx`, `#/graveyard`)

### Controls
- [ ] Search box "Search titles, domains, kill reasons…" (debounced 250ms; AND-match; race-guarded getGraveyard).
- [ ] "Open" (btn-sm) → onOpenNode — only when project_id present.
- [ ] "Watch for expiry" → control(watch_node); states Watch/Watching…/Watching/Retry watch; tooltip. **Keep watch/expiry fully intact.**

### Stats / status
- [ ] Title "Rejected spaces" + subtitle; count line "{n} rejected space(s) matching…/across your runs…".
- [ ] Row: title · thesis_first_line · "viab {n}" chip · kill-lens chips (per lens) · "you: {reason}" chip · meta domain/external + when.
- [ ] "external post-mortem" badge (external corpus).

### Empty states
- [ ] error "⚠︎ …" / loading "Digging through the rejects…" / no-match-with-query "Nothing here matches “{q}”…" / empty "Nothing has been rejected yet…".

---

## Library (`LibraryView.tsx`, `#/library` + `#/library/{slug}`)

### Controls
- [ ] "+ New project" → inline creator; input "Project name…" (Enter create / Esc cancel); "Create" (disabled empty) / "Cancel".
- [ ] Project card → onOpenProject(slug); title tooltip = verdict.
- [ ] "×" delete → confirm popover "Delete this project?" → "Delete"/"Deleting…" + "Cancel".
- [ ] Library root path display (read-only, tooltip). One-row card per brief.

### Stats / status
- [ ] Card: score chip (viability, band hi/mid/lo/crit, "·stale" when validation_stale) · title · verdict · status (st-{status}) · "{n} idea(s)" · when.
- [ ] Ordering: validated-by-viability, then unvalidated-by-recency.

### Empty states
- [ ] error / loading "Reading the library…" / empty "The library is empty." + note.
- [ ] `#/library/{slug}` renders `ProjectWorkspace` (hosts ProjectChat) — out of the 9-page scope but must keep routing.

---

## Starred (`StarredView.tsx`, `#/starred`)

### Controls
- [ ] Row checkbox toggle (select Set); row body → onOpenNode; unstar "★" → control(unstar_node).
- [ ] "Clear selection" (when >0); "Export {n} to project →" → export panel (disabled empty).
- [ ] Export panel: destination `<select>` ("+ New project…" or existing slug); name input "e.g. Kernel CI" (when new); "Develop each idea first" checkbox (default true) + explainer; "Export {n} idea(s)"/"Exporting…" + "Cancel".

### Stats / status
- [ ] Count bar "{n} of {m} selected" / "{m} starred idea(s)".
- [ ] Grouped by exploration (domain header); row meta: v{viab} · fit {fit} · "engine pick" (it.star) · when.
- [ ] Export progress line; degraded-development warning.

### Empty states
- [ ] error / loading "Loading your starred ideas…" / empty "No starred ideas yet." + note. Populated = idea cards + checkboxes + bottom export bar (per brief).

---

## Assistant (`AssistantView.tsx`, `#/assistant`)

### Controls
- [ ] Composer input "Ask the assistant, or tell it what to hunt…" (Enter sends, disabled while pending, auto-refocus).
- [ ] "Send" + `↵` chip (disabled empty/pending).
- [ ] Intro inline "Explore"/"Compare" links → onNav.

### Chat structure / status
- [ ] Roles: You (tint bubble) / Assistant (white card) — per brief.
- [ ] Standing intro (assistant) with 2 example prompts → **become tappable suggestion chips** above input.
- [ ] Command blocks "✓ ran" dotted-CLI per tool call (tool + --arg "val").
- [ ] Assistant prose via `<Markdown>`; mutating tools (explore.start/run.pause/run.resume) → onActed().
- [ ] pending thinking row "Working — running the tool layer…" (pulse); error bubble "⚠︎ …".

> Note: `ProjectChat.tsx` (inside ProjectWorkspace) is a distinct project-scoped chat — separate surface, keep as-is.

---

## Shared sub-components (reused across pages — keep every encoding)

### PortfolioScatter (`PortfolioScatter.tsx`) — Compare's 2×2
- [ ] SVG scatter, X "FOUNDER FIT →", Y "VIABILITY →", midlines at 50.
- [ ] Quadrant labels: INVESTIGATE NOW / STRONG MARKET, WEAK FIT / FITS YOU, WEAK MARKET / SKIP.
- [ ] Trust-encoded dots (earned=solid ramp, provisional/unverified=outline), star ★, passed dimming; keyboard-activatable → open dossier.
- [ ] Hover tooltip (title · domain · viab · fit · confidence · workflow line).
- [ ] No-fit strip "No steering — fit unscored" + sub; rows ViabChip+title+domain+"Open"; "Show all/fewer" >6.
- [ ] Empty/loading/error/not-plottable copy variants.

### ViabChip / FitChip / NoveltyChip
- [ ] ViabChip: viability numeral or "—"; trust encoding (earned solid ramp / provisional-unverified outlined via `trust-{level}`); optional ★.
- [ ] FitChip: renders **only when fit non-null** (never a fake 0); `labeled` adds "fit"; tooltip FIT_HELP.
- [ ] NoveltyChip: "—" (no scan) or "{verdict} · {n}" with `nv-{verdict}`; "occupied" special-cased for filter; rationale/risk tooltip.

### RunCard (NEW shared primitive — unify Home/Autonomous/Explore variants)
- [ ] name + status chip · 4 stats (nodes · gaps · starred · ran) tabular row · spend meter + "{tok} · {cap}" caption · inline "Resume run" (contextual transport) + "Curb spend" (set_pace eco) on **every** surface.
