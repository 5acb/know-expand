# Agent: research__prod_patterns
**ID:** `agent-a27f519d2e3754d1a`  **Turns:** 50

## Prompt
```
You are a platform engineer. Use WebSearch extensively (current date: June 2026). Research current best practice for productionizing long-running multi-agent LLM pipelines like LangGraph apps: LangGraph Platform/Cloud (pricing, what it provides — checkpointing, queues, human-in-the-loop interrupts), Temporal for LLM workflows, durable execution patterns, job queue patterns for 1-3 hour jobs, multi-tenancy and per-tenant cost metering, observability standards (LangSmith, Langfuse, OpenTelemetry GenAI conventions), eval frameworks for long-form generated documents (e.g. LLM-as-judge pipelines, citation-faithfulness evals like what RAG evals use), and batch APIs (Anthropic/OpenAI batch discounts) for cost reduction. For each: concrete capability and source URL. Then state what a minimal production architecture for know-expand (1-3hr pipeline, human-in-the-loop at 2 stages, $1-150/run LLM cost) should look like.
```

**[4] tool→ToolSearch:** `{'query': 'select:WebSearch,WebFetch', 'max_results': 2}`

**[7] tool→WebSearch:** `{'query': 'LangGraph Platform pricing 2026 checkpointing task queues human-in-the-loop interrupts'}`

**[8] tool→WebSearch:** `{'query': 'Temporal durable execution LLM workflows agents best practice 2026'}`

<details><summary>[9] tool result (2904 chars)</summary>

```
Web search results for query: "Temporal durable execution LLM workflows agents best practice 2026"

Links: [{"title":"Durable Execution Solutions | Temporal","url":"https://temporal.io/"},{"title":"AI Workflow Orchestration Guide 2026 | AI Workflow Lab","url":"https://aiworkflowlab.dev/article/ai-workflow-orchestration-in-production-building-durable-agent-pipelines-with-langgraph-and-temporal"},{"title":"Durable Execution for LLM Agents 2026: Temporal + LangGraph — AppScale Blog","url":"https://appscale.blog/en/blog/durable-execution-llm-agents-temporal-langgraph-checkpointing-2026"},{"title":"Announcing new Temporal capabilities from Replay 2026 | Temporal","url":"https://temporal.io/blog/replay-2026-product-announcements"},{"title":"Temporal for AI Agent Workflows: Durable Execution and Workflow-as-Code | CallSphere Blog","url":"https://callsphere.ai/blog/temporal-ai-agent-workflows-durable-execution-workflow-as-code"},{"title":"Of course you can build dynamic AI agents with Temporal | Temporal","url":"https://temporal.io/blog/of-course-you-can-build-dynamic-ai-agents-with-temporal"},{"title":"Temporal for AI | Temporal","url":"https://temporal.io/solutions/ai"},{"title":"Durable Workflow Platforms for AI Agents and LLM Workloads","url":"https://render.com/articles/durable-workflow-platforms-ai-agents-llm-workloads"}]

Based on the search results, here's what I found about temporal durable execution for LLM workflows and agents in 2026:

## Key Best Practices

LangGraph and Temporal have emerged as the production standard for AI workflow orchestration, with a two-layer architecture combining them increasingly popular for mission-critical agent systems.

## Core Principles for Temporal

Workflows serve as the orchestration layer defining application structure and must be deterministic so Temporal can help agents survive crashes and outages, while Activities are where actual work happens such as calling LLMs and invoking tools. This separation makes Temporal perfect...[truncated]
```
</details>

<details><summary>[10] tool result (3245 chars)</summary>

```
Web search results for query: "LangGraph Platform pricing 2026 checkpointing task queues human-in-the-loop interrupts"

Links: [{"title":"LangGraph Pricing Guide: How Much Does It Cost? - ZenML Blog","url":"https://www.zenml.io/blog/langgraph-pricing"},{"title":"LangSmith Plans and Pricing","url":"https://www.langchain.com/pricing"},{"title":"LangGraph Pricing Explained - Integration & Maintenance Costs | MetaCTO","url":"https://www.metacto.com/blogs/langgraph-pricing-explained-a-deep-dive-into-integration-maintenance-costs"},{"title":"LangChain vs LangGraph: Performance, Cost & ROI (2026 Guide)","url":"https://www.alphabold.com/langchain-vs-langgraph/"},{"title":"LangGraph: Agent Orchestration Framework for Reliable AI Agents","url":"https://www.langchain.com/langgraph"},{"title":"LangSmith Pricing 2026: Complete Cost Breakdown & Integration Guide | metacto","url":"https://www.metacto.com/blogs/the-true-cost-of-langsmith-a-comprehensive-pricing-integration-guide"},{"title":"LangGraph Reviews 2026: Pricing, Features & More","url":"https://www.selecthub.com/p/ai-agent-orchestration-platforms/langgraph/"},{"title":"LangSmith Pricing 2026 — Plans, Limits, and Alternatives | MarginDash","url":"https://margindash.com/langsmith-pricing"},{"title":"Agent Platform Pricing Compared 2026 | Awesome Agents","url":"https://awesomeagents.ai/pricing/agent-platform-pricing/"}]

Based on the search results, here's comprehensive information about LangGraph Platform pricing and features in 2026:

## Pricing Tiers

LangGraph's pricing is structured across three main tiers: Developer, Plus, and Enterprise.

**Developer Plan**: The Developer tier is free with 5,000 traces per month, 14-day retention, one seat, basic evaluations, and community support.

**Plus Plan**: Plus plan costs $39/month for managed deployment and includes 100,000 LangGraph cloud calls per month. Additionally, the cost is $0.001 per node executed plus a small per-minute charge for standby time, with standby minutes ...[truncated]
```
</details>

**[11] tool→WebSearch:** `{'query': 'Anthropic Message Batches API 50% discount limits polling 2026'}`

**[12] tool→WebSearch:** `{'query': 'OpenAI Batch API 50% discount 24 hour window limits'}`

<details><summary>[13] tool result (2608 chars)</summary>

