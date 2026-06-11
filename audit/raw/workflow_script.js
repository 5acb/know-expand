export const meta = {
  name: 'know-expand-full-audit',
  description: 'Audit know-expand at objective/architecture/planning/execution layers + market research for production & business path',
  phases: [
    { title: 'Analyze', detail: '6 codebase analysts + 4 web researchers' },
    { title: 'Verify', detail: 'adversarially verify critical/high code findings' },
    { title: 'Critique', detail: 'bear/bull business critic + completeness critic' },
  ],
}

const REPO = '/Users/cyan/ccr/know-expand'

const CODE_SCHEMA = {
  type: 'object',
  required: ['summary', 'strengths', 'issues', 'production_gaps'],
  properties: {
    summary: { type: 'string', description: '2-4 sentence assessment of this subsystem' },
    strengths: { type: 'array', items: { type: 'string' } },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        required: ['layer', 'severity', 'title', 'detail'],
        properties: {
          layer: { type: 'string', enum: ['objective', 'architecture', 'planning', 'execution'] },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          title: { type: 'string' },
          detail: { type: 'string', description: 'Specific, evidence-backed explanation with file:line refs' },
          file_refs: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    production_gaps: { type: 'array', items: { type: 'string' } },
  },
}

const RESEARCH_SCHEMA = {
  type: 'object',
  required: ['summary', 'key_facts', 'implications'],
  properties: {
    summary: { type: 'string' },
    key_facts: {
      type: 'array',
      items: {
        type: 'object',
        required: ['fact'],
        properties: { fact: { type: 'string' }, source_url: { type: 'string' } },
      },
    },
    implications: { type: 'array', items: { type: 'string', description: 'What this means for know-expand specifically' } },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['verdict', 'reasoning'],
  properties: {
    verdict: { type: 'string', enum: ['confirmed', 'refuted', 'partially_confirmed'] },
    reasoning: { type: 'string' },
    correction: { type: 'string', description: 'If refuted/partial, what is actually true' },
  },
}

const COMMON = `You are auditing the codebase at ${REPO} (a Python multi-agent CLI called know-expand that turns a technical document into a long "knowledge expansion" PDF via a LangGraph pipeline of 11 stages, with a QuotaAwareRouter over many LLM providers and a single-file web dashboard). Read the actual files with Read/Grep/Bash. Be a rigorous senior staff engineer: every issue must cite specific files/lines and be classified by layer — "objective" (is the goal itself right / coherent), "architecture" (structural design decisions), "planning" (roadmap, sequencing, what's prioritized), "execution" (bugs, code quality, correctness). Judge against the goal of making this a production-grade product that many users run concurrently (SaaS or self-hosted), not a personal tool. Do NOT pad with trivia; do report real critical/high problems. Also read ${REPO}/CLAUDE.md and ${REPO}/README.md for stated intent so you can flag intent-vs-code mismatches.`

const codeTargets = [
  {
    kind: 'code', key: 'orchestration',
    prompt: `${COMMON}\n\nYour subsystem: core orchestration — ${REPO}/know_expand/pipeline.py, cli.py, state.py, config.py, union_find.py, centrality.py. Assess: how LangGraph is actually used (is it a real graph or a linear chain wrapper?), state management & resume mechanism (pipeline.json stage markers — is it robust? partial-stage resume? idempotency? crash mid-stage?), error handling, the emit() event system, signal handling, config handling (dict-based timeouts footgun), and whether this orchestration layer can support concurrent multi-user runs, queueing, retries, or horizontal scale. Identify what a production orchestrator (Temporal/LangGraph Platform-style durable execution) would require that this lacks.`,
  },
  {
    kind: 'code', key: 'llm-routing',
    prompt: `${COMMON}\n\nYour subsystem: LLM routing — ${REPO}/know_expand/agents/base.py (960 lines), agents/lc_adapter.py, agents/schemas.py, models.yaml. Assess: QuotaAwareRouter design (global mutable _PROBED_UNAVAILABLE set, per-router _skip, semaphores), the geminicli subprocess provider (spawning npx per call — latency, security, robustness), instructor mode selection, cost tracking accuracy, retry/backoff correctness (race conditions under asyncio concurrency?), testability, and whether routing free-tier/OAuth-CLI models first is sane for a product whose pitch is research-grade quality. Check models.yaml ordering vs the README claim "quality IS the product — no cheap shortcuts": cheapest models are tried FIRST for the researcher role — evaluate that contradiction. Also evaluate vendor lock-in/abstraction quality of litellm+instructor+langchain mixed together.`,
  },
  {
    kind: 'code', key: 'stages-early',
    prompt: `${COMMON}\n\nYour subsystem: stages S0–S4 — ${REPO}/know_expand/stages/s0_ingest.py, s2_extract.py, s1_assess.py, s3_graph.py, s4_audit.py plus sources.py and bibliography.py. Assess: ingestion robustness (Docling failure modes, URL fetch, scanned PDFs), 3-signal term extraction quality and determinism, the 10-15 turn human interview (S1) — is mandatory human interaction at two stages compatible with a scalable product? The taxonomy review loop (S3), the adversarial gap-finder loop (S4), Semantic Scholar/Crossref/OpenAlex rate limits (0.1-1 req/s) as a throughput ceiling, the 65/35 foundational/frontier bibliography split ("non-negotiable" per CLAUDE.md — evaluate whether hard-coding that is justified), and caching/reuse across runs (is the same Wikipedia/SS data refetched per run?).`,
  },
  {
    kind: 'code', key: 'stages-late',
    prompt: `${COMMON}\n\nYour subsystem: stages S5–S10 — ${REPO}/know_expand/stages/s5_research.py (1017 lines), s6_align.py, s7_synthesize.py, s8_verify.py, s9_prereq.py, s10_assemble.py. Assess: the three-persona research design (Theoretician/Engineer/Practitioner + Reconciler) — does it measurably add value or just 4x cost? The critic loops; prompt-cache engineering; S8 "verify" — read it closely: what does citation verification ACTUALLY check (key existence vs bibliography? semantic support of claims?) and how much hallucination can still pass through to the final PDF? [NEEDS_CITATION] policy (logged, never a build failure — evaluate that for a product claiming "verified citations"). S10 pandoc/xelatex assembly fragility (LaTeX escaping of LLM output, PDF build failures). Quantify where output quality is actually enforced vs merely prompted for.`,
  },
  {
    kind: 'code', key: 'webui',
    prompt: `${COMMON}\n\nYour subsystem: the web dashboard — ${REPO}/know_expand/observe.py (2768 lines, single file: HTML/CSS/JS template string + Python HTTP server). Assess: security (any auth? CSRF? arbitrary file read via /api/section or /api/files? API keys posted via /api/keys held in server env — exposure risk? path traversal? localhost-only binding?), architecture (hand-rolled http.server vs a framework; 2s polling vs SSE/websockets; file-based IPC via jsonl files — races?), multi-run/multi-user support, the SIGCHLD global hack and process management (PID files, process groups, orphan handling), and maintainability of a 2700-line single file with HTML inside Python. Judge: what must change for this to be a hosted product UI?`,
  },
  {
    kind: 'code', key: 'quality-infra',
    prompt: `${COMMON}\n\nYour subsystem: quality & delivery infrastructure — ${REPO}/tests/ (all 14 files), pyproject.toml, docker-compose.yml, uv.lock, .gitignore, any CI config (check .github/). Assess: what the 182 tests actually cover vs what matters (are LLM-dependent paths tested at all? mocked how? do tests cover the router fallback matrix, resume logic, observe.py endpoints?), test file naming vs current stage naming (test_s4_research.py vs s5_research.py — stale?), packaging (is this pip-installable? entry points? pinned deps? Python version), docker-compose contents (what does it run? prod-ready?), missing CI/CD, missing eval harness for OUTPUT quality (the product is generated text — where are the quality evals?), licensing of dependencies, and release/versioning readiness. Run the test suite if quick (uv run pytest --collect-only -q at minimum) and report.`,
  },
]

const researchTargets = [
  {
    kind: 'research', key: 'market',
    prompt: `You are a market analyst. Use WebSearch extensively (current date: June 2026). Research the competitive landscape for AI tools that help people deeply understand technical/scientific documents and fields: Google NotebookLM, OpenAI/Anthropic/Google "Deep Research" features, Elicit, Consensus, SciSpace, Semantic Scholar tools, Stanford STORM, gpt-researcher, Undermind, Perplexity, any "AI textbook/course generation" startups (e.g. anything that generates structured learning material from papers). For each: pricing, traction/users/funding if known, and what they do vs don't do. Then assess: where is the white space for a tool that produces a 200+ page verified, pedagogically-structured "field guide" from one paper? Is anyone paying for that? What adjacent segments (corporate L&D, consulting research, due diligence, defense/intel, pharma literature review) pay real money for synthesized research documents? Include source URLs.`,
  },
  {
    kind: 'research', key: 'prod-patterns',
    prompt: `You are a platform engineer. Use WebSearch extensively (current date: June 2026). Research current best practice for productionizing long-running multi-agent LLM pipelines like LangGraph apps: LangGraph Platform/Cloud (pricing, what it provides — checkpointing, queues, human-in-the-loop interrupts), Temporal for LLM workflows, durable execution patterns, job queue patterns for 1-3 hour jobs, multi-tenancy and per-tenant cost metering, observability standards (LangSmith, Langfuse, OpenTelemetry GenAI conventions), eval frameworks for long-form generated documents (e.g. LLM-as-judge pipelines, citation-faithfulness evals like what RAG evals use), and batch APIs (Anthropic/OpenAI batch discounts) for cost reduction. For each: concrete capability and source URL. Then state what a minimal production architecture for know-expand (1-3hr pipeline, human-in-the-loop at 2 stages, $1-150/run LLM cost) should look like.`,
  },
  {
    kind: 'research', key: 'business',
    prompt: `You are a startup strategist. Use WebSearch extensively (current date: June 2026). Research: (1) willingness to pay for AI research/learning deliverables — pricing of Deep Research tiers (OpenAI Pro, Gemini, Perplexity Max etc.), Elicit/SciSpace/Consensus plans, corporate spend on literature reviews and technical training content; (2) market sizes: EdTech content creation, market/technical research services, systematic literature review market (pharma/medtech), expert networks (GLG etc.); (3) cases of AI-generated long-form content products succeeding or failing commercially; (4) the commoditization risk: frontier labs shipping deep-research agents free/cheap — what happened to thin wrappers in 2024-2026? Give honest evidence-based assessment of which customer segment + wedge could plausibly support a venture-scale business for an automated "paper → field guide" engine, and which framings are dead on arrival. Source URLs required.`,
  },
  {
    kind: 'research', key: 'quality-risk',
    prompt: `You are an AI-quality researcher. Use WebSearch extensively (current date: June 2026). Research: (1) measured citation hallucination/error rates in LLM long-form generation and in commercial deep-research products (studies 2024-2026, e.g. academic audits of ChatGPT/Gemini citation accuracy, RAG faithfulness benchmarks); (2) how production systems enforce citation faithfulness (claim-level attribution, NLI entailment checks, retrieval-grounded generation, post-hoc verification services); (3) the reputational/legal exposure of selling AI-generated 'research-grade' documents with errors (any incidents, lawsuits, retraction-level embarrassments); (4) copyright status of derivative works generated from a single source paper (fair use for transformation, publisher stances 2025-2026). Conclude with: what quality bar and verification machinery would a paid 'verified knowledge expansion' product need to defend its core claim, and how far is 'check citation key exists in bibliography' from that bar. Source URLs required.`,
  },
]

phase('Analyze')
log('Fanning out 6 codebase analysts and 4 web researchers')

const analyzed = await pipeline(
  [...codeTargets, ...researchTargets],
  (t) => agent(t.prompt, {
    label: `${t.kind}:${t.key}`,
    phase: 'Analyze',
    schema: t.kind === 'code' ? CODE_SCHEMA : RESEARCH_SCHEMA,
  }).then(r => ({ ...t, result: r })),
  // verify critical/high code findings as each analyst completes; pass research through
  (a) => {
    if (!a || !a.result) return a
    if (a.kind !== 'code') return a
    const critical = a.result.issues.filter(i => i.severity === 'critical')
    const high = a.result.issues.filter(i => i.severity === 'high')
    const toVerify = [...critical, ...high.slice(0, 5)]
    const dropped = high.length - Math.min(high.length, 5)
    if (dropped > 0) log(`${a.key}: verifying ${toVerify.length} findings, skipping ${dropped} lower-priority high findings`)
    if (toVerify.length === 0) return a
    return parallel(toVerify.map(f => () =>
      agent(
        `You are an adversarial verifier. A code auditor claims the following about the repo at ${REPO}:\n\nTITLE: ${f.title}\nLAYER: ${f.layer}\nSEVERITY: ${f.severity}\nCLAIM: ${f.detail}\nFILES: ${(f.file_refs || []).join(', ')}\n\nRead the actual code and try to REFUTE this claim. Check: does the cited code actually behave as claimed? Is the severity inflated? Is there mitigating code elsewhere (search the repo)? If the claim is materially correct, confirm it. Default to refuted if the evidence is ambiguous.`,
        { label: `verify:${a.key}:${f.title.slice(0, 40)}`, phase: 'Verify', schema: VERDICT_SCHEMA }
      ).then(v => ({ finding: f, verdict: v }))
    )).then(verdicts => ({ ...a, verdicts: verdicts.filter(Boolean) }))
  }
)

const results = analyzed.filter(Boolean)
const code = results.filter(r => r.kind === 'code')
const research = results.filter(r => r.kind === 'research')

// compact digest for the critics — titles/verdicts/summaries only, not full detail
const digest = {
  code: code.map(c => ({
    subsystem: c.key,
    summary: c.result.summary,
    issues: c.result.issues.map(i => ({ layer: i.layer, severity: i.severity, title: i.title })),
    verified: (c.verdicts || []).map(v => ({ title: v.finding.title, verdict: v.verdict.verdict })),
    production_gaps: c.result.production_gaps,
  })),
  research: research.map(r => ({
    topic: r.key,
    summary: r.result.summary,
    facts: r.result.key_facts.map(f => f.fact).slice(0, 15),
    implications: r.result.implications,
  })),
}

phase('Critique')
log('Running bear/bull business critic and completeness critic')

const CRITIQUE_SCHEMA = {
  type: 'object',
  required: ['verdict_summary', 'points'],
  properties: {
    verdict_summary: { type: 'string' },
    points: { type: 'array', items: { type: 'object', required: ['title', 'argument'], properties: { title: { type: 'string' }, argument: { type: 'string' } } } },
  },
}

const critics = await parallel([
  () => agent(
    `You are a brutally honest venture partner evaluating know-expand (repo at ${REPO} — read README.md for the pitch). The founder wants to know how to make it "production ready and a billion dollar product". Here is the audit + market digest:\n\n${JSON.stringify(digest, null, 1)}\n\nGive the strongest BEAR case (why this dies: commoditization by frontier-lab deep research, no distribution, quality claims it can't defend, 1-3hr latency, who actually pays) AND the strongest BULL case (what wedge, segment, and repositioning could actually work — be specific about the customer and why incumbents won't serve them). End with the single most important strategic decision the founder must make. Do not flatter.`,
    { label: 'critic:bear-bull', phase: 'Critique', schema: CRITIQUE_SCHEMA }
  ),
  () => agent(
    `You are a completeness critic for a codebase+market audit of ${REPO}. Digest of what 10 analysts covered:\n\n${JSON.stringify(digest, null, 1)}\n\nIdentify what is MISSING from this audit that materially affects "make it production ready" or the business case: dimensions not analyzed (e.g. data privacy/GDPR for uploaded papers, accessibility, i18n, GPU/embedding infra costs, BGE-M3+Docling local compute requirements on a server, license compliance of dependencies like Docling/KeyBERT for commercial use, support burden of xelatex), claims asserted but unverified, and contradictions between analysts. Read repo files only as needed to check specifics (e.g. pyproject.toml licenses). List concrete gaps with why each matters.`,
    { label: 'critic:completeness', phase: 'Critique', schema: CRITIQUE_SCHEMA }
  ),
])

return {
  code: code.map(c => ({ subsystem: c.key, ...c.result, verdicts: (c.verdicts || []).map(v => ({ title: v.finding.title, verdict: v.verdict.verdict, reasoning: v.verdict.reasoning, correction: v.verdict.correction })) })),
  research: research.map(r => ({ topic: r.key, ...r.result })),
  bearBull: critics[0],
  completeness: critics[1],
}