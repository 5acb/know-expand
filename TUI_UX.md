# doc-expand TUI — UX specification

## Goal

Replace the plain streaming log with a Textual TUI that:
- shows pipeline state at a glance without scrolling
- educates the user about what each stage is doing and why their choices matter
- makes interactive moments (taxonomy approval, profile assessment) feel like first-class UI rather than raw stdin prompts

---

## Screens

### 1. Main view (default, always running)

Two-column layout. Left = progress tree + scrolling log. Right = context panel + model stats strip.

```
┌─────────────────────────────────────┬──────────────────────────────┐
│  doc-expand  ·  input.md  ·  survey │                              │
├────────────────────┬────────────────┤                              │
│  stage tree        │  live log      │  context panel               │
│                    │                │  (updates per active stage)  │
│  ✓  0  ingest      │  21:19 ...     │                              │
│  ✓  1  extract     │  21:20 ...     │                              │
│  ●  2  graph       │  21:20 ...     │                              │
│  ○  3  audit       │                │  ─────────────────────────   │
│  ○  4  research    │                │  model stats strip           │
│  ○  5  synthesize  │                │                              │
│  ○  6  verify      │                │                              │
│  ○  7  assemble    │                │                              │
└────────────────────┴────────────────┴──────────────────────────────┘
```

**Left column (60%)**

Split vertically: stage tree on top (~55%), live log on bottom (~45%).

Stage tree rows:
- icon: `✓` (done, teal), `●` animated (running, blue), `○` (pending, muted)
- stage number + name + elapsed time (when done) or "Xs elapsed" (when running)
- expandable sub-rows for per-domain work (stage 4 / 5): same icon + domain id + status

Live log:
- scrollable, newest at bottom
- shows `llm_call_done`, `stage_complete`, `domain_complete` events
- format: `HH:MM  <event summary>  <tok info if llm>`
- older entries fade (lower opacity) to visually emphasize recency

**Right column (40%)**

Context panel on top (~70%): shows the active stage's name, a 2–3 sentence plain-English explanation of what the stage is doing, and what the user should expect next. Content is static per stage (see §Educational content).

Model stats strip on bottom (~30%): three metric cards.
- `llm calls` — total calls so far this run
- `avg tok/s` — rolling average throughput
- `active model` — name of the model currently being used

---

### 2. Taxonomy approval (stage 2 interactive)