```
Web search results for query: "OpenAI Batch API 50% discount 24 hour window limits"

Links: [{"title":"OpenAI Batch API 2026: 50% Off Every Model, 24-Hour Guide - TokenMix Blog","url":"https://tokenmix.ai/blog/openai-batch-api-pricing"},{"title":"Batch API is now available - Announcements - OpenAI Developer Community","url":"https://community.openai.com/t/batch-api-is-now-available/718416"},{"title":"LLM Batch API Pricing: When 50% Discounts Make Sense (and When They Don't)","url":"https://simplifai.tools/blog/llm-batch-api-pricing"},{"title":"OpenAI API Pricing | OpenAI","url":"https://openai.com/api/pricing/"},{"title":"Batch API | OpenAI API","url":"https://developers.openai.com/api/docs/guides/batch"},{"title":"Batch API FAQ | OpenAI Help Center","url":"https://help.openai.com/en/articles/9197833-batch-api-faq"},{"title":"Save 50% on OpenAI: Complete Guide to Batch Requests","url":"https://www.youtube.com/watch?v=YVjr4HprklU"},{"title":"Complete Guide to OpenAI and Gemini Batch API: 50% Cost-Saving Solutions and Official Top-up Access (2026) - Apiyi.com Blog","url":"https://help.apiyi.com/en/openai-gemini-batch-api-official-topup-guide-2026-en.html"},{"title":"OpenAI Batch API: 50% Cost Reduction Tutorial for Bulk Jobs - TeachMeIDEA","url":"https://teachmeidea.com/openai-batch-api/"},{"title":"50% discount on OpenAI pricing if you submit a batch and give them up to 24h | Hacker News","url":"https://news.ycombinator.com/item?id=40043845"}]

Based on the search results, here's the information about OpenAI's Batch API 50% discount and 24-hour window limits:

## 50% Discount

The Batch API gives a flat 50% discount on every OpenAI model in exchange for a 24-hour completion window. The discount applies to both input and output tokens.

## 24-Hour Window

OpenAI guarantees completion within 24 hours, but most batches finish in 1-6 hours depending on size and current system load. There is no way to request faster processing — the 24-hour window is the only option. Addi...[truncated]
```
</details>

<details><summary>[14] tool result (2982 chars)</summary>

```
Web search results for query: "Anthropic Message Batches API 50% discount limits polling 2026"

Links: [{"title":"Anthropic API Pricing in 2026: Complete Guide — Models, Caching, Batch & Optimization","url":"https://www.finout.io/blog/anthropic-api-pricing"},{"title":"Anthropic batch API: process thousands of prompts at 50% cost","url":"https://www.codewords.ai/blog/anthropic-batch-api"},{"title":"Using Anthropic's Message Batches API with Temporal | Steve Kinney","url":"https://stevekinney.com/writing/anthropic-batch-api-with-temporal"},{"title":"Anthropic Message Batches API — Cut LLM Costs 50% at Scale","url":"https://jangwook.net/en/blog/en/anthropic-message-batches-api-production-guide/"},{"title":"When and How to Use the Anthropic Batch API in Your Agent - DEV Community","url":"https://dev.to/mukundakatta/when-and-how-to-use-the-anthropic-batch-api-in-your-agent-5fgn"},{"title":"Anthropic API Pricing 2026: Official Token Rates for Every Claude Model","url":"https://pecollective.com/tools/anthropic-api-pricing/"},{"title":"Batch processing - Claude API Docs","url":"https://platform.claude.com/docs/en/build-with-claude/batch-processing"},{"title":"Anthropic API Pricing 2026: Every Model, Compared | Calcis","url":"https://www.calcis.dev/pricing/anthropic"},{"title":"Batch Processing Pool: 50% Cost Reduction Opportunity · cline/cline · Discussion #1326","url":"https://github.com/cline/cline/discussions/1326"},{"title":"Anthropic Batch API in Production: 50% Cost Reduction Through Smart API Architecture - Dotzlaw Consulting","url":"https://www.dotzlaw.com/insights/obsidian-notes-02/"}]

Based on the search results, here's what I found about the Anthropic Message Batches API regarding the 50% discount, limits, and polling:

## 50% Discount

The Anthropic Message Batches API processes requests asynchronously and returns results within 24 hours, at exactly 50% off standard token prices. Batch processing delivers a guaranteed 50% discount on both input and output token...[truncated]
```
</details>

**[15] tool→WebSearch:** `{'query': 'OpenTelemetry GenAI semantic conventions 2026 status gen_ai spans token usage'}`

**[16] tool→WebSearch:** `{'query': 'Langfuse vs LangSmith 2026 self-hosted LLM observability cost tracking'}`

<details><summary>[17] tool result (3190 chars)</summary>

```
Web search results for query: "OpenTelemetry GenAI semantic conventions 2026 status gen_ai spans token usage"

Links: [{"title":"Semantic conventions for generative AI systems | OpenTelemetry","url":"https://opentelemetry.io/docs/specs/semconv/gen-ai/"},{"title":"Semantic conventions for generative client AI spans | OpenTelemetry","url":"https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/"},{"title":"Semantic Conventions for GenAI agent and framework spans | OpenTelemetry","url":"https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/"},{"title":"Semantic conventions for Generative AI events | OpenTelemetry","url":"https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/"},{"title":"Inside the LLM Call: GenAI Observability with OpenTelemetry | OpenTelemetry","url":"https://opentelemetry.io/blog/2026/genai-observability/"},{"title":"semantic-conventions/docs/gen-ai/gen-ai-spans.md at main · open-telemetry/semantic-conventions","url":"https://github.com/open-telemetry/semantic-conventions/blob/main/docs/gen-ai/gen-ai-spans.md"},{"title":"Semantic conventions for generative AI metrics | OpenTelemetry","url":"https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/"},{"title":"OpenTelemetry GenAI Semantic Conventions - The Standard for LLM Observability - DEV Community","url":"https://dev.to/x4nent/opentelemetry-genai-semantic-conventions-the-standard-for-llm-observability-1o2a"},{"title":"How OpenTelemetry Traces LLM Calls, Agent Reasoning, and MCP Tools | Greptime","url":"https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions"},{"title":"OpenTelemetry GenAI: Tracing AI Agents Without Leaking PII - maketocreate.com","url":"https://maketocreate.com/opentelemetry-genai-tracing-ai-agents-without-leaking-pii/"}]

Based on the search results, here's what I found about OpenTelemetry GenAI semantic conventions, gen_ai spans, and token usage in 2026:

## Current Status

The GenAI semantic conventions are cu...[truncated]
```
</details>

