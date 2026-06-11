# Agent: critic__completeness
**ID:** `agent-a967d492f5f662b53`  **Turns:** 33

## Prompt
```
You are a completeness critic for a codebase+market audit of /Users/cyan/ccr/know-expand. Digest of what 10 analysts covered:

{
 "code": [
  {
   "subsystem": "orchestration",
   "summary": "The orchestration layer is a well-instrumented single-operator local tool, not a production orchestrator. LangGraph is a decorative linear wrapper (no checkpointer, no conditional edges, no interrupts, state carries 6 constants), real state lives in pipeline.json + sentinel files, and the resume story is fragile: the canonical store is written non-atomically with multi-writer races, several documented CLI flags (--resume, --no-pdf, --stage) are dead or silently dropped, and web resume silently downgrades run depth. There is no run-failure handling at all — an unhandled stage exception kills the process with stderr piped to DEVNULL and no terminal event — and the entire web control plane assumes exactly one active run discovered by directory mtime scan, which is structurally incompatible with the stated multi-user production goal.",
   "issues": [
    {
     "layer": "architecture",
     "severity": "critical",
     "title": "Single-active-run control plane: orchestration is structurally incompatible with concurrent multi-user operation"
    },
    {
     "layer": "execution",
     "severity": "critical",
     "title": "Unhandled stage exceptions kill the run silently: no run_failed event, web-spawned stderr discarded"
    },
    {
     "layer": "execution",
     "severity": "high",
     "title": "pipeline.json — the sole resume mechanism — is written non-atomically with unsynchronized multi-writer races"
    },
    {
     "layer": "execution",
     "severity": "high",
     "title": "Dead and broken CLI surface: --resume is a no-op, --no-pdf is silently dropped, four more flags do nothing"
    },
    {
     "layer": "execution",
     "severity": "high",
     "title": "Web-UI resume silently downgrades a deep run to standard depth"
    },
    {
     "layer": "architecture",
     "severity": "high",
     "title": "LangGraph is a decorative facade: linear chain, no checkpointer, no interrupts, state that carries nothing"
    },
    {
     "layer": "execution",
     "severity": "high",
     "title": "Centrality boilerplate filter uses substring matching and silently discards legitimate core terms"
    },
    {
     "layer": "planning",
     "severity": "high",
     "title": "README documents an orchestrator that does not exist — aspirational claims presented as implemented"
    },
    {
     "layer": "architecture",
     "severity": "medium",
     "title": "Run/stage lifecycle has exactly one state ('complete') and liveness detection is pid-probe with reuse hazards"
    },
    {
     "layer": "architecture",
     "severity": "medium",
     "title": "Config layer mixes typed dataclasses with raw dicts and loads from CWD-relative paths with no validation"
    },
    {
     "layer": "execution",
     "severity": "medium",
     "title": "Event-name drift: stage 10 em...[truncated]
```

**[4] tool→Read:** `/Users/cyan/ccr/know-expand/pyproject.toml` 

<details><summary>[5] tool result (1177 chars)</summary>

```
1	[build-system]
2	requires = ["hatchling"]
3	build-backend = "hatchling.build"
4	
5	[project]
6	name = "know-expand"
7	version = "0.1.0"
8	requires-python = ">=3.11"
9	dependencies = [
10	    # Document parsing
11	    "docling>=2.0.0",
12	    # NLP / extraction
13	    "spacy>=3.7.0",
14	    "keybert",
15	    "sentence-transformers>=3.0.0",   # BGE-M3
16	    "numpy>=1.26.0",
17	    # LLM routing + structured outputs
18	    "litellm>=1.40.0",
19	    "anthropic>=0.30.0",
20	    "instructor>=1.3.0",
21	    "pydantic>=2.7.0",
22	    # Pipeline orchestration + agent loops
23	    "langgraph>=0.2.0",
24	    # Observability (serve renders markdown in browser)
25	    "markdown>=3.6",
26	    # Rate limiting
27	    "aiolimiter>=1.1.0",
28	    # HTTP
29	    "httpx>=0.27.0",
30	    # PDF integrity check
31	    "pypdf>=4.2.0",
32	    # .env loading
33	    "python-dotenv>=1.0.0",
34	]
35	
36	[project.scripts]
37	know-expand = "know_expand.cli:main"
38	
39	[project.optional-dependencies]
40	dev = [
41	    "pytest>=8.0.0",
42	    "pytest-asyncio>=0.23.0",
43	    "pytest-httpx>=0.30.0",
44	]
45	
46	[tool.pytest.ini_options]
47	asyncio_mode = "auto"
48	testpaths = ["tests"]
49	
```
</details>

