# know-expand audit (2026-06-10)

56-agent workflow run. Full methodology, findings, and strategic recommendations in [`REPORT.md`](REPORT.md).

## Contents

| Path | What's here |
|---|---|
| [`REPORT.md`](REPORT.md) | Full synthesized report — all four layers, market research, business case, production roadmap |
| [`agents/`](agents/) | 56 individual agent transcripts — prompt, tool trace, structured output |
| [`findings/`](findings/) | Parsed JSON outputs per subsystem (code analysts, researchers, critics) |
| [`raw/workflow_script.js`](raw/workflow_script.js) | The LangGraph-style workflow script that orchestrated all 56 agents |
| [`raw/task_output.json`](raw/task_output.json) | Full raw workflow result (all findings, all verdicts) |

## Agent breakdown

| Prefix | Count | Role |
|---|---|---|
| `code__*` | 6 | Subsystem code analysts (orchestration, llm-routing, stages-early, stages-late, webui, quality-infra) |
| `verify__*` | 43 | Adversarial verifiers — each tried to *refute* one finding; one succeeded (`/api/section` path-traversal) |
| `research__*` | 4 | Web researchers (market, prod-patterns, business, quality-risk) |
| `critic__*` | 2 | Strategy critics (bear/bull, completeness) |
| `workflow__journal` | 1 | Workflow journal / internal state |

## Key verdict

The verification claim (`"grounds every claim in verifiable, real citations"`) is false today — S8 is regex key-membership (level 1 of a 6-level ladder). Two confirmed bugs make it worse: S7 cites from an empty bibliography on fresh runs; S6 injects phantom citation keys. Both are fixable in an afternoon. The strategic opportunity is real (pharma SLR, $141k/67-weeks per review, incumbents plateau at 65% citation quality) but requires the verification machinery to actually work first.