<details><summary>[18] tool result (4648 chars)</summary>

```
Web search results for query: "Langfuse vs LangSmith 2026 self-hosted LLM observability cost tracking"

Links: [{"title":"LangSmith Alternative? Langfuse vs. LangSmith for LLM Observability - Langfuse","url":"https://langfuse.com/faq/all/langsmith-alternative"},{"title":"Langfuse vs LangSmith: An Independent Verdict | TECHSY","url":"https://techsy.io/en/blog/langfuse-vs-langsmith"},{"title":"Langfuse alternatives: Top 5 competitors compared (2026) - Articles - Braintrust","url":"https://www.braintrust.dev/articles/langfuse-alternatives-2026"},{"title":"Langfuse Alternatives 2026: 7 Top Picks for Agent Observability | Laminar","url":"https://laminar.sh/article/langfuse-alternatives-2026"},{"title":"LLM Observability Tools Compared: LangFuse vs LangSmith vs Opik","url":"https://bigdataboutique.com/blog/llm-observability-tools-compared-langfuse-vs-langsmith-vs-opik"},{"title":"LangSmith vs Langfuse: LLM Observability Compared (2026) | MyEngineeringPath","url":"https://myengineeringpath.dev/tools/langsmith-vs-langfuse/"},{"title":"Best LLM Cost Tracking Tools in 2026","url":"https://www.getmaxim.ai/articles/best-llm-cost-tracking-tools-in-2026/"},{"title":"Top 7 LangSmith Alternatives for LLM Observability in 2026 | SigNoz","url":"https://signoz.io/comparisons/langsmith-alternatives/"},{"title":"Top 5 LLM Observability Platforms 2026: Langfuse vs LangSmith vs Helicone vs Arize vs Weights & Biases | Deepak Gupta","url":"https://guptadeepak.com/tools/top-5-llm-observability-platforms-2026/"},{"title":"Top 5 LangSmith Alternatives and Competitors, Compared (2026) - Confident AI","url":"https://www.confident-ai.com/knowledge-base/compare/top-langsmith-alternatives-and-competitors-compared"}]

Based on the 2026 search results, here's a comprehensive comparison of Langfuse vs LangSmith for self-hosted LLM observability and cost tracking:

## Self-Hosting Capabilities

Langfuse is open source (MIT) and self-hosting is a first-class citizen, while LangSmith is a proprietary, cl...[truncated]
```
</details>

**[19] tool→WebSearch:** `{'query': 'LLM-as-judge evaluation long-form document generation 2026 best practices rubric'}`

**[20] tool→WebSearch:** `{'query': 'RAGAS faithfulness citation evaluation framework 2026 deepeval'}`

<details><summary>[21] tool result (2355 chars)</summary>

```
Web search results for query: "RAGAS faithfulness citation evaluation framework 2026 deepeval"

Links: [{"title":"RAG System Metrics: Recall, Precision, Faithfulness 2026","url":"https://www.digitalapplied.com/blog/rag-system-metrics-recall-precision-faithfulness-2026"},{"title":"What is RAG Evaluation? Frameworks, Metrics, and Gates in 2026","url":"https://futureagi.com/blog/what-is-rag-evaluation-2026"},{"title":"RAGAS, TruLens, DeepEval: LLM Evaluation Frameworks (2026)","url":"https://atlan.com/know/llm-evaluation-frameworks-compared/"},{"title":"RAG Evaluation 2026: Methods, Metrics, Frameworks","url":"https://datavlab.ai/post/rag-evaluation-methods-metrics-2026-guide"},{"title":"Seeing Through the MiRAGE: Evaluating Multimodal Retrieval Augmented Generation","url":"https://arxiv.org/pdf/2510.24870"},{"title":"Qaskills","url":"https://qaskills.sh/blog/rag-evaluation-metrics-complete-2026"}]

Based on the search results, here's what I found about RAGAS faithfulness, citation evaluation, and the DeepEval framework in 2026:

## RAGAS Framework and Faithfulness

RAGAS is a RAG evaluation framework developed for automatic evaluation of retrieval-augmented generation—the task of producing long-form, citation attributed responses to a user query. Faithfulness evaluates "statements" from a prediction against the context used in generation.

## Comparison of RAGAS and DeepEval in 2026

The three production-ready RAG eval frameworks in 2026 are RAGAS, DeepEval, and Promptfoo. When it comes to faithfulness specifically, RAGAS faithfulness and DeepEval FaithfulnessEval are both production-grade, with DeepEval's implementation having slightly better claim decomposition in 2026 builds while RAGAS has more battle-testing.

## Citation Accuracy in DeepEval

DeepEval's metric library is fast-moving with citation accuracy, hallucination, contextual precision/recall, and a stronger faithfulness implementation in 2026.

## Framework Integration Patterns

RAGAS provides the concept...[truncated]
```
</details>

<details><summary>[22] tool result (3006 chars)</summary>

