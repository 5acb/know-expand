# Agent: agent__unknown_04
**ID:** `agent-aa65d68c6807e7102`  **Turns:** 71

## Prompt
```
You are auditing the codebase at /Users/cyan/ccr/know-expand (a Python multi-agent CLI called know-expand that turns a technical document into a long "knowledge expansion" PDF via a LangGraph pipeline of 11 stages, with a QuotaAwareRouter over many LLM providers and a single-file web dashboard). Read the actual files with Read/Grep/Bash. Be a rigorous senior staff engineer: every issue must cite specific files/lines and be classified by layer — "objective" (is the goal itself right / coherent), "architecture" (structural design decisions), "planning" (roadmap, sequencing, what's prioritized), "execution" (bugs, code quality, correctness). Judge against the goal of making this a production-grade product that many users run concurrently (SaaS or self-hosted), not a personal tool. Do NOT pad with trivia; do report real critical/high problems. Also read /Users/cyan/ccr/know-expand/CLAUDE.md and /Users/cyan/ccr/know-expand/README.md for stated intent so you can flag intent-vs-code mismatches.

Your subsystem: core orchestration — /Users/cyan/ccr/know-expand/know_expand/pipeline.py, cli.py, state.py, config.py, union_find.py, centrality.py. Assess: how LangGraph is actually used (is it a real graph or a linear chain wrapper?), state management & resume mechanism (pipeline.json stage markers — is it robust? partial-stage resume? idempotency? crash mid-stage?), error handling, the emit() event system, signal handling, config handling (dict-based timeouts footgun), and whether this orchestration layer can support concurrent multi-user runs, queueing, retries, or horizontal scale. Identify what a production orchestrator (Temporal/LangGraph Platform-style durable execution) would require that this lacks.
```

**[4] tool→Bash:** `wc -l /Users/cyan/ccr/know-expand/know_expand/pipeline.py /Users/cyan/ccr/know-expand/know_expand/cli.py /Users/cyan/ccr` — Count lines in orchestration files

<details><summary>[5] tool result (421 chars)</summary>

```
     155 /Users/cyan/ccr/know-expand/know_expand/pipeline.py
     179 /Users/cyan/ccr/know-expand/know_expand/cli.py
     380 /Users/cyan/ccr/know-expand/know_expand/state.py
     146 /Users/cyan/ccr/know-expand/know_expand/config.py
      28 /Users/cyan/ccr/know-expand/know_expand/union_find.py
      33 /Users/cyan/ccr/know-expand/know_expand/centrality.py
    1022 /Users/cyan/ccr/know-expand/README.md
    1943 total
```
</details>

**[6] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/pipeline.py` 

<details><summary>[7] tool result (5220 chars)</summary>

```
1	from pathlib import Path
2	
3	from langgraph.graph import StateGraph, END
4	
5	from know_expand.config import Config
6	from know_expand.state import PipelineState, emit, load_pipeline_json
7	from know_expand.stages import (
8	    s0_ingest,
9	    s1_assess,
10	    s2_extract,
11	    s3_graph,
12	    s4_audit,
13	    s5_research,
14	    s6_align,
15	    s7_synthesize,
16	    s8_verify,
17	    s9_prereq,
18	    s10_assemble,
19	)
20	
21	
22	def build_graph(
23	    cfg: Config,
24	    auto_taxonomy: bool,
25	    no_pdf: bool = False,
26	) -> StateGraph:
27	    graph = StateGraph(PipelineState)
28	
29	    async def node_ingest(state: PipelineState) -> PipelineState:
30	        await s0_ingest.run(state, cfg)
31	        return state
32	
33	    async def node_assess(state: PipelineState) -> PipelineState:
34	        await s1_assess.run(state, cfg)
35	        return state
36	
37	    async def node_extract(state: PipelineState) -> PipelineState:
38	        await s2_extract.run(state, cfg)
39	        return state
40	
41	    async def node_graph(state: PipelineState) -> PipelineState:
42	        await s3_graph.run(state, cfg, auto_taxonomy=auto_taxonomy)
43	        return state
44	
45	    async def node_audit(state: PipelineState) -> PipelineState:
46	        await s4_audit.run(state, cfg)
47	        return state
48	
49	    async def node_research(state: PipelineState) -> PipelineState:
50	        await s5_research.run(state, cfg)
51	        return state
52	
53	    async def node_align(state: PipelineState) -> PipelineState:
54	        await s6_align.run(state, cfg)
55	        return state
56	
57	    async def node_synthesize(state: PipelineState) -> PipelineState:
58	        await s7_synthesize.run(state, cfg)
59	        return state
60	
61	    async def node_verify(state: PipelineState) -> PipelineState:
62	        await s8_verify.run(state, cfg)
63	        return state
64	
65	    async def node_prereq(state: PipelineState) -> PipelineState:
66	        await s9_prereq.run...[truncated]
```
</details>

**[9] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/cli.py` 

<details><summary>[10] tool result (6752 chars)</summary>

```
1	import argparse
2	import asyncio
3	import signal
4	import sys
5	from pathlib import Path
6	
7	from dotenv import load_dotenv
8	load_dotenv()
9	
10	from know_expand.config import load_config
11	from know_expand.state import emit, load_pipeline_json, new_run_id, setup_logging
12	
13	
14	def _install_sigterm_handler() -> None:
15	    """Emit run_stopped and exit cleanly when SIGTERM arrives (e.g. from web UI stop)."""
16	    def _handler(signum, frame):
17	        emit({"event": "run_stopped", "reason": "sigterm"})
18	        sys.exit(0)
19	    signal.signal(signal.SIGTERM, _handler)
20	
21	
22	def _status(state_dir: Path) -> None:
23	    data = load_pipeline_json(state_dir)
24	    emit({"event": "pipeline_status", **data})
25	
26	
27	def _find_latest_state_dir(runs_dir: Path) -> Path | None:
28	    """Scan runs_dir for the most recently modified subdir that has state/pipeline.json."""
29	    if not runs_dir.exists():
30	        return None
31	    candidates = sorted(
32	        (d for d in runs_dir.iterdir() if d.is_dir()),
33	        key=lambda d: d.stat().st_mtime,
34	        reverse=True,
35	    )
36	    for run_dir in candidates:
37	        sd = run_dir / "state"
38	        if (sd / "pipeline.json").exists():
39	            return sd
40	    return None
41	
42	
43	_SUBCOMMANDS = {"tail", "serve"}
44	
45	
46	def main() -> None:
47	    # Pre-scan argv: if the first positional arg is not a known subcommand
48	    # (and not a flag), it's the pipeline input file. Extract it before
49	    # argparse consumes it as an invalid subcommand choice.
50	    raw_argv = sys.argv[1:]
51	    _input_file: str | None = None
52	    if raw_argv and not raw_argv[0].startswith("-") and raw_argv[0] not in _SUBCOMMANDS:
53	        _input_file = raw_argv[0]
54	        raw_argv = raw_argv[1:]
55	
56	    parser = argparse.ArgumentParser(
57	        prog="know-expand",
58	        description="Expand a technical document into a research-grade knowledge document.",
59	    )
60	    parser.add_...[truncated]
```
</details>

