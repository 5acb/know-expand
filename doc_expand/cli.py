import argparse
import asyncio
import json
import sys
from pathlib import Path

from doc_expand.config import load_config
from doc_expand.state import emit, load_pipeline_json, new_run_id, setup_logging


def _status(state_dir: Path) -> None:
    data = load_pipeline_json(state_dir)
    emit({"event": "pipeline_status", **data})


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-expand",
        description="Expand a technical document into a research-grade knowledge document.",
    )
    parser.add_argument("input", nargs="?", help="File path, URL, or - for stdin")

    # Output / mode
    parser.add_argument("--human", action="store_true", help="Rich terminal output")
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF build")
    parser.add_argument("--renderer", choices=["xelatex", "typst"], default="xelatex")

    # Pipeline control
    parser.add_argument(
        "--depth", choices=["survey", "standard", "deep"], default="standard"
    )
    parser.add_argument("--status", action="store_true", help="Emit pipeline status and exit")
    parser.add_argument("--resume", type=str, metavar="STAGE", help="Resume from stage N")
    parser.add_argument("--stage", type=str, metavar="STAGE", help="Run only stage N then stop")
    parser.add_argument("--interactive", action="store_true", help="Run Stage 0.5 interactively")
    parser.add_argument("--user-profile", type=Path, metavar="PATH")
    parser.add_argument("--auto-taxonomy", action="store_true")
    parser.add_argument("--no-bibliography-fetch", action="store_true")

    # Paths
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument(
        "--log-dir", type=Path, default=Path("logs"),
        help="Base directory for run logs (default: logs/)",
    )

    # Concurrency overrides (defaults come from config.yaml)
    parser.add_argument("--cloud-concurrency", type=int, default=None,
                        help="Override config concurrency.cloud_default")
    parser.add_argument("--local-concurrency", type=int, default=None,
                        help="Override config concurrency.local_default")

    # Run identity
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Reuse an existing run ID (for --resume); a new UUID is generated otherwise",
    )

    args = parser.parse_args()

    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        run_id = args.run_id or new_run_id()
        log_dir = setup_logging(run_id, log_base=args.log_dir)
        print(f"logs → {log_dir}", flush=True)
        _status(state_dir)
        return

    if not args.input:
        parser.error("input is required unless --status is passed")

    run_id = args.run_id or new_run_id()
    log_dir = setup_logging(run_id, log_base=args.log_dir)
    print(f"run  {run_id}", flush=True)
    print(f"logs {log_dir}", flush=True)

    if args.input == "-":
        content = sys.stdin.read()
        tmp = state_dir / "stdin_input.txt"
        tmp.write_text(content)
        input_path = str(tmp)
    else:
        input_path = args.input

    cfg = load_config()

    if args.cloud_concurrency is not None:
        cfg.concurrency.cloud_default = args.cloud_concurrency
    if args.local_concurrency is not None:
        cfg.concurrency.local_default = args.local_concurrency

    from doc_expand.pipeline import run_pipeline

    asyncio.run(run_pipeline(
        input_path=input_path,
        state_dir=state_dir,
        output_dir=args.output_dir,
        depth=args.depth,
        cfg=cfg,
        interactive=args.interactive,
        auto_taxonomy=args.auto_taxonomy,
        resume_stage=int(args.resume) if args.resume else None,
    ))


if __name__ == "__main__":
    main()