```
Web search results for query: "LLM-as-judge evaluation long-form document generation 2026 best practices rubric"

Links: [{"title":"Rubric-Based Evaluations & LLM-as-a-Judge — Methodologies, Biases, and Empirical Validation in Domain-Specific Contexts. | by Adnan Masood, PhD. | Apr, 2026 | Medium","url":"https://medium.com/@adnanmasood/rubric-based-evals-llm-as-a-judge-methodologies-and-empirical-validation-in-domain-context-71936b989e80"},{"title":"LLM-as-a-Judge Simply Explained: The Complete Guide to Run LLM Evals at Scale - Confident AI","url":"https://www.confident-ai.com/blog/why-llm-as-a-judge-is-the-best-llm-evaluation-method"},{"title":"SurveyEval: Towards Comprehensive Evaluation of LLM-Generated Academic Surveys","url":"https://arxiv.org/pdf/2512.02763"},{"title":"LLM-as-a-Judge - Langfuse","url":"https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge"},{"title":"AppellateGen: A Benchmark for Appellate Legal Judgment Generation","url":"https://arxiv.org/pdf/2601.01331"},{"title":"LLM-as-a-Judge Evaluation","url":"https://www.emergentmind.com/topics/llm-as-a-judge-evaluations"},{"title":"LLM-as-a-Judge: A Practical Guide | Towards Data Science","url":"https://towardsdatascience.com/llm-as-a-judge-a-practical-guide/"},{"title":"LLM-As-Judge: 7 Best Practices & Evaluation Templates","url":"https://montecarlo.ai/blog-llm-as-judge/"}]

Based on the search results, here's what the current research and best practices show about LLM-as-judge evaluation for long-form document generation in 2026:

## Current State of LLM-as-Judge

In 2026, LLM judges agree with human reviewers about 85% of the time—higher than two humans agree with each other—making LLM-as-a-Judge the default method for evaluating LLM applications at scale.

## Best Practices for Rubric Design

Well-defined rubrics and concrete examples are key to ensuring consistency and accuracy. A rubric describes what "good" looks like across different score levels, giving the LLM a stable frame...[truncated]
```
</details>

**[23] tool→WebSearch:** `{'query': 'multi-tenant SaaS LLM cost metering per-tenant token attribution architecture 2026'}`

**[24] tool→WebSearch:** `{'query': 'job queue long-running jobs hours Celery vs Temporal vs Hatchet 2026 background workers'}`

<details><summary>[25] tool result (3597 chars)</summary>

```
Web search results for query: "multi-tenant SaaS LLM cost metering per-tenant token attribution architecture 2026"

Links: [{"title":"Metering LLM Token Usage: An Architecture Guide for AI SaaS | Dodo Payments","url":"https://dodopayments.com/blogs/metering-llm-token-usage-architecture"},{"title":"Token Attribution and Cost Governance for Multi-Tenant LLM Products in Production - SoftwareSeni","url":"https://www.softwareseni.com/token-attribution-and-cost-governance-for-multi-tenant-llm-products-in-production/"},{"title":"Multi-Tenant LLM Serving on GPU Cloud: Per-Customer Isolation, Token Quotas, and Production SaaS Architecture Guide (2026) | Spheron Blog","url":"https://www.spheron.network/blog/multi-tenant-llm-serving-gpu-cloud/"},{"title":"How to track LLM costs (2026): A playbook for per-user, per-feature, and per-agent-run attribution - Articles - Braintrust","url":"https://www.braintrust.dev/articles/how-to-track-llm-costs-2026"},{"title":"Multi-Model LLM Cost Tracking: Token Usage Attribution Guide","url":"https://www.buildmvpfast.com/blog/cost-tracking-multi-model-ai-token-usage-attribution-2026"},{"title":"LLM Billing System Design (Token-based Metering Architecture) | by Rurutia1027 | Apr, 2026 | Medium","url":"https://rurutia1027.medium.com/llm-billing-system-design-token-based-metering-architecture-66147a190a79"},{"title":"Multi-Tenant Architecture with LiteLLM | liteLLM","url":"https://docs.litellm.ai/docs/proxy/multi_tenant_architecture"},{"title":"Kinde AI Token Pricing Optimization: Dynamic Cost Management for LLM-Powered SaaS","url":"https://www.kinde.com/learn/billing/billing-for-ai/ai-token-pricing-optimization-dynamic-cost-management-for-llm-powered-saas/"}]

Based on the search results, here are the key findings on multi-tenant SaaS LLM cost metering and per-tenant token attribution architecture:

## Core Architecture Principles

Multi-tenant LLM serving is the pattern of running one shared GPU-backed inference endpoint that serves multiple ex...[truncated]
```
</details>

<details><summary>[26] tool result (2928 chars)</summary>

```
Web search results for query: "job queue long-running jobs hours Celery vs Temporal vs Hatchet 2026 background workers"

Links: [{"title":"GitHub - hatchet-dev/hatchet: 🪓 An orchestration engine for background tasks, AI agents, and durable workflows","url":"https://github.com/hatchet-dev/hatchet"},{"title":"Orchestrating AI Tasks with Celery vs Temporal · Technical news about AI, coding and all","url":"https://dasroot.net/posts/2026/02/orchestrating-ai-tasks-celery-temporal/"},{"title":"Testing hatchet tasks/workflows · hatchet-dev/hatchet · Discussion #1863","url":"https://github.com/hatchet-dev/hatchet/discussions/1863"},{"title":"Agentic vs. Deterministic Orchestration: The 2026 Perspective | Frank' Wiki","url":"https://liviaerxin.github.io/blog/agentic-vs-deterministic-orchestration"},{"title":"Temporal vs Celery: Which One Should You Use?","url":"https://www.linkedin.com/pulse/temporal-vs-celery-which-one-should-you-use-vaibhav-gupta-hszwc"},{"title":"Celery vs Temporal.io Comparison - pedrobuzzi.dev","url":"https://pedrobuzzi.hashnode.dev/celery-vs-temporalio"},{"title":"Hatchet · Hatchet vs Celery","url":"https://hatchet.run/versus/hatchet-vs-celery"},{"title":"Hatchet · Hatchet vs Temporal","url":"https://hatchet.run/versus/hatchet-vs-temporal"}]

Based on the web search results, here's what I found about Celery vs Temporal vs Hatchet for long-running background jobs in 2026:

## Key Architectural Differences

Celery is ideal for fire-and-forget tasks or tasks that require quick execution, while Temporal is designed for long-running, stateful workflows. Celery uses a message broker like RabbitMQ or Redis to queue tasks, while Temporal provides a built-in workflow engine with durable execution capabilities.

## Use Cases

Celery's lightweight architecture is beneficial for projects that don't require advanced features, such as running periodic data cleaning jobs or executing batch predictions, since its lack of complex state management means it can be deploye...[truncated]
```
</details>