**[11] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/state.py` 

<details><summary>[12] tool result (16862 chars)</summary>

```
1	"""
2	Logging and state management for the know-expand pipeline.
3	
4	Log layout per run (runs/{run_id}/logs/):
5	  events.jsonl  — every emit() call as a timestamped JSON Line
6	  pipeline.log  — human-readable INFO+ with UTC timestamps
7	  debug.log     — everything including DEBUG
8	  errors.log    — WARNING+ only
9	
10	stdout — real-time progress for the terminal operator (key events only)
11	stderr — nothing; all output is routed to files or stdout
12	"""
13	
14	import json
15	import logging
16	import os
17	import uuid
18	from datetime import datetime, timezone
19	from pathlib import Path
20	from typing import TextIO, TypedDict
21	
22	# ---------------------------------------------------------------------------
23	# Run identity
24	# ---------------------------------------------------------------------------
25	
26	_run_id: str = ""
27	_log_dir: Path | None = None
28	_event_fh: TextIO | None = None
29	_logger = logging.getLogger("know_expand")
30	
31	
32	def new_run_id() -> str:
33	    return str(uuid.uuid4())
34	
35	
36	def _utc() -> str:
37	    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
38	
39	
40	# ---------------------------------------------------------------------------
41	# Logging setup
42	# ---------------------------------------------------------------------------
43	
44	class _UTCFormatter(logging.Formatter):
45	    def format(self, record: logging.LogRecord) -> str:
46	        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds")
47	        return f"{ts} {record.levelname[0]} {record.name}: {record.getMessage()}"
48	
49	
50	def setup_logging(run_id: str, log_dir: Path) -> Path:
51	    """
52	    Wire up file handlers writing into log_dir (the full path, already including run_id).
53	    Must be called once at pipeline startup before any emit().
54	    Returns the log directory path.
55	    """
56	    global _run_id, _log_dir, _event_fh
57	
58	    _run_id = run_id
59	    _log_dir =...[truncated]
```
</details>

**[14] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/config.py` 

<details><summary>[15] tool result (4880 chars)</summary>

```
1	from dataclasses import dataclass, field
2	from pathlib import Path
3	
4	import yaml
5	
6	
7	@dataclass
8	class BibliographyConfig:
9	    foundational_fraction: float = 0.65
10	    frontier_months: int = 24
11	    depth_totals: dict[str, int] = field(default_factory=lambda: {
12	        "survey": 10, "standard": 30, "deep": 50
13	    })
14	    ss_max_retries: int = 5
15	    ss_retry_initial_delay: float = 30.0
16	    ss_max_backoff: float = 120.0
17	    ss_anchors_n: int = 3
18	
19	
20	@dataclass
21	class CentralityConfig:
22	    structural_weight: float = 2.0
23	    core_effective_threshold: float = 5.0
24	    core_chunk_fraction: float = 0.3
25	    supporting_effective_threshold: float = 2.0
26	    alias_cosine_threshold: float = 0.92
27	
28	
29	@dataclass
30	class RateLimitConfig:
31	    max_rate: float
32	    time_period: float
33	
34	
35	@dataclass
36	class ConcurrencyConfig:
37	    default: int = 8
38	
39	
40	@dataclass
41	class NlpModelsConfig:
42	    spacy: str = "en_core_web_sm"
43	    embedding: str = "BAAI/bge-m3"
44	    tokenizer: str = "cl100k_base"
45	
46	
47	@dataclass
48	class ChunkingConfig:
49	    word_size: int = 800
50	    max_tokens: int = 4000
51	    spacy_char_limit: int = 50000
52	    llm_map_char_limit: int = 6000
53	
54	
55	@dataclass
56	class KeywordExtractionConfig:
57	    top_n: int = 20
58	    ngram_min: int = 1
59	    ngram_max: int = 3
60	
61	
62	@dataclass
63	class GraphDefaultsConfig:
64	    node_tier: str = "journeyman"
65	    node_xp: int = 100
66	
67	
68	@dataclass
69	class LlamaCppConfig:
70	    base_url: str = "http://192.168.1.117:8080"
71	
72	
73	@dataclass
74	class CriticThinkingConfig:
75	    budget_tokens: int = 0
76	
77	
78	@dataclass
79	class ResearchContextConfig:
80	    """Prompt-size controls for S5 persona prompts.
81	
82	    Depth-aware caps prevent the ~44k-char prompts that caused geminicli timeouts
83	    while still giving deep runs more context than shallow ones.
84	    """
85	    nodes_top_n: int = 30
86	   ...[truncated]
```
</details>

**[16] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/union_find.py` 

<details><summary>[17] tool result (957 chars)</summary>

```
1	class UnionFind:
2	    def __init__(self) -> None:
3	        self.parent: dict[str, str] = {}
4	        self.rank: dict[str, int] = {}
5	
6	    def find(self, x: str) -> str:
7	        if x not in self.parent:
8	            self.parent[x] = x
9	            self.rank[x] = 0
10	        if self.parent[x] != x:
11	            self.parent[x] = self.find(self.parent[x])
12	        return self.parent[x]
13	
14	    def union(self, x: str, y: str) -> None:
15	        rx, ry = self.find(x), self.find(y)
16	        if rx == ry:
17	            return
18	        if self.rank[rx] < self.rank[ry]:
19	            rx, ry = ry, rx
20	        self.parent[ry] = rx
21	        if self.rank[rx] == self.rank[ry]:
22	            self.rank[rx] += 1
23	
24	    def clusters(self) -> dict[str, list[str]]:
25	        groups: dict[str, list[str]] = {}
26	        for x in self.parent:
27	            groups.setdefault(self.find(x), []).append(x)
28	        return groups
29	
```
</details>

**[18] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/centrality.py` 