Triggered when stage 2 finishes the ontology lock and needs user approval. Overlays the main view as a full-width panel at the bottom (pushes log up, doesn't replace the stage tree).

```
┌──────────────────────────────────────────────────────────────────────┐
│  choose a domain taxonomy                                            │
│  how the pipeline partitions your document's concepts                │
├───────────────────────────────┬──────────────────────────────────────┤
│  lumper (4 domains)           │  splitter (6 domains)                │
│  ─────────────────────        │  ──────────────────────────────      │
│  [AI systems]                 │  [multi-agent systems]               │
│  [document processing]        │  [document layout analysis]          │
│  [knowledge engineering]      │  [ontology learning]                 │
│  [software engineering]       │  [concurrent systems]                │
│                               │  [scientometrics]                    │
│                               │  [AI observability]                  │
│  Broader → wider bib,         │  Finer → targeted research,          │
│  more synthesis, less depth   │  precise citations, less overlap     │
├───────────────────────────────┴──────────────────────────────────────┤
│  [l] lumper   [s] splitter   [m] merge   [e] edit   [x] explain ↗   │
└──────────────────────────────────────────────────────────────────────┘
```

`x` / explain: calls the classifier LLM to produce a plain-English advisory specific to *this document's* terms and the two proposals. Output replaces the description rows inside the panel (no full-screen takeover). The action bar remains visible so the user can still choose without re-reading.

`e` / edit: opens `$EDITOR` with the lumper taxonomy as JSON pre-filled. On close, the panel reloads with the edited domains.

After a choice is made, the panel collapses and the stage tree updates the stage 2 row with `mode=<choice>`.

---

### 3. Profile assessment (stage 0.5 interactive)

Same pattern as taxonomy: overlay at the bottom, question + answer options shown as a numbered list or clickable buttons (if Textual), keyboard shortcuts for fast navigation. Each question shows a one-line explanation of why it's being asked.

---

### 4. Error / quota exhausted

If `quota_exhausted` or an unhandled exception fires, the main view dims and an alert banner appears at the top:

```
┌──────────────────────────────────────────────────────────────────────┐
│  ✗  all models exhausted for role: researcher                       │
│     add a key to .env or check your quota, then run with --resume   │
└──────────────────────────────────────────────────────────────────────┘
```

Stage tree row turns red. The log continues to show the last events. Process exits cleanly after a 3-second pause so the user can read the banner.

---

## Educational content (per stage)

Static text, shown in the context panel while the stage is active.

| Stage | Title | Body |
|-------|-------|------|
| 0 ingest | loading your document | Splits the file into overlapping chunks sized for parallel LLM processing. No content is lost — chunks overlap so terms that span a boundary are captured by both. |
| 0.5 assess | calibrating to you | A short quiz establishes your background and learning goal. Your answers set `depth` (survey / standard / deep) and `math_mode`, which control how technical the final document gets. |
| 1 extract | mapping the concepts | Runs all chunks in parallel through an extractor LLM. Each chunk produces a term inventory: concept name, aliases, co-occurring terms, and how central each is to the document. A reduce pass merges duplicates and scores centrality. |
| 2 graph | locking the knowledge structure | Two domain proposals (lumper + splitter) are generated simultaneously, then validated against OpenAlex to confirm they map to real academic fields. Your choice here determines how the rest of the pipeline is organised — it cannot be changed after this stage. |
| 3 audit | finding what's missing | For each domain, three LLM roles debate: a finder identifies gaps in the document relative to anchor papers; a defender argues the gaps aren't real; a finder rebuts. Only gaps that survive all three rounds are flagged for the research stage. |
| 4 research | deep per-domain synthesis | Two parallel strategies per domain: top-down (anchor papers → your terms) and bottom-up (your terms → literature). A critic reconciles both summaries, keeps cited claims, and ensures gap findings from stage 3 are addressed. |
| 5 synthesize | connecting the domains | Finds cross-domain insights that no single domain section captures. Produces the connective narrative and a suggested reading order. A critic culls trivial connections (things that are just prerequisite chains) and flags unsupported ones. |
| 6 verify | citation audit | Scans every section file for `[NEEDS_CITATION]` markers and unknown citation keys. Outputs a repair list. Any unresolved markers are visible in the final document as a quality signal rather than silently dropped. |
| 7 assemble | writing the document | Orders sections, weaves the synthesis narrative, formats the bibliography (CSL-JSON → Markdown). Produces `output/knowledge_expansion.md` and optionally a PDF via pandoc. |

---

## Interaction model

**Keyboard everywhere.** No mouse required. All interactive choices are single-key. Escape dismisses overlays without making a choice (re-shows them on the next tick).

**Non-blocking.** Pipeline stages that don't require user input never pause. Interactive stages (0.5 assess, 2 graph) show the prompt immediately and block *only that stage* — other stages that could run concurrently are not held.

**Resume-aware.** If `--resume` is passed, already-complete stages are shown as `✓ (skipped)` with their original timing from state. The context panel shows "this stage was skipped — results loaded from state/".

**No-TTY fallback.** If stdin is not a TTY (CI, piped, `--yes` flag), all interactive prompts auto-resolve: taxonomy defaults to `auto-merge`, profile defaults to `practitioner` + `standard` depth. The context panel is not rendered; structured JSON events go to stdout as before.

---

## Component map (Textual)

| Component | Textual class | Notes |
|-----------|--------------|-------|
| App shell | `App` | Sets `CSS_PATH`, binds `on_pipeline_event` message |
| Stage tree | `Tree` or custom `Static` | Updated via `post_message` on each `stage_*` event |
| Live log | `RichLog` | `highlight=True`, max 200 lines, auto-scroll |
| Context panel | `Markdown` | Content swapped per active stage |
| Model stats | `Grid` of `Label` pairs | Updated on every `llm_call_done` |
| Taxonomy overlay | `Widget` mounted to `Screen` | Contains two `Static` domain cards + `Button` row |
| Profile overlay | `Widget` mounted to `Screen` | Question + answer buttons |
| Alert banner | `Notification` or inline `Static` | Shown on `quota_exhausted` / exception |

Pipeline runs as a Textual `Worker` (`self.run_worker(pipeline_main(), ...)`). Pipeline emits events via a `Queue` that the App drains in `on_mount` / a background task, converting each to a custom `Message` subclass.

---

## Open questions

1. **Context panel: static vs. dynamic.** Static text (above) is fast and deterministic. Dynamic (LLM-generated per-document) is richer but adds latency and a model call before the stage starts. Proposed: static by default, `--rich-context` flag enables a one-shot LLM call at stage start that personalises the explanation using actual term counts and domain names.

2. **Domain cards in taxonomy overlay: show example terms?** `DomainProposal.example_terms` is already populated. Showing the top 5 terms per domain gives the user concrete grounding without explaining the whole taxonomy.

3. **Textual vs. Rich Live.** Rich `Live` with a `Layout` is simpler to integrate with an async pipeline but has no interactivity. Textual is the right choice if the taxonomy and profile prompts should feel native — the complexity cost is justified by those two screens.

4. **PDF output.** Stage 7 optionally calls pandoc. If pandoc is absent, show a one-line hint in the assembly context panel rather than failing silently.