**[28] tool→WebSearch:** `{'query': 'LangGraph Platform background runs webhooks cron double texting stateless runs docs'}`

**[29] tool→WebSearch:** `{'query': 'DBOS vs Inngest vs Restate durable execution lightweight Postgres 2026'}`

<details><summary>[30] tool result (2654 chars)</summary>

```
Web search results for query: "LangGraph Platform background runs webhooks cron double texting stateless runs docs"

Links: [{"title":"How to kick off background runs - Docs by LangChain","url":"https://docs.langchain.com/langgraph-platform/background-run"},{"title":"Why do I need LangGraph Platform for agent deployment?","url":"https://blog.langchain.com/why-langgraph-platform/"},{"title":"GitHub - tadata-org/langchain-runner: Zero-configuration way to expose LangChain/LangGraph agents as autonomous services with webhooks, cron, and HTTP triggers. · GitHub","url":"https://github.com/tadata-org/langchain-runner"},{"title":"⚠️ Recommending LangGraph Platform for new projects · Issue #791 · langchain-ai/langserve","url":"https://github.com/langchain-ai/langserve/issues/791"},{"title":"Stateless Runs","url":"https://langchain-ai.github.io/langgraphjs/cloud/how-tos/stateless_runs/"},{"title":"Orchestrating Long-Running Processes with LangGraph Agents","url":"https://www.auxiliobits.com/blog/orchestrating-long-running-processes-using-langgraph-agents/"},{"title":"langgraph/docs/docs/cloud/how-tos/webhooks.md at main · langchain-ai/langgraph","url":"https://github.com/langchain-ai/langgraph/blob/main/docs/docs/cloud/how-tos/webhooks.md"}]

Based on the search results, I found relevant information about the LangGraph Platform features you're looking for:

## LangGraph Platform Features

LangGraph Platform provides comprehensive, out-of-the-box support for persistence, memory, double-texting handling, human-in-the-loop workflows, cron job scheduling, webhooks, high-load management, advanced streaming, support for long-running tasks, and background task processing.

### Background Runs and Webhooks
The LangGraph Server supports launching agent runs in the background and provides polling endpoints, streaming endpoints and webhooks to monitor run status effectively.

### Double-Texting Handling
Some types of bursty applications that are also stateful may let the user send mult...[truncated]
```
</details>

<details><summary>[31] tool result (3304 chars)</summary>

```
Web search results for query: "DBOS vs Inngest vs Restate durable execution lightweight Postgres 2026"

Links: [{"title":"Postgres-backed Durable Workflow Execution | DBOS","url":"https://www.dbos.dev/blog/postgres-is-all-you-need-for-durable-execution"},{"title":"Running Durable Workflows in Postgres using DBOS","url":"https://supabase.com/blog/durable-workflows-in-postgres-dbos"},{"title":"DBOS vs Temporal: Choosing Durable Execution in 2026","url":"https://www.tiarebalbi.com/en/blog/dbos-vs-temporal-postgres-durable-execution"},{"title":"DBOS | Durable Workflow Orchestration","url":"https://www.dbos.dev/"},{"title":"Durable Execution: How Temporal, Restate, and DBOS Are Rethinking Distributed State · Dev Note","url":"https://devstarsj.github.io/2026/04/03/durable-execution-temporal-restate-dbos-distributed-workflows-2026/"},{"title":"Durable Execution Patterns for AI Agents: Building Fault-Tolerant Autonomous Systems | Zylos Research","url":"https://zylos.ai/research/2026-02-17-durable-execution-ai-agents"},{"title":"DBOS, Inc. · GitHub","url":"https://github.com/dbos-inc"},{"title":"GitHub - dbos-inc/dbos-transact-py: Database-Backed Durable Python Workflows · GitHub","url":"https://github.com/dbos-inc/dbos-transact-py"},{"title":"GitHub - SokratisVidros/pg-workflows: Postgres workflows. Durable execution built on pg-boss. Like Temporal, Inngest, or Trigger, powered by Postgres. · GitHub","url":"https://github.com/SokratisVidros/pg-workflows"}]

Based on the web search results, here's a comprehensive comparison of DBOS, Inngest, and Restate for durable execution with lightweight Postgres in 2026:

## Architecture & Operational Approach

DBOS is a library that you import into your service with a Postgres connection string, with no separate process to deploy, no history cluster, and no task queue daemon. It runs fully in-process as a library with zero new infrastructure required.

Restate is elegant and simpler to operate, while DBOS is intriguing for teams who wa...[truncated]
```
</details>

**[33] tool→WebFetch:** `{'url': 'https://platform.claude.com/docs/en/build-with-claude/batch-processing', 'prompt': 'What are the exact limits of the Message Batches API: max`

<details><summary>[34] tool result (2203 chars)</summary>

```
<persisted-output>
Output too large (73KB). Full output saved to: /Users/cyan/.claude/projects/-Users-cyan-ccr-know-expand/47800223-f745-425a-9b17-04407d177a8d/tool-results/toolu_01JvMJGc371d3vrpDYENw9w1.txt

Preview (first 2KB):
# Batch processing

---

Batch processing is a powerful approach for handling large volumes of requests efficiently. Instead of processing requests one at a time with immediate responses, batch processing allows you to submit multiple requests together for asynchronous processing. This pattern is particularly useful when:

- You need to process large volumes of data
- Immediate responses are not required
- You want to optimize for cost efficiency
- You're running large-scale evaluations or analyses

The Message Batches API is Anthropic's first implementation of this pattern.

<Note>
This feature is **not** eligible for [Zero Data Retention (ZDR)](/docs/en/build-with-claude/api-and-data-retention). Data is retained according to the feature's standard retention policy.
</Note>

---

# Message Batches API

The Message Batches API is a powerful, cost-effective way to asynchronously process large volumes of [Messages](/docs/en/api/messages/create) requests. This approach is well-suited to tasks that do not require immediate responses, with most batches finishing in less than 1 hour while reducing costs by 50% and increasing throughput.

You can [explore the API reference directly](/docs/en/api/creating-message-batches), in addition to this guide.

## How the Message Batches API works

When you send a request to the Message Batches API:

1. The system creates a new Message Batch with the provided Messages requests.
2. The batch is then processed asynchronously, with each request handled independently.
3. You can poll for the status of the batch and retrieve results when processing has ended for all requests.

This is especially useful for bulk operations that don't require immediate results, such as:
- Large-scale evaluations: Process thousands o...[truncated]
```
</details>