<details><summary>[19] tool result (1190 chars)</summary>

```
1	from know_expand.config import CentralityConfig
2	
3	_DEFAULT = CentralityConfig()
4	
5	BOILERPLATE: frozenset[str] = frozenset({
6	    "introduction", "methodology", "conclusion", "figure", "table",
7	    "theorem", "proof", "lemma", "et al", "appendix", "section",
8	    "related work", "abstract", "references", "background", "overview",
9	})
10	
11	
12	def compute_centrality(
13	    term: str,
14	    occurrences: int,
15	    chunk_count: int,
16	    structural_zones: set[str],
17	    cfg: CentralityConfig = _DEFAULT,
18	) -> str | None:
19	    """Return 'core', 'supporting', 'incidental', or None (boilerplate)."""
20	    name = term.lower()
21	    if any(bp in name for bp in BOILERPLATE):
22	        return None
23	
24	    in_structure = name in {z.lower() for z in structural_zones}
25	    effective = occurrences * (cfg.structural_weight if in_structure else 1.0)
26	
27	    if effective >= cfg.core_effective_threshold:
28	        return "core"
29	    if chunk_count > 0 and occurrences / chunk_count >= cfg.core_chunk_fraction:
30	        return "core"
31	    if effective >= cfg.supporting_effective_threshold:
32	        return "supporting"
33	    return "incidental"
34	
```
</details>

**[21] tool→Bash:** `grep -rn "resume_stage\|args.resume\|args.stage\b\|user_profile\|no_bibliography\|args.human\|args.no_pdf\|no_pdf" know_` — Find usage of CLI flags in codebase

<details><summary>[22] tool result (1971 chars)</summary>

```
know_expand/observe.py:1724:        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
know_expand/observe.py:2149:        profile_path = state_dir / "user_profile.json"
know_expand/observe.py:2509:    no_pdf = params.get("no_pdf", False)
know_expand/observe.py:2513:    if no_pdf:
know_expand/cli.py:174:        resume_stage=int(args.resume) if args.resume else None,
know_expand/pipeline.py:25:    no_pdf: bool = False,
know_expand/pipeline.py:70:        await s10_assemble.run(state, cfg, no_pdf=no_pdf)
know_expand/pipeline.py:109:    no_pdf: bool = False,
know_expand/pipeline.py:110:    resume_stage: int | None = None,
know_expand/pipeline.py:153:    graph = build_graph(cfg, auto_taxonomy=auto_taxonomy, no_pdf=no_pdf)
know_expand/stages/s1_assess.py:676:    profile_path = state_dir / "user_profile.json"
know_expand/stages/s9_prereq.py:47:def _load_user_profile(state_dir: Path) -> dict:
know_expand/stages/s9_prereq.py:48:    """Load user_profile.json; return empty dict on any error."""
know_expand/stages/s9_prereq.py:49:    path = state_dir / "user_profile.json"
know_expand/stages/s9_prereq.py:187:    profile = _load_user_profile(state_dir)
know_expand/stages/s10_assemble.py:395:async def run(state: PipelineState, cfg: Config, no_pdf: bool = False) -> None:
know_expand/stages/s10_assemble.py:555:    if no_pdf:
know_expand/stages/s10_assemble.py:556:        emit({"event": "s7_pdf_skipped", "reason": "no_pdf_flag"})
know_expand/stages/s5_research.py:657:    user_profile: dict | None = None,
know_expand/stages/s5_research.py:727:    reader_profile_str, reader_profile_note = _reader_profile_strings(user_profile or {})
know_expand/stages/s5_research.py:968:    profile_path = state_dir / "user_profile.json"
know_expand/stages/s5_research.py:969:    user_profile: dict = {}
know_expand/stages/s5_research.py:971:        user_profile = json.loads(profile_path.read_text())
know_expand/stages/s5_research.py:985:            user_profile=user_profile,
```
</details>

**[23] tool→Bash:** `grep -rn "stage_is_complete\|mark_stage_complete\|sentinel_exists\|write_sentinel" know_expand/stages/*.py | head -50` — Find stage completion marker usage in stages

<details><summary>[24] tool result (3415 chars)</summary>

```
know_expand/stages/s0_ingest.py:11:from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
know_expand/stages/s0_ingest.py:36:    if stage_is_complete(state_dir, 0):
know_expand/stages/s0_ingest.py:165:    mark_stage_complete(state_dir, 0)
know_expand/stages/s10_assemble.py:16:    mark_stage_complete,
know_expand/stages/s10_assemble.py:17:    stage_is_complete,
know_expand/stages/s10_assemble.py:403:    if stage_is_complete(state_dir, 10):
know_expand/stages/s10_assemble.py:629:    mark_stage_complete(state_dir, 10)
know_expand/stages/s1_assess.py:31:    mark_stage_complete,
know_expand/stages/s1_assess.py:32:    stage_is_complete,
know_expand/stages/s1_assess.py:678:    if stage_is_complete(state_dir, 1):
know_expand/stages/s1_assess.py:735:    mark_stage_complete(state_dir, 1)
know_expand/stages/s2_extract.py:20:    PipelineState, emit, mark_stage_complete, stage_is_complete,
know_expand/stages/s2_extract.py:367:    if stage_is_complete(state_dir, 2):
know_expand/stages/s2_extract.py:472:    mark_stage_complete(state_dir, 2)
know_expand/stages/s3_graph.py:17:from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
know_expand/stages/s3_graph.py:273:    if stage_is_complete(state_dir, 3):
know_expand/stages/s3_graph.py:411:    mark_stage_complete(state_dir, 3)
know_expand/stages/s4_audit.py:15:from know_expand.state import PipelineState, emit, mark_stage_complete, stage_is_complete
know_expand/stages/s4_audit.py:267:            "reason": "bibliography_sentinel_exists",
know_expand/stages/s4_audit.py:371:    if stage_is_complete(state_dir, 4):
know_expand/stages/s4_audit.py:455:    mark_stage_complete(state_dir, 4)
know_expand/stages/s5_research.py:25:    mark_stage_complete,
know_expand/stages/s5_research.py:26:    stage_is_complete,
know_expand/stages/s5_research.py:669:            "reason": "sentinel_exists",
know_expand/stages/s5_research.py:949:    if stage_is_complete(state_dir, 5):
know_expand...[truncated]
```
</details>