**[6] tool→Bash:** `ls /Users/cyan/ccr/know-expand` — List repo root files

<details><summary>[7] tool result (111 chars)</summary>

```
CLAUDE.md
LICENSE
README.md
config.yaml
docker-compose.yml
know_expand
models.yaml
pyproject.toml
tests
uv.lock
```
</details>

**[9] tool→Bash:** `head -20 /Users/cyan/ccr/know-expand/LICENSE; echo ---; grep -n -iE "embed|bge|keybert|device|gpu|cuda|model_name" /User` — Check LICENSE and embedding config

<details><summary>[10] tool result (1090 chars)</summary>

```
MIT License

Copyright (c) 2026 Anas Ahmed

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
---
48:  embedding: "BAAI/bge-m3"
```
</details>

**[11] tool→Bash:** `grep -n -iE "privacy|gdpr|retention|delete|data|upload" /Users/cyan/ccr/know-expand/README.md | head -30; echo ---; grep` — Scan README for privacy/data mentions

<details><summary>[12] tool result (848 chars)</summary>

```
491:**Constraint:** only ADD content; never delete or rewrite existing text. Max 12 tool calls per domain.
750:Before re-running any unit, partial output files are deleted — not overwritten. This prevents corrupted partial writes from a previous crash from being merged with new output.
792:    volumes: [ollama_data:/root/.ollama]
799:  ollama_data:
902:- **Unconditional structural promotion:** `if name in structural_zones: return "core"` floods the core tier with bolded dataset names and italicized variables. Structural presence is a weight multiplier (`effective = occurrences × 2.0`), not a frequency bypass.
921:- **Silent resume after partial crash:** appending to partial files corrupts structure. Always delete before rewriting.
997:  config.py       — Config dataclass; timeouts is a dict (use cfg.timeouts.get(), not getattr)
---
1022
```
</details>

**[14] tool→Bash:** `grep -n -iE "SentenceTransformer|bge|keybert|device|cuda|torch" /Users/cyan/ccr/know-expand/know_expand/stages/s2_extrac` — Check embedding loading and heavyweight transitive deps in lockfile

<details><summary>[15] tool result (859 chars)</summary>

```
11:from keybert import KeyBERT
12:from sentence_transformers import SentenceTransformer
51:    kw_model: KeyBERT,
59:    nlp_text = _strip_code_from_text(text)  # code-free version for spaCy + KeyBERT
83:    # Signal C — KeyBERT embedding (on code-stripped text)
90:    keybert_terms = {kw.lower() for kw, _ in keywords}
96:    # Merge: lexical + keybert into base set; LLM adds conceptual terms
104:    for term in keybert_terms:
183:    """Remove fenced code blocks and inline code for NLP signals (spaCy, KeyBERT)."""
348:    embedding_model: SentenceTransformer,
---
name = "accelerate"
name = "docling"
name = "docling-core"
name = "docling-ibm-models"
name = "docling-parse"
name = "docling-slim"
name = "langchain-core"
name = "langchain-protocol"
name = "pyyaml"
name = "sentence-transformers"
name = "torch"
name = "torchvision"
name = "transformers"
```
</details>

**[16] tool→Bash:** `grep -n -iE "user-agent|mailto|contact" /Users/cyan/ccr/know-expand/know_expand/sources.py /Users/cyan/ccr/know-expand/k` — Check API etiquette headers and module list