**[35] tool→WebSearch:** `{'query': 'Temporal Cloud pricing actions per month 2026 cost'}`

<details><summary>[36] tool result (2549 chars)</summary>

```
Web search results for query: "Temporal Cloud pricing actions per month 2026 cost"

Links: [{"title":"Temporal Platform Pricing Options | Temporal","url":"https://temporal.io/pricing"},{"title":"Temporal Cloud pricing | Temporal Platform Documentation","url":"https://docs.temporal.io/cloud/pricing"},{"title":"Temporal Pricing 2026: Free Self-Host, Cloud From $100/mo | Automation Atlas","url":"https://automationatlas.io/answers/temporal-pricing-explained-2026/"},{"title":"Temporal Cloud Pricing Update | Temporal","url":"https://temporal.io/blog/temporal-cloud-pricing-update"},{"title":"Temporal Pricing 2026: Plans, Costs & Hidden Fees - Temporal | CheckThat.ai","url":"https://checkthat.ai/brands/temporal/pricing"},{"title":"Temporal Pricing Teardown 2026 - DEV Community","url":"https://dev.to/beton/temporal-pricing-teardown-2026-2j11"},{"title":"Temporal Cloud Actions | Temporal Platform Documentation","url":"https://docs.temporal.io/cloud/actions"},{"title":"Temporal Cloud Pricing Information","url":"https://web.temporal.io/pricing"},{"title":"Temporal Pricing & Plans 2026 | AI Tools Atlas","url":"https://aitoolsatlas.ai/tools/temporal/pricing"},{"title":"AWS Marketplace: Temporal Cloud (Pay-as-you-Go)","url":"https://aws.amazon.com/marketplace/pp/prodview-xx2x66m6fp2lo"}]

Based on the search results, here's the current Temporal Cloud pricing information for 2026 regarding actions per month costs:

## Actions Pricing (Pay-as-You-Go)

Actions pricing starts at $50 per million Actions ($0.00005 per Action). You gain progressive volume discounts as you scale.

More specifically, actions pricing starts at $50 per million Actions for the first 5 million (previously $25), with pricing decreasing at higher volumes. Discounts start at 5 million Actions (down from 300 million) and apply across all namespaces in an account.

## Cloud Plans (with Bundled Actions)

For those preferring predictable pricing, as of May 2026 the Essentials plan is from $100/month and includes 1 mi...[truncated]
```
</details>

**[38] tool→Bash:** `grep -n -iE "100,000|100000|256 MB|256MB|24 hour|29 days|limit|expire|prompt caching|poll" "/Users/cyan/.claude/projects` — Extract batch API limits from saved docs page

<details><summary>[39] tool result (3611 chars)</summary>

```
32:3. You can poll for the status of the batch and retrieve results when processing has ended for all requests.
40:### Batch limitations
41:- A Message Batch is limited to either 100,000 Message requests or 256 MB in size, whichever is reached first.
42:- The system processes each batch as fast as possible, with most batches completing within 1 hour. You can access batch results when all messages have completed or after 24 hours, whichever comes first. Batches expire if processing does not complete within 24 hours.
43:- Batch results are available for 29 days after creation. After that, you may still view the Batch, but its results will no longer be available for download.
45:- Rate limits apply to both Batches API HTTP requests and the number of requests within a batch waiting to be processed. See [Message Batches API rate limits](/docs/en/api/rate-limits#message-batches-api). Additionally, processing may be slowed down based on current demand and your request volume. In that case, you may see more requests expiring after 24 hours.
46:- Due to high throughput and concurrent processing, batches may go slightly over your Workspace's configured [spend limit](/settings/limits).
47:- Each batched request must have `max_tokens` of at least `1`. `max_tokens: 0` ([cache pre-warming](/docs/en/build-with-claude/prompt-caching#pre-warming-the-cache)) is not supported inside a batch, since an ephemeral cache entry written during batch processing would likely expire before the follow-up request runs.
73:| `max_tokens: 0` | See [Batch limitations](#batch-limitations). |
77:Since batches can take longer than 5 minutes to process, consider using the [1-hour cache duration](/docs/en/build-with-claude/prompt-caching#1-hour-cache-duration) with prompt caching for better cache hit rates when processing batches with shared context.
88:| Claude Mythos 5 ([limited availability](https://anthropic.com/glasswing)) | $5 / MTok        | $25 / MTok      |
461:    "expired": 0
465:  "expires_at...[truncated]
```
</details>

**[41] tool→WebSearch:** `{'query': 'LangGraph interrupt Command resume human-in-the-loop checkpointer Postgres production'}`

**[42] tool→WebSearch:** `{'query': 'LiteLLM proxy per-key budgets spend tracking virtual keys 2026'}`

<details><summary>[43] tool result (3146 chars)</summary>