**[26] tool→Bash:** `grep -rn "run_failed\|pipeline_failed\|run_stopped\|pipeline_error\|except Exception" know_expand/pipeline.py know_expan` — Find failure-event handling across orchestration and server

<details><summary>[27] tool result (1892 chars)</summary>

```
know_expand/observe.py:1546:  lastEventsEtag = '';   // force events refresh so run_stopped appears immediately
know_expand/observe.py:1553:  // Switch to all-events so the run_stopped log line is visible
know_expand/observe.py:2055:        except Exception:
know_expand/observe.py:2064:    except Exception:
know_expand/observe.py:2075:        except Exception:
know_expand/observe.py:2086:                except Exception:
know_expand/observe.py:2098:        except Exception:
know_expand/observe.py:2142:            except Exception:
know_expand/observe.py:2153:            except Exception:
know_expand/observe.py:2164:            except Exception:
know_expand/observe.py:2172:            except Exception:
know_expand/observe.py:2180:            except Exception:
know_expand/observe.py:2190:                except Exception:
know_expand/observe.py:2206:                        except Exception:
know_expand/observe.py:2209:            except Exception:
know_expand/observe.py:2232:                            except Exception:
know_expand/observe.py:2246:                    except Exception:
know_expand/observe.py:2271:                            except Exception:
know_expand/observe.py:2274:            except Exception:
know_expand/observe.py:2292:                            except Exception:
know_expand/observe.py:2304:                    except Exception:
know_expand/observe.py:2315:            except Exception:
know_expand/observe.py:2329:            except Exception:
know_expand/observe.py:2343:                except Exception:
know_expand/observe.py:2358:                except Exception:
know_expand/observe.py:2380:                except Exception:
know_expand/observe.py:2395:        except Exception:
know_expand/observe.py:2418:    except Exception:
know_expand/observe.py:2488:            except Exception:
know_expand/observe.py:2504:            except Exception:
```
</details>

**[28] tool→Bash:** `grep -n "resume\|--stage\|pipeline.json\|run_id\|concurrent\|production\|multi-user\|Resume" /Users/cyan/ccr/know-expand` — Find README claims about resume and concurrency

<details><summary>[29] tool result (5846 chars)</summary>

```
22:# Resume a stopped run
24:uv run know-expand --resume 4
27:Each run writes to `runs/{run_id}/` containing `state/`, `logs/`, and `output/`.
156:Stages skip automatically if already complete (`stage_is_complete()` checks `pipeline.json`). Resume a stopped run via the web UI Resume button or `--resume N`.
164:know-expand tail [run_id] [--runs-dir DIR]
171:  tail [run_id]             Stream pipeline events to stdout. Reads
172:                            runs/<run_id>/logs/events.jsonl; uses latest
173:                            run if run_id omitted. Pipe-friendly, greppable.
185:  --status                  Emit pipeline_status from pipeline.json; no work runs
186:  --resume STAGE            Resume from stage N (e.g. --resume 4)
187:  --stage STAGE             Run only stage N then stop
200:  --run-id ID               Reuse an existing run ID (for --resume)
207:{"event": "pipeline_paused", "reason": "taxonomy_review", "instructions": "Review taxonomy in web UI, then click Resume"}
208:{"event": "quota_exhausted", "stage": 5, "domain_id": "inference_systems", "resume_command": "know-expand --resume 5"}
388:*Default:* pipeline emits `pipeline_paused` and exits cleanly. User reviews proposals in `runs/{id}/state/audit/taxonomy_a.json` and `taxonomy_b.json`, edits `taxonomy.json`, and resumes. In the web UI, taxonomy proposals appear in a dedicated review pane.
438:**External API rate limiting:** 8 concurrent domain fetches hit Semantic Scholar (100 req/5 min unauthenticated), Crossref, and OpenAlex simultaneously. `aiolimiter` (async token bucket) serializes at the API call level without blocking domain-level parallelism.
470:**Prompt sizing and caching:** persona prompts are split into a shared context block (domain info, reader profile, graph nodes, gap analysis, bibliography, knowledge sources — marked `cache_control: {type: ephemeral}`) and a task block (persona-specific role and output instruction). The shared block exceeds Anthropic's 4,096-token caching thresh...[truncated]
```
</details>

**[31] tool→Bash:** `grep -rn "model_consistency\|domain_timeout\|asyncio.timeout\|from langgraph\|Send(" know_expand/ --include="*.py" | hea` — Check README claims: Send API, timeouts, checkpointer, model_consistency

<details><summary>[32] tool result (248 chars)</summary>

```
know_expand/pipeline.py:3:from langgraph.graph import StateGraph, END
know_expand/agents/lc_adapter.py:27:from langgraph.graph import END, MessagesState, StateGraph
know_expand/agents/lc_adapter.py:28:from langgraph.prebuilt import ToolNode
---
---
```
</details>

**[33] tool→Bash:** `grep -n "_spawn_pipeline\|pipeline_pid\|Resume\|resume" know_expand/observe.py | head -40` — Locate spawn and resume logic in observe.py

<details><summary>[34] tool result (2445 chars)</summary>

```
493:/* resume card */
494:#resume-card {
498:#resume-card.has-errors { background: var(--yellow-dim); border-color: #4a3a10; }
504:.resume-toggle { display: flex; gap: 6px; margin-top: 10px; }
505:.resume-btn {
510:.resume-btn:hover { border-color: var(--accent); color: var(--accent); }
511:.resume-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; }
514:#run-resume { display: none; }
750:        <!-- Resume card — shown by JS when prior run detected -->
751:        <div id="resume-card" style="display:none" class="setup-section">
756:          <div id="resume-info" class="rc-stages"></div>
757:          <div id="resume-toggle" class="resume-toggle">
758:            <button id="resume-btn-fresh" class="resume-btn active" onclick="setResumeMode(false)">Fresh run</button>
759:            <button id="resume-btn-resume" class="resume-btn" onclick="setResumeMode(true)">Resume</button>
762:        <input type="hidden" id="run-resume" value="0" />
1396:      updateResumeCard(s.stages || {}, s.input_path || '', s.run_id || '');
1401:        // Pipeline finished — return to setup but keep resume selected if prior run exists
1403:        if (_resumeInputPath) setResumeMode(true);
1479:function updateResumeCard(stages, inputPath, runId) {
1480:  const card = $('resume-card');
1488:    $('run-resume').value = '0';
1491:    _resumeInputPath = '';
1495:  _resumeInputPath = inputPath || '';
1516:  $('resume-info').textContent = info;
1519:let _resumeInputPath = '';   // set by updateResumeCard from prior run's input_path
1521:function setResumeMode(doResume) {
1522:  $('run-resume').value = doResume ? '1' : '0';
1523:  $('resume-btn-fresh').classList.toggle('active', !doResume);
1524:  $('resume-btn-resume').classList.toggle('active', doResume);
1525:  $('fresh-options').style.display = doResume ? 'none' : 'block';
1526:  $('run-start-btn').textContent = doResume ? '▶ Resume Run' : '▶ Start Run';
1527:  if (doResume && _resumeInputPath) {
1528:    $(...[truncated]
```
</details>