<details><summary>[17] tool result (648 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/sources.py:24:# Wikipedia requires a descriptive User-Agent; without it they return 403.
/Users/cyan/ccr/know-expand/know_expand/sources.py:26:_WIKI_HEADERS = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
/Users/cyan/ccr/know-expand/know_expand/sources.py:38:# OpenAlex allows 10 req/s for polite pool (with email in User-Agent).
/Users/cyan/ccr/know-expand/know_expand/sources.py:51:    "User-Agent": "know-expand/1.0 (mailto:research@example.com)",
---
__init__.py
__pycache__
agents
bibliography.py
centrality.py
cli.py
config.py
observe.py
pipeline.py
sources.py
stages
state.py
union_find.py
```
</details>

**[19] tool→Bash:** `grep -rn "model_usage" /Users/cyan/ccr/know-expand/know_expand /Users/cyan/ccr/know-expand/README.md | head; echo ---; g` — Check whether model_usage.jsonl cost logging exists

<details><summary>[20] tool result (857 chars)</summary>

```
/Users/cyan/ccr/know-expand/README.md:43:**Cost:** a deep run is roughly 8 domains × (3 personas + 1 reconciler) × up to 3 critic rounds, plus adversarial loops in S4 and S7. ~150–300 LLM calls for the researcher/synthesizer role. At current Opus 4.7 pricing, expect **$50–150 per deep run** with Opus as primary researcher. Standard depth with Sonnet 4.6 is roughly $5–20. Monitor `state/audit/model_usage.jsonl` — every call is logged with `cost_usd`.
/Users/cyan/ccr/know-expand/README.md:723:Every LLM call is logged to `state/audit/model_usage.jsonl`:
/Users/cyan/ccr/know-expand/README.md:830:- **`state/audit/model_usage.jsonl`** — per-call cost and model provenance log. `cat ... | jq` answers every debug question.
/Users/cyan/ccr/know-expand/README.md:969:│   │   ├── model_usage.jsonl                   Per-call: model, tokens, cost, switches
---
```
</details>

**[21] tool→Bash:** `grep -n -iE "en_core_web_sm|language|english|lang" /Users/cyan/ccr/know-expand/know_expand/stages/s0_ingest.py /Users/cy` — Check language hardcoding and source licensing handling

<details><summary>[22] tool result (1181 chars)</summary>

```
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:41:For each term, provide: name (clean English noun phrase, no code syntax), aliases \
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:87:        stop_words="english",
/Users/cyan/ccr/know-expand/know_expand/stages/s2_extract.py:224:Examples: "asyncio", "xelatex", "docling", "langgraph", "json", "pdf", "docker", "spacy"
---
3:Fetches Wikipedia, PyPI, and arXiv content for terms extracted from the pipeline.
24:# Wikipedia requires a descriptive User-Agent; without it they return 403.
31:    source_type: Literal["wikipedia", "pypi", "arxiv", "openalex"]
116:async def _fetch_wikipedia(term: str, http: httpx.AsyncClient) -> "FetchedSource | None":
117:    """Search Wikipedia and return a plain-text extract for the best match."""
120:            "https://en.wikipedia.org/w/api.php"
139:                f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
168:                    _logger.debug("wikipedia relevance rejected %r → %r", term, title)
174:                    source_type="wikipedia",
181:                _logger.debug("wikipedia summary fetch failed for %r: %s", title, exc)
```
</details>

**[24] tool→Bash:** `grep -n -E "xelatex|pandoc|shell|escape|subprocess|run\(" /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py` — Check pandoc/xelatex invocation flags

<details><summary>[25] tool result (1097 chars)</summary>

```
7:import subprocess
80:    pandoc citeproc rejects entries that have null for string/number fields.
195:# LaTeX commands that must be inside $...$ to survive xelatex.
395:async def run(state: PipelineState, cfg: Config, no_pdf: bool = False) -> None:
523:    # Write bibliography in CSL-JSON format for pandoc citeproc
527:    # Write a pandoc-ready markdown copy with YAML frontmatter.
528:    # Embedding bibliography in frontmatter avoids the pandoc 3.x bug where
546:    pandoc_md = output_dir / "expanded_pandoc.md"
549:    pandoc_md.write_text("\n".join(frontmatter_lines) + body)
558:        pandoc_available = shutil.which("pandoc") is not None
559:        xelatex_available = shutil.which("xelatex") is not None
561:        if not pandoc_available or not xelatex_available:
563:            if not pandoc_available:
564:                missing.append("pandoc")
565:            if not xelatex_available:
566:                missing.append("xelatex")
574:            pandoc_cmd = [
575:                "pandoc",
576:                str(pandoc_md),
577:                "--pdf-engine=xelatex",
```
</details>

**[26] tool→Bash:** `sed -n 574,615p /Users/cyan/ccr/know-expand/know_expand/stages/s10_assemble.py` — Read pandoc command construction

<details><summary>[27] tool result (1828 chars)</summary>

```
            pandoc_cmd = [
                "pandoc",
                str(pandoc_md),
                "--pdf-engine=xelatex",
                "--citeproc",           # process [@key] citations via frontmatter bibliography
                "-o", str(pdf_path),
            ]

            try:
                result = subprocess.run(
                    pandoc_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    emit({
                        "event": "s7_pdf_error",
                        "reason": "pandoc_error",
                        "returncode": result.returncode,
                        "stderr": result.stderr[:1000],
                    })
                    _logger.error("s10: pandoc failed (rc=%d): %s", result.returncode, result.stderr[:500])
                else:
                    if pdf_path.exists() and pdf_path.stat().st_size > 0:
                        pages = _count_pdf_pages(pdf_path)
                        output_pdf = str(pdf_path)
                        emit({
                            "event": "s7_pdf_complete",
                            "path": str(pdf_path),
                            "pages": pages,
                        })
                    else:
                        emit({
                            "event": "s7_pdf_error",
                            "reason": "pdf_not_created",
                            "stderr": result.stderr[:500],
                        })
            except subprocess.TimeoutExpired:
                emit({"event": "s7_pdf_error", "reason": "pandoc_timeout"})
            except Exception as exc:
                emit({"event": "s7_pdf_error", "reason": "pandoc_exception", "error": str(exc)[:200]})
```
</details>

## Structured Output
```json
{
  "verdict_summary": "The audit is deep on code mechanics and market comps but has a blind hemisphere: it never analyzes data privacy/confidentiality, local-compute COGS, content licensing of fetched sources, i18n/accessibility, or the legal/contract layer \u2014 and several of its own recommended business wedges (pharma, defense/intel, 'reproducible evidence synthesis') are directly contradicted by code findings the audit itself confirmed but never cross-referenced. At least two analyst contradictions (model_usage.jsonl existence; a refuted path-traversal finding still listed as a production gap) made it into the final digest unreconciled, and the single number the business case turns on \u2014 the cost to build the L5 verification moat \u2014 is estimated nowhere.",
  "points": [
    {
      "title": "Data privacy / GDPR / confidentiality: entirely missing dimension",
      "argument": "Across 10 analysts there is one passing clause ('free-tier ToS/data-training exposure unaddressed'). Nobody analyzed: runs/{run_id}/ retains the full uploaded document, all LLM outputs, and the S1 interview-derived UserProfile (reader profiling = personal data under GDPR Art. 4) indefinitely with no deletion endpoint, no encryption at rest, no retention policy; user documents fan out to 5+ subprocessors (Anthropic, OpenAI, Google API, Groq, Mistral, plus the geminicli free OAuth tier whose consumer terms grant Google training rights); no DPA/subprocessor inventory exists; prod-patterns noted Anthropic batch mode is not ZDR-eligible but never connected that to the proposed pharma wedge. This matters because the audit's own #1 GTM recommendation \u2014 pharma medical affairs handling confidential pre-publication evidence \u2014 is a confidentiality-first buyer; without a data-handling story the recommended business case is unsellable regardless of code quality."
    },
    {
      "title": "Defense/intel wedge contradicts the architecture: pipeline cannot run air-gapped",
      "argument": "The market analyst recommends defense/intel analyst onboarding (Primer's segment) as wedge #3. No analyst noted the pipeline is structurally cloud-bound: every stage depends on live external APIs (Wikipedia, Semantic Scholar, OpenAlex, arXiv, PyPI in sources.py/bibliography.py), frontier LLM APIs, an npx-spawned Google OAuth CLI, and a first-run HuggingFace download of BGE-M3 weights (s2_extract.py imports SentenceTransformer('BAAI/bge-m3')). Defense/intel buyers require on-prem/air-gapped or IL4+ deployment. This wedge should either be struck from the GTM list or costed as a separate offline product (local models + offline corpus mirrors) \u2014 the audit does neither."
    },
    {
      "title": "'Reproducible, auditable evidence synthesis' positioning is refuted by confirmed routing findings \u2014 never cross-referenced",
      "argument": "The business analyst proposes repositioning as 'reproducible, auditable evidence synthesis' for pharma. The llm-routing analyst confirmed: quota-driven nondeterministic model substitution mid-run, no temperature/max_tokens control anywhere, and cost/model-mix logging (model_usage.jsonl) that does not exist in code. A run's model mix cannot be reproduced or even described post-hoc. GxP/audit-trail buyers require exactly the per-call provenance record the README fakes. The audit contains both halves of this contradiction and never connects them; the fix (persist actual model, params, prompt hash per call) is cheap and should be a named prerequisite of the pharma pivot."
    },
    {
      "title": "Local compute / infra COGS unanalyzed; all unit economics are token-only",
      "argument": "uv.lock (/Users/cyan/ccr/know-expand/uv.lock) pins torch, torchvision, transformers, accelerate, docling-ibm-models, sentence-transformers \u2014 a multi-GB install plus ~2.3GB BGE-M3 weights and Docling layout models downloaded at first run. No analyst measured S0 (Docling CPU parse) or S2 (KeyBERT+BGE-M3 embedding) wall-clock, RAM footprint per concurrent run (plausibly 8-12GB), container image size, or GPU-vs-CPU server pricing. The market analysis prices runs at $100-500 against token cost alone, and even the README's $50-150/deep-run token estimate (README.md:43) was never validated against actual prompt sizes. The hosted-product margin math \u2014 the core of the business case \u2014 has no compute, storage, bandwidth, or HITL-operator-time line items."
    },
    {
      "title": "xelatex/pandoc: operational burden and an unflagged LaTeX-injection vector",
      "argument": "s10_assemble.py:574-580 invokes pandoc with --pdf-engine=xelatex and no --sandbox flag, so raw LaTeX in LLM-generated (or source-document-derived) markdown passes through to xelatex, where default TeX Live openin_any settings allow \\input-style reads of server files into the shipped PDF \u2014 a data-exfiltration vector for a hosted product compiling untrusted content; no analyst examined it. Separately unanalyzed: TeX Live adds 2-5GB to any image, font/Unicode coverage failures on arbitrary LLM math output are a recurring support class (the audit found the math-fixer incoherence but not the ops burden), pandoc is GPL-2+ (fine as subprocess, but image redistribution carries a source-offer obligation), and no alternative (Typst, HTML+print CSS) was evaluated."
    },
    {
      "title": "Content licensing of fetched third-party sources: the real license exposure, not analyzed",
      "argument": "quality-risk covered the source-paper derivative-work question thoroughly but never the inbound content: Wikipedia extracts (sources.py:116-181, en.wikipedia hardcoded) are CC BY-SA \u2014 share-alike text injected into S5 persona prompts and paraphrased into a commercial PDF raises attribution/SA obligations with no attribution mechanism in the output; Semantic Scholar abstracts (stored in audit caches and used by S8) are publisher-licensed with redistribution restrictions under S2's ODC-BY/dataset terms; OpenAlex is CC0 (fine). Also concrete: sources.py:51 sends OpenAlex a fake polite-pool identity 'mailto:research@example.com' \u2014 a ToS-etiquette violation that risks rate-deprioritization and is indefensible in a commercial product."
    },
    {
      "title": "Dependency code-license review was never done \u2014 it is actually clean, and saying so closes a risk cheaply",
      "argument": "pyproject.toml deps check out for commercial use: docling MIT (IBM), keybert MIT, sentence-transformers Apache-2.0, BGE-M3 model weights MIT, spaCy + en_core_web_sm MIT, litellm MIT, instructor MIT, langgraph MIT, pypdf BSD; the project itself ships MIT (LICENSE, copyright Anas Ahmed 2026). The only copyleft touchpoints are pandoc (GPL-2+) and TeX Live components, both invoked as subprocesses \u2014 compliance burden is a source-offer if redistributed in an image. The audit left this as an open unknown; it should be recorded as resolved so the licensing risk register correctly points at content (previous point) and the geminicli consumer ToS, not code."
    },
    {
      "title": "English-only pipeline ceiling: i18n never assessed against the market sizing",
      "argument": "Hardcoded: en.wikipedia.org endpoints (sources.py:120,139), KeyBERT stop_words='english' (s2_extract.py:87), spaCy en_core_web_sm (a CLAUDE.md constraint), English-language prompts throughout. Non-English source papers and non-English deliverables are unsupported. The market analysis sizes against global pharma, EU consulting, and a $400B corporate-training market without noting the product addresses only the English slice, and no analyst listed i18n cost in the production-readiness gap lists."
    },
    {
      "title": "Accessibility unexamined: untagged PDFs and an inaccessible web UI",
      "argument": "xelatex output is not tagged PDF (no PDF/UA), and the observe.py dashboard has no accessibility consideration at all. The European Accessibility Act has applied to consumer-facing digital services since June 2
```

<details><summary>[31] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[32] assistant:** Completeness critique delivered via StructuredOutput: 15 concrete gaps covering missing dimensions (privacy/GDPR, compute COGS, content licensing, LaTeX injection, i18n/a11y, liability), verified analyst contradictions (model_usage.jsonl fiction vs "logged", refuted path-traversal still listed as a gap), and unreconciled strategy conflicts — each grounded in repo checks (pyproject.toml, uv.lock, sources.py:51, s2_extract.py:87, s10_assemble.py:574-580, LICENSE).