```
Web search results for query: "LiteLLM proxy per-key budgets spend tracking virtual keys 2026"

Links: [{"title":"Virtual Keys | liteLLM","url":"https://docs.litellm.ai/docs/proxy/virtual_keys"},{"title":"Budgets, Rate Limits | liteLLM","url":"https://docs.litellm.ai/docs/proxy/users"},{"title":"Spend Tracking | liteLLM","url":"https://docs.litellm.ai/docs/proxy/cost_tracking"},{"title":"LiteLLM Virtual Keys Best Practices: Secure, Scalable API Management for Your LLM Proxy in 2026 - Success Knocks | The Business Magazine","url":"https://successknocks.com/litellm-virtual-keys-best-practices-secure/"},{"title":"LiteLLM: One API Gateway to Control 100+ LLM Providers (Virtual Keys, Routing, and Guardrails)","url":"https://blog.elest.io/litellm-stop-burning-money-on-llm-apis-virtual-keys-cost-tracking-and-guardrails/"},{"title":"LiteLLM Proxy Production Tutorial: LLM Gateway in 2026 | Nerd Level Tech","url":"https://nerdleveltech.com/litellm-proxy-production-llm-gateway-tutorial"},{"title":"LiteLLM AI Gateway (LLM Proxy) | liteLLM","url":"https://docs.litellm.ai/docs/simple_proxy"},{"title":"Setting Tag Budgets | liteLLM","url":"https://docs.litellm.ai/docs/proxy/tag_budgets"},{"title":"Getting Started | liteLLM","url":"https://docs.litellm.ai/docs/"},{"title":"✨ Budget / Rate Limit Tiers | liteLLM","url":"https://docs.litellm.ai/docs/proxy/rate_limit_tiers"}]

Based on the search results, here's comprehensive information about LiteLLM proxy's per-key budgets and spend tracking capabilities:

## Virtual Keys and Spend Tracking

Spend is automatically tracked for the key in the "LiteLLM_VerificationTokenTable". LiteLLM tracks spend for keys, users, and teams across 100+ LLMs, and automatically tracks spend for all known models.

## Per-Key Budget Configuration

You can set multiple budget windows on virtual keys through the /key/generate endpoint or the Admin UI by adding budget windows with different periods and spend caps. Model-specific budgets can be applied to a key...[truncated]
```
</details>

<details><summary>[44] tool result (3878 chars)</summary>

```
Web search results for query: "LangGraph interrupt Command resume human-in-the-loop checkpointer Postgres production"

Links: [{"title":"Interrupts - Docs by LangChain","url":"https://docs.langchain.com/oss/python/langgraph/interrupts"},{"title":"Interrupts and Commands in LangGraph: Building Human-in-the-Loop Workflows - DEV Community","url":"https://dev.to/jamesbmour/interrupts-and-commands-in-langgraph-building-human-in-the-loop-workflows-4ngl"},{"title":"LangGraph 201: Adding Human Oversight to Your Deep Research Agent | Towards Data Science","url":"https://towardsdatascience.com/langgraph-201-adding-human-oversight-to-your-deep-research-agent/"},{"title":"LangGraph Persistence Guide: Checkpointers & State (2026) | Fastio","url":"https://fast.io/resources/langgraph-persistence/"},{"title":"Architecting Human-in-the-Loop Agents: Interrupts, Persistence, and State Management in LangGraph","url":"https://medium.com/data-science-collective/architecting-human-in-the-loop-agents-interrupts-persistence-and-state-management-in-langgraph-fa36c9663d6f"},{"title":"Beyond input(): Building Production-Ready Human-in-the-Loop AI Agents with LangGraph - DEV Community","url":"https://dev.to/sreeni5018/beyond-input-building-production-ready-human-in-the-loop-ai-with-langgraph-2en9"},{"title":"Human-in-the-Loop Workflows with LangGraph: Interrupts, Approvals, and Async Execution","url":"https://www.abstractalgorithms.dev/langgraph-human-in-the-loop"},{"title":"Human-in-the-loop Resume Not Working in LangGraph JS with Checkpoint · Issue #1308 · langchain-ai/langgraphjs","url":"https://github.com/langchain-ai/langgraphjs/issues/1308"},{"title":"🧠 LangGraph Breakpoints or Interrupt: How to Add Human-in-the-Loop Control to Your AI Workflows | by Sangeethasaravanan | Medium","url":"https://sangeethasaravanan.medium.com/build-llm-workflows-with-langgraph-breakpoints-and-interrupts-for-human-in-the-loop-control-bb311ce681c3"}]

Based on the search results, here's comprehensive informati...[truncated]
```
</details>

**[46] assistant:** All research areas are covered. Compiling the findings into the structured output.