**[36] tool→Bash:** `sed -n 1690,1760p /Users/cyan/ccr/know-expand/know_expand/observe.py; echo ===; sed -n 2470,2560p /Users/cyan/ccr/know-e` — Read resume JS and server spawn code in observe.py

<details><summary>[37] tool result (2143 chars)</summary>

```
Exit code 1
    const d = await (await fetch('/api/keys')).json();
    renderProvGrid(d);
  } catch {}
}

async function startRun() {
  const isResume = $('run-resume').value === '1';
  const input = isResume ? _resumeInputPath : $('run-input').value.trim();
  if (!input) {
    if (isResume) {
      // Prior run predates input_path tracking — fall back to text field
      setResumeMode(false);
      $('run-input').focus();
      $('run-input').placeholder = 'enter input path to resume…';
    } else {
      $('run-input').focus();
    }
    return;
  }
  // In resume mode depth/autoTax/noPdf come from the prior run — pass
  // sensible defaults; the pipeline already has them baked into state.
  const depth = isResume ? 'standard' : $('run-depth').value;
  const autoTax = isResume ? true : $('run-auto-tax').checked;
  const noPdf = isResume ? true : $('run-no-pdf').checked;
  const resume = isResume;

  $('run-start-btn').disabled = true;
  $('run-start-btn').textContent = 'starting…';

  try {
    await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        input, depth, auto_taxonomy: autoTax, no_pdf: noPdf,
        resume,
      }),
    });

    // Invalidate status cache so stage list refreshes
    lastStatusEtag = '';

    // Always start in running mode; pollQa/pollTaxonomy switch reactively
    setRunMode('running');

    // Show all-events view so user sees live log immediately
    $('sl-all-events').click();
  } catch(e) {
    $('run-start-btn').disabled = false;
    $('run-start-btn').textContent = isResume ? '▶ Resume Run' : '▶ Start Run';
  }
}

// File browser
let fbOpen = false;
let fbCwd = null;

function toggleBrowser() {
  const el = $('file-browser');
  fbOpen = !fbOpen;
  el.style.display = fbOpen ? 'flex' : 'none';
  if (fbOpen && fbCwd === null) loadDir('.');
}

async function loadDir(dir) {
  const r = await fetch('/api/files?dir=' + encodeURIComponent(dir));
  if (!r.ok...[truncated]
```
</details>

**[38] tool→Bash:** `grep -n "def _spawn_pipeline" /Users/cyan/ccr/know-expand/know_expand/observe.py` — Find _spawn_pipeline definition line

<details><summary>[39] tool result (62 chars)</summary>

```
2445:def _spawn_pipeline(runs_dir: Path, params: dict) -> int:
```
</details>

**[40] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[41] tool result (7427 chars)</summary>

```
2400	def _render_section(section_path: Path) -> str:
2401	    if not section_path.exists():
2402	        return ""
2403	    text = section_path.read_text(errors="replace")
2404	    try:
2405	        import markdown as _md_lib
2406	        return _md_lib.markdown(text, extensions=["tables", "fenced_code"])
2407	    except ImportError:
2408	        import html as _h
2409	        return "<pre>" + _h.escape(text) + "</pre>"
2410	
2411	
2412	
2413	
2414	def _list_files(dir_param: str) -> dict:
2415	    """Return directory listing for the file browser."""
2416	    try:
2417	        target = Path(dir_param).expanduser().resolve()
2418	    except Exception:
2419	        target = Path.cwd()
2420	    if not target.is_dir():
2421	        target = target.parent if target.parent.is_dir() else Path.cwd()
2422	
2423	    entries = []
2424	    try:
2425	        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
2426	            if p.name.startswith("."):
2427	                continue
2428	            try:
2429	                size = p.stat().st_size if p.is_file() else None
2430	            except OSError:
2431	                size = None
2432	            entries.append({
2433	                "name": p.name,
2434	                "type": "dir" if p.is_dir() else "file",
2435	                "path": str(p),
2436	                "size": size,
2437	            })
2438	    except PermissionError:
2439	        pass
2440	
2441	    parent = str(target.parent) if target != target.parent else None
2442	    return {"cwd": str(target), "parent": parent, "entries": entries}
2443	
2444	
2445	def _spawn_pipeline(runs_dir: Path, params: dict) -> int:
2446	    """Determine run_id, create run dir tree, clear Q&A state, spawn pipeline subprocess, return PID."""
2447	    from know_expand.state import new_run_id as _new_run_id
2448	
2449	    is_resume = params.get("resume", False)
2450	
2451	    if is_resume:
2452	        # Reuse the run directory from the currently activ...[truncated]
```
</details>

**[43] tool→Bash:** `grep -n "_find_active_state_dir\|def _pipeline_running\|api/run\|'/api/run'\|\"run\"" /Users/cyan/ccr/know-expand/know_e` — Find active-run selection and run endpoint guard

<details><summary>[44] tool result (470 chars)</summary>

```
1720:    await fetch('/api/run', {
2021:def _find_active_state_dir(runs_dir: Path) -> "Path | None":
2456:        active_state = _find_active_state_dir(runs_dir)
2615:            state_dir = _find_active_state_dir(runs_dir) or (runs_dir / "_default" / "state")
2679:            state_dir = _find_active_state_dir(runs_dir) or (runs_dir / "_default" / "state")
2688:            if self.path == "/api/run":
2700:                active_sd = _find_active_state_dir(runs_dir)
```
</details>

**[45] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[46] tool result (3267 chars)</summary>

