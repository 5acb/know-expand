import argparse
import asyncio
import json
import sys
from pathlib import Path

from doc_expand.config import load_config
from doc_expand.state import emit, load_pipeline_json


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

    # Concurrency
    parser.add_argument("--cloud-concurrency", type=int, default=8)
    parser.add_argument("--local-concurrency", type=int, default=1)

    args = parser.parse_args()

    state_dir: Path = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        _status(state_dir)
        return

    if not args.input:
        parser.error("input is required unless --status is passed")

    if args.input == "-":
        input_path = "-"
        # Write stdin to a temp file so downstream stages can reference a path
        content = sys.stdin.read()
        tmp = state_dir / "stdin_input.txt"
        tmp.write_text(content)
        input_path = str(tmp)
    else:
        input_path = args.input

    cfg = load_config()

    # Apply concurrency overrides
    cfg.concurrency.cloud_default = args.cloud_concurrency
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