## Structured Output
```json
{
  "summary": "Current (June 2026) best practice for productionizing 1-3 hour multi-agent LangGraph pipelines converges on five layers: (1) durable execution \u2014 either LangGraph Platform (managed queues, Postgres checkpointing, background runs, webhooks, HITL interrupts at $39/mo + $0.001/node) or a durable-execution engine (Temporal for scale, DBOS/Hatchet for minimal-footprint Postgres-backed durability; the 2026 consensus is that most teams should start with the lightweight option and only adopt Temporal when they hit real scale); (2) interrupt-based human-in-the-loop with a Postgres checkpointer so paused runs survive process death, with idempotency required for any pre-interrupt side effects; (3) per-run/per-tenant cost metering via a proxy pattern (LiteLLM virtual keys with budgets and tag-based attribution is the de facto standard); (4) observability via Langfuse (MIT, self-hostable, ~1/3 the cost of LangSmith at volume) emitting OpenTelemetry GenAI semantic-convention attributes (still Development status \u2014 pin versions); (5) cost reduction via batch APIs \u2014 Anthropic and OpenAI both give a flat 50% discount with a 24h ceiling, and batch + prompt-caching discounts stack. Evals for long-form generated documents are now standard practice: versioned LLM-as-judge rubrics (with a human reference document to fight score saturation on long surveys) plus RAGAS/DeepEval faithfulness and citation-accuracy metrics in CI. A minimal production architecture for know-expand: keep the LangGraph pipeline self-hosted; swap pipeline.json-only resume for a real SQLite/Postgres checkpointer; replace file-based Q&A IPC with interrupt()/Command(resume=); route all LLM calls through a LiteLLM proxy with one virtual key per run for $-per-run accounting and hard budget caps; self-host Langfuse and map the existing emit()/events.jsonl to OTel gen_ai spans; ship --batch-mode on the Anthropic Batches API with 1-hour prompt-cache TTL and the planned s5_batch_poll event; add a DeepEval citation-faithfulness eval gate over S8 outputs. No Temporal Cloud or LangGraph Cloud spend is justified at single-user scale, but the architecture should keep stage boundaries activity-shaped so a later move to Hatchet/DBOS/Temporal is mechanical.",
  "key_facts": [
    {
      "fact": "LangGraph Platform Plus tier: $39/month including 100k node executions, then $0.001 per node executed plus standby charges of $0.0007/min (dev) and $0.0036/min (prod). Provides hosted execution, built-in Postgres checkpointing, task queues, LangGraph Studio debugging, streaming, and webhooks. Developer tier is free with 5k traces/month.",
      "source_url": "https://www.zenml.io/blog/langgraph-pricing"
    },
    {
      "fact": "LangGraph Platform's stated value proposition is out-of-the-box persistence, memory, double-texting handling, human-in-the-loop, cron scheduling, webhooks, background runs with polling/streaming/webhook monitoring, and support for long-running tasks \u2014 exactly the glue code know-expand currently hand-rolls in observe.py.",
      "source_url": "https://blog.langchain.com/why-langgraph-platform/"
    },
    {
      "fact": "LangGraph's interrupt() pauses graph execution and saves state via the checkpointer; Command(resume=...) resumes it. PostgresSaver persists checkpoints across restarts and long pauses (MemorySaver is dev-only; SqliteSaver suits single-process deployments). The thread_id is the persistent cursor for resuming. Critical caveat: any logic before interrupt() (paid API calls, writes) must be idempotent because it re-runs on resume.",
      "source_url": "https://docs.langchain.com/oss/python/langgraph/interrupts"
    },
    {
      "fact": "Temporal pattern for LLM apps: workflows are the deterministic orchestration layer, LLM calls and tool executions run as Activities with automatic retries and recovery. Replay 2026 added Serverless Workers, Standalone Activities, Workflow Streams, and Google ADK / OpenAI Agents SDK integrations.",
      "source_url": "https://temporal.io/blog/replay-2026-product-announcements"
    },
    {
      "fact": "Temporal Cloud pricing (2026): $50 per million Actions pay-as-you-go; Essentials plan from $100/month (1M actions, 99.9% SLA), Business from $500/month. Self-hosting is free but requires operating the Temporal server cluster.",
      "source_url": "https://docs.temporal.io/cloud/pricing"
    },
    {
      "fact": "DBOS provides durable execution as an in-process Python library backed only by Postgres \u2014 no separate orchestrator, history cluster, or queue daemon. The 2026 consensus: most backend services can ship durable execution with DBOS and move to Temporal only if they hit real scale walls; DBOS fits when side effects land in your own Postgres and throughput is a few thousand state transitions/sec.",
      "source_url": "https://www.dbos.dev/blog/postgres-is-all-you-need-for-durable-execution"
    },
    {
      "fact": "Hatchet is an open-source orchestration engine whose durable tasks are positioned as a drop-in replacement for Temporal/DBOS workflows; teams choose it over Celery for native asyncio support \u2014 a significant Celery gap given long-lived LLM API calls. 2026 industry practice is to replace Celery entirely for multi-step workflows with Temporal or Hatchet.",
      "source_url": "https://github.com/hatchet-dev/hatchet"
    },
    {
      "fact": "Anthropic Message Batches API: 50% off input and output tokens on all models; a batch is limited to 100,000 requests or 256 MB; most batches finish under 1 hour with a hard 24-hour expiry ('expired' results are not billed); results downloadable for 29 days; no completion callback \u2014 you poll batch status (30s intervals are standard practice); max_tokens:0 cache pre-warming is unsupported in batches; docs recommend the 1-hour prompt-cache duration (not 5-min ephemeral) for shared context across batched requests; not ZDR-eligible.",
      "source_url": "https://platform.claude.com/docs/en/build-with-claude/batch-processing"
    },
    {
      "fact": "OpenAI Batch API: flat 50% discount on all models, 24h completion window (not configurable), up to 50,000 requests per batch file, 2,000 batches/hour creation limit. Prompt caching and batch discounts stack \u2014 cached input on GPT-5.4 drops to $0.625/M, 75% off the standard $2.50 rate.",
      "source_url": "https://developers.openai.com/api/docs/guides/batch"
    },
    {
      "fact": "OpenTelemetry GenAI semantic conventions remain in Development status as of May 2026 with no stabilization timeline. Key attributes: gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.usage.cache_read.input_tokens (included within input_tokens), plus the gen_ai.client.token.usage metric. OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental controls version transitions \u2014 pin conventions because attribute names may still change.",
      "source_url": "https://opentelemetry.io/docs/specs/semconv/gen-ai/"
    },
    {
      "fact": "Langfuse is MIT-licensed with first-class self-hosting (Docker Compose in ~30 min); LangSmith self-hosting requires an Enterprise license. At 1M traces/month: Langfuse Cloud ~$919 vs LangSmith ~$2,500+; self-hosted Langfuse ~$150/month infra. Langfuse auto-computes per-call cost from model + token counts including cached-token usage types, with custom model pricing for self-hosted models.",
      "source_url": "https://langfuse.com/faq/all/langsmith-alternative"
    },
    {
      "fact": "LangSmith pricing: free Developer tier 5k traces/month, Plus at $39/seat/month with 10k base traces, overage $2.50/1k traces (14-day retention) or $5/1k (400-day). Best fit only for all-in LangChain/LangGraph shops wanting managed SaaS.",
      "source_url": "https://www.langchain.com/pricing"
    },
    {
      "fact": "LiteLLM proxy is the de facto per-tenant metering layer: virtual keys are independently revocable with their own budgets (multiple budget windows, model-specific bud
```

<details><summary>[48] tool result (39 chars)</summary>

```
Structured output provided successfully
```
</details>

**[49] assistant:** Research complete. Findings returned via structured output: 18 sourced facts covering LangGraph Platform pricing and capabilities, Temporal/DBOS/Hatchet durable execution trade-offs, Anthropic/OpenAI batch API limits and discounts, OTel GenAI convention status, Langfuse vs LangSmith economics, LiteLLM per-key metering, and long-form eval practice — plus 8 know-expand-specific implications recommending a checkpointer-based resume path, interrupt()-based HITL, LiteLLM proxy metering, self-hosted Langfuse, and the confirmed-viable --batch-mode design with 1-hour cache TTL.