```
2021	def _find_active_state_dir(runs_dir: Path) -> "Path | None":
2022	    """Return the state_dir of the currently running run, or the most-recently-modified one."""
2023	    if not runs_dir.exists():
2024	        return None
2025	    candidates = sorted(
2026	        (d for d in runs_dir.iterdir() if d.is_dir()),
2027	        key=lambda d: d.stat().st_mtime,
2028	        reverse=True,
2029	    )
2030	    # Prefer a live run
2031	    for run_dir in candidates:
2032	        sd = run_dir / "state"
2033	        if sd.is_dir() and _pipeline_is_running(sd):
2034	            return sd
2035	    # Fall back to most recent with a state dir
2036	    for run_dir in candidates:
2037	        sd = run_dir / "state"
2038	        if sd.is_dir():
2039	            return sd
2040	    return None
2041	
2042	
2043	def _load_state(state_dir: Path) -> dict:
2044	    result: dict = {"stages": {}, "domains": [], "run_id": "", "summary": "",
2045	                    "pipeline_running": _pipeline_is_running(state_dir),
2046	                    "state_dir": str(state_dir)}
2047	
2048	    pipeline_path = state_dir / "pipeline.json"
2049	    if pipeline_path.exists():
2050	        try:
2051	            data = json.loads(pipeline_path.read_text())
2052	            result["stages"] = data.get("stages", {})
2053	            result["run_id"] = data.get("run_id", "")
2054	            result["input_path"] = data.get("input_path", "") or data.get("input", "")
2055	        except Exception:
2056	            pass
2057	
2058	    tax_path = state_dir / "taxonomy.json"
2059	    if not tax_path.exists():
2060	        return result
2061	
2062	    try:
2063	        tax = json.loads(tax_path.read_text())
2064	    except Exception:
2065	        return result
2066	
2067	    graph_path = state_dir / "graph.json"
2068	    node_counts: dict[str, int] = {}
2069	    if graph_path.exists():
2070	        try:
2071	            g = json.loads(graph_path.read_text())
2072	            for n in g.get("nodes", []):
2073	    ...[truncated]
```
</details>

**[47] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[48] tool result (4214 chars)</summary>

```
2679	            state_dir = _find_active_state_dir(runs_dir) or (runs_dir / "_default" / "state")
2680	
2681	            length = int(self.headers.get("Content-Length", 0))
2682	            body = self.rfile.read(length)
2683	            try:
2684	                data = json.loads(body)
2685	            except json.JSONDecodeError:
2686	                data = {}
2687	
2688	            if self.path == "/api/run":
2689	                pid = _spawn_pipeline(runs_dir, data)
2690	                payload = json.dumps({"ok": True, "pid": pid}).encode()
2691	                self.send_response(200)
2692	                self.send_header("Content-Type", "application/json; charset=utf-8")
2693	                self.send_header("Content-Length", str(len(payload)))
2694	                self.end_headers()
2695	                self.wfile.write(payload)
2696	
2697	            elif self.path == "/api/stop":
2698	                killed = False
2699	                pid = None
2700	                active_sd = _find_active_state_dir(runs_dir)
2701	                pid_file = (active_sd / "pipeline_pid") if active_sd else (state_dir / "pipeline_pid")
2702	                if pid_file.exists():
2703	                    try:
2704	                        pid = int(pid_file.read_text().strip())
2705	                        os.killpg(os.getpgid(pid), signal.SIGTERM)
2706	                        killed = True
2707	                    except Exception:
2708	                        pass
2709	                    try:
2710	                        pid_file.unlink()
2711	                    except Exception:
2712	                        pass
2713	                payload = json.dumps({"ok": True, "killed": killed}).encode()
2714	                self.send_response(200)
2715	                self.send_header("Content-Type", "application/json; charset=utf-8")
2716	                self.send_header("Content-Length", str(len(payload)))
2717	                self.end_headers()
2718	                self.wfile.wri...[truncated]
```
</details>

**[50] tool→Bash:** `grep -rn "pipeline_paused" know_expand/ --include="*.py" | grep -v observe | head; echo ---; grep -n "_pipeline_is_runni` — Check pause events, run detection, quota handling, test coverage

<details><summary>[51] tool result (681 chars)</summary>

```
know_expand/state.py:179:    warn_events = {"ss_429_retry", "pipeline_paused"}
know_expand/state.py:287:        case "pipeline_paused":
---
2008:def _pipeline_is_running(state_dir: Path) -> bool:
2033:        if sd.is_dir() and _pipeline_is_running(sd):
2045:                    "pipeline_running": _pipeline_is_running(state_dir),
---
know_expand/agents/base.py:817:            "event": "quota_exhausted",
---
__init__.py
__pycache__
test_base.py
test_bibliography.py
test_centrality.py
test_config.py
test_llamacpp_routing.py
test_s10_pdf.py
test_s3_integration.py
test_s4_research.py
test_s5_synthesize.py
test_s6_verify.py
test_s7_assemble.py
test_sources.py
test_union_find.py
```
</details>

**[53] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/observe.py` 

<details><summary>[54] tool result (540 chars)</summary>

```
2008	def _pipeline_is_running(state_dir: Path) -> bool:
2009	    """True if the pipeline process (recorded in pipeline_pid) is still alive."""
2010	    pid_file = state_dir / "pipeline_pid"
2011	    if not pid_file.exists():
2012	        return False
2013	    try:
2014	        pid = int(pid_file.read_text().strip())
2015	        os.kill(pid, 0)   # signal 0 = probe only
2016	        return True
2017	    except (OSError, ValueError):
2018	        return False
2019	
2020	
2021	def _find_active_state_dir(runs_dir: Path) -> "Path | None":
```
</details>

**[55] tool→Read:** `/Users/cyan/ccr/know-expand/know_expand/agents/base.py` 

<details><summary>[56] tool result (1494 chars)</summary>

```
800	                    # geminicli errors are always terminal for this run (quota,
801	                    # session expired, empty output) — skip and try next model.
802	                    if _is_geminicli(model):
803	                        async with self._lock:
804	                            self._skip.add(model)
805	                            next_model = self._next_model()
806	                        emit({
807	                            "event": "model_geminicli_skip",
808	                            "role": self.role,
809	                            "skipped_model": model,
810	                            "next_model": next_model,
811	                            "reason": str(e)[:200],
812	                        })
813	                        continue
814	                    raise
815	
816	        emit({
817	            "event": "quota_exhausted",
818	            "role": self.role,
819	            "all_models_tried": list(self._skip),
820	        })
821	        raise RuntimeError(f"All models exhausted for role: {self.role}")
822	
823	
824	    async def call_with_tools(
825	        self,
826	        messages: list[dict],
827	        tools: list[dict],
828	    ) -> dict:
829	        """Single LLM call with tool definitions. Returns the raw message dict
830	        (which may contain tool_calls). Handles model fallback like call()."""
831	        while True:
832	            model = self._next_model()
833	            if model is None:
834	                break
```
</details>

**[58] tool→Bash:** `grep -rn "domain_ids" know_expand/ --include="*.py" | head -10; echo ---; grep -n "except RuntimeError\|except Exception` — Check domain_ids writers, stage error handling, centrality call sites

<details><summary>[59] tool result (1689 chars)</summary>

```
know_expand/pipeline.py:139:        "domain_ids": pipeline_json.get("domain_ids", []),
know_expand/state.py:104:    domain_ids: list[str]
know_expand/stages/s7_synthesize.py:124:Domain IDs in scope: {domain_ids}
know_expand/stages/s7_synthesize.py:302:    domain_ids = [s.get("domain_id", "") for s in summaries]
know_expand/stages/s7_synthesize.py:377:                domain_ids=json.dumps(domain_ids),
know_expand/stages/s10_assemble.py:35:    domain_ids = [d["id"] for d in domains]
know_expand/stages/s10_assemble.py:36:    prerequisite_in_count: dict[str, int] = {did: 0 for did in domain_ids}
know_expand/stages/s10_assemble.py:45:    return domain_ids
know_expand/stages/s10_assemble.py:57:    domain_ids = [d["id"] for d in domains]
know_expand/stages/s10_assemble.py:60:    prerequisite_in_count: dict[str, int] = {did: 0 for did in domain_ids}
---
know_expand/stages/s4_audit.py:222:        except Exception as exc:
know_expand/stages/s4_audit.py:259:        except Exception as exc:
know_expand/stages/s4_audit.py:430:            except Exception as exc:
know_expand/stages/s4_audit.py:432:                    "event": "domain_failed",
know_expand/stages/s5_research.py:425:        except Exception as exc:
know_expand/stages/s5_research.py:441:    except Exception as exc:
know_expand/stages/s5_research.py:503:        except Exception as exc:
know_expand/stages/s5_research.py:697:        except Exception as exc:
know_expand/stages/s5_research.py:1000:                "event": "s5_domain_failed",
---
know_expand/stages/s2_extract.py:17:from know_expand.centrality import compute_centrality, BOILERPLATE
know_expand/stages/s2_extract.py:447:        tier = compute_centrality(
```
</details>

**[61] tool→Bash:** `grep -n "\"event\": \"s7_\|'event': 's7_" know_expand/stages/s10_assemble.py | head; echo ---; grep -n "\"event\": \"s4_` — Check event-name vs stage-number drift

<details><summary>[62] tool result (1923 chars)</summary>

```
426:        "event": "s7_bibliography_merged",
444:                "event": "s7_section_missing",
517:        "event": "s7_markdown_written",
556:        emit({"event": "s7_pdf_skipped", "reason": "no_pdf_flag"})
568:                "event": "s7_pdf_skipped",
573:            emit({"event": "s7_pdf_start", "output": str(pdf_path)})
591:                        "event": "s7_pdf_error",
602:                            "event": "s7_pdf_complete",
608:                            "event": "s7_pdf_error",
613:                emit({"event": "s7_pdf_error", "reason": "pandoc_timeout"})
---
know_expand/stages/s7_synthesize.py:323:    emit({"event": "s5_structural_start", "domain_count": len(summaries)})
know_expand/stages/s7_synthesize.py:324:    emit({"event": "s5_semantic_start", "domain_count": len(summaries)})
know_expand/stages/s7_synthesize.py:325:    emit({"event": "s5_connector_start", "domain_count": len(summaries)})
know_expand/stages/s7_synthesize.py:370:        emit({"event": "s5_critique_round", "round": rnd, "total_rounds": rounds})
know_expand/stages/s7_synthesize.py:408:            emit({"event": "s5_critic_stalled", "round": rnd})
know_expand/stages/s7_synthesize.py:427:    emit({"event": "s5_critic_loop_done", "termination_reason": termination_reason, "rounds_run": rnd})
know_expand/stages/s5_research.py:414:        "event": "s5_bibliography_expansion_start",
know_expand/stages/s5_research.py:443:        emit({"event": "s5_bibliography_expansion_skipped", "domain_id": domain_id, "reason": str(exc)[:120]})
know_expand/stages/s5_research.py:494:        "event": "s5_bibliography_expanded",
know_expand/stages/s5_research.py:666:            "event": "s5_domain_start",
know_expand/stages/s5_research.py:682:    emit({"event": "s5_domain_start", "domain_id": domain_id, "label": domain_label})
know_expand/stages/s5_research.py:784:    emit({"event": "s5_personas_done", "domain_id": domain_id,
```
</details>

**[64] tool→Bash:** `grep -n "table\|proof\|BOILERPLATE\|boilerplate" /Users/cyan/ccr/know-expand/tests/test_centrality.py | head; echo ---; ` — Verify centrality substring filter false positives empirically

<details><summary>[65] tool result (251 chars)</summary>

```
7:def test_boilerplate_excluded():
48:def test_boilerplate_partial_match():
49:    # Terms containing boilerplate substrings are excluded
---
hash table -> None
zero-knowledge proof -> None
lookup table -> None
proof of stake -> None
configure -> None
```
</details>

## Structured Output
```json
{
  "summary": "The orchestration layer is a well-instrumented single-operator local tool, not a production orchestrator. LangGraph is a decorative linear wrapper (no checkpointer, no conditional edges, no interrupts, state carries 6 constants), real state lives in pipeline.json + sentinel files, and the resume story is fragile: the canonical store is written non-atomically with multi-writer races, several documented CLI flags (--resume, --no-pdf, --stage) are dead or silently dropped, and web resume silently downgrades run depth. There is no run-failure handling at all \u2014 an unhandled stage exception kills the process with stderr piped to DEVNULL and no terminal event \u2014 and the entire web control plane assumes exactly one active run discovered by directory mtime scan, which is structurally incompatible with the stated multi-user production goal.",
  "strengths": [
    "The emit() event system (know_expand/state.py:157-364) is genuinely good: every event lands in events.jsonl as timestamped JSONL, is routed to leveled log files, and gets a curated human progress line \u2014 a real audit trail and the dashboard's single source of truth.",
    "Filesystem-canonical state is the right instinct: pipeline.json stage markers plus per-domain .done sentinels (state.py:111-150, used consistently in all 11 stages, e.g. s4_audit.py:267, s5_research.py:669) give whole-stage and partial sub-stage idempotency without a database.",
    "All 11 stages uniformly implement the stage_is_complete() skip check \u2014 the resume convention is followed everywhere, not just in some stages.",
    "Real subprocess hygiene: the SIGCHLD SIG_IGN / preexec_fn SIG_DFL pairing (observe.py:2516-2528) and process-group SIGTERM on stop (observe.py:2705) show hard-won POSIX correctness.",
    "probe_models() at startup (pipeline.py:150-151) fails fast on missing keys/binaries before burning LLM calls.",
    "atomic_write() exists (state.py:376-380) and leaf utilities (union_find.py, centrality.py, config dataclasses) are small, typed, and unit-tested."
  ],
  "issues": [
    {
      "layer": "architecture",
      "severity": "critical",
      "title": "Single-active-run control plane: orchestration is structurally incompatible with concurrent multi-user operation",
      "detail": "Every web API operation resolves \"the\" run via _find_active_state_dir(), an mtime-sorted directory scan (observe.py:2021-2040, used at 2456, 2615, 2679, 2700). POST /api/run spawns a subprocess unconditionally \u2014 no guard against an already-running pipeline, no queue, no concurrency limit (observe.py:2688-2695). Resume reuses whichever run happens to be 'active' by mtime (observe.py:2451-2459). Human-in-the-loop IPC is loose files in one state dir (qa_queue.jsonl, taxonomy_choice.json). state.py's module globals (_run_id, _event_fh, fixed 'know_expand' logger, state.py:26-29, 78-84) hard-bind one run per process \u2014 fine for process-per-run, but there is no orchestrator above the processes: no run registry, no run addressing in any endpoint, no tenancy, no auth. Two users (or two clicks) produce two subprocesses overwriting the same pipeline_pid (observe.py:2529), and /api/stop kills only one. Supporting many concurrent users requires a job system, not patches to this layer.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/observe.py:2021-2040",
        "/Users/cyan/ccr/know-expand/know_expand/observe.py:2688-2695",
        "/Users/cyan/ccr/know-expand/know_expand/state.py:26-29"
      ]
    },
    {
      "layer": "execution",
      "severity": "critical",
      "title": "Unhandled stage exceptions kill the run silently: no run_failed event, web-spawned stderr discarded",
      "detail": "Neither pipeline.py:155 (compiled.ainvoke) nor cli.py:166-175 (asyncio.run) wraps execution in try/except. A stage-level exception (Docling parse failure in S0, pandoc failure in S10, the RuntimeError from quota_exhausted at agents/base.py:821 escaping a stage) propagates to process exit with a traceback on stderr. But _spawn_pipeline pipes stderr to DEVNULL (observe.py:2524-2525), and unhandled exceptions never pass through emit() or the logger, so errors.log and events.jsonl contain nothing. The dashboard infers death only from the pid probe. Result: a web-launched run that crashes leaves zero diagnostics anywhere. Only SIGTERM gets a terminal event (run_stopped, cli.py:14-19); crash and Ctrl-C (SIGINT unhandled) get none. The events vocabulary has no run_failed/run_complete terminal state at all (grep confirms run_failed appears nowhere).",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/pipeline.py:155",
        "/Users/cyan/ccr/know-expand/know_expand/cli.py:166-175",
        "/Users/cyan/ccr/know-expand/know_expand/observe.py:2524-2525"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "pipeline.json \u2014 the sole resume mechanism \u2014 is written non-atomically with unsynchronized multi-writer races",
      "detail": "save_pipeline_json uses p.write_text() (state.py:134-136) even though atomic_write() exists 240 lines below in the same file (state.py:376-380). mark_stage_complete is an unlocked read-modify-write (state.py:139-145). Concurrent writers exist: the pipeline process writes markers while the web server's POST /api/stage/:id/clear does its own read-modify-write of the same file (observe.py:2720-2729), and the dashboard reads it every 2s poll. A SIGTERM arriving mid-write (the handler raises SystemExit at an arbitrary bytecode, cli.py:16-19) or a crash can truncate pipeline.json \u2014 corrupting the only resume store at exactly the moment resume matters; load_pipeline_json (state.py:127-131) would then raise on every subsequent start and status call.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/state.py:134-145",
        "/Users/cyan/ccr/know-expand/know_expand/state.py:376-380",
        "/Users/cyan/ccr/know-expand/know_expand/observe.py:2720-2729"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "Dead and broken CLI surface: --resume is a no-op, --no-pdf is silently dropped, four more flags do nothing",
      "detail": "(1) --resume STAGE: cli.py:174 passes resume_stage to run_pipeline, which accepts it (pipeline.py:110) and never references it \u2014 the parameter is dead. Worse, README:22-24 and 186 present 'know-expand --resume 4' as the canonical recovery command, but without --run-id it generates a fresh UUID (cli.py:125) and starts from scratch in a new directory. Actual resume = --run-id + the per-stage skip checks; --resume itself changes nothing. (2) --no-pdf: parsed (cli.py:76) but never forwarded \u2014 the run_pipeline call at cli.py:166-175 omits no_pdf, so it defaults False. The web UI dutifully passes --no-pdf (observe.py:2513-2514) and it is dropped on the floor; PDF always builds. (3) --stage, --user-profile, --no-bibliography-fetch, --human (cli.py:75-87): parsed, documented in README:187+, referenced nowhere in the codebase (grep confirms). Six documented flags, zero or broken implementations.",
      "file_refs": [
        "/Users/cyan/ccr/know-expand/know_expand/cli.py:74-107",
        "/Users/cyan/ccr/know-expand/know_expand/cli.py:166-175",
        "/Users/cyan/ccr/know-expand/know_expand/pipeline.py:101-155"
      ]
    },
    {
      "layer": "execution",
      "severity": "high",
      "title": "Web-UI resume silently downgrades a deep run to standard depth",
      "detail": "observe.py JS hardcodes depth='standard' and noPdf=true on resume, with the comment 'the pipeline already has them baked into state' (observe.py:~1709-1713). That is false: depth is never persisted \u2014 run_pipeline writes only run_id and input_path into pipeline.json (pipeline.py:128-131), and depth re-enters PipelineState from the CLI --depth flag on every invocation (cli.py:170, pipeline.py:138). So resuming a crashed --depth dee
```

<details><summary>[69] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[70] assistant:** Audit complete. Findings returned via structured output.
