import argparse
import asyncio
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from know_expand.config import load_config
from know_expand.state import emit, load_pipeline_json, new_run_id, setup_logging


def _install_sigterm_handler() -> None:
    """Emit run_stopped and exit cleanly when SIGTERM arrives (e.g. from web UI stop)."""
    def _handler(signum, frame):
        emit({"event": "run_stopped", "reason": "sigterm"})
        sys.exit(0)
    signal.signal(signal.SIGTERM, _handler)


def _status(state_dir: Path) -> None:
    data = load_pipeline_json(state_dir)
    emit({"event": "pipeline_status", **data})


def _find_latest_state_dir(runs_dir: Path) -> Path | None:
    """Scan runs_dir for the most recently modified subdir that has state/pipeline.json."""
    if not runs_dir.exists():
        return None
    candidates = sorted(
        (d for d in runs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for run_dir in candidates:
        sd = run_dir / "state"
        if (sd / "pipeline.json").exists():
            return sd
    return None


_SUBCOMMANDS = {"tail", "serve"}


def main() -> None:
    # Pre-scan argv: if the first positional arg is not a known subcommand
    # (and not a flag), it's the pipeline input file. Extract it before
    # argparse consumes it as an invalid subcommand choice.
    raw_argv = sys.argv[1:]
    _input_file: str | None = None
    if raw_argv and not raw_argv[0].startswith("-") and raw_argv[0] not in _SUBCOMMANDS:
        _input_file = raw_argv[0]
        raw_argv = raw_argv[1:]

    parser = argparse.ArgumentParser(
        prog="know-expand",
        description="Expand a technical document into a research-grade knowledge document.",
    )
    parser.add_argument("input", nargs="?", help="File path, URL, or - for stdin")

    subparsers = parser.add_subparsers(dest="command")

    # --- tail subcommand ---
    tail_p = subparsers.add_parser("tail", help="Stream pipeline events to stdout")
    tail_p.add_argument("run_id", nargs="?", default=None, help="Run ID (default: latest)")
    tail_p.add_argument("--runs-dir", type=Path, default=Path("runs"))

    # --- serve subcommand ---
    serve_p = subparsers.add_parser("serve", help="Serve completed sections at localhost")
    serve_p.add_argument("--port", type=int, default=7842)
    serve_p.add_argument("--runs-dir", type=Path, default=Path("runs"))

    # Output / mode
    parser.add_argument("--no-pdf", action="store_true", help="Skip PDF build")

    # Pipeline control
    parser.add_argument(
        "--depth", choices=["survey", "standard", "deep"], default="standard"
    )
    parser.add_argument("--status", action="store_true", help="Emit pipeline status and exit")
    parser.add_argument("--resume", type=str, metavar="STAGE", help="Resume from stage N")
    parser.add_argument("--stage", type=str, metavar="STAGE", help="Run only stage N then stop")
    parser.add_argument("--user-profile", type=Path, metavar="PATH")
    parser.add_argument("--auto-taxonomy", action="store_true")
    parser.add_argument("--no-bibliography-fetch", action="store_true")
    parser.add_argument("--bypass-license-gate", action="store_true", help="Bypass Stage 0 restrictive license gate")
    parser.add_argument(
        "--primary-model", type=str, default=None, metavar="MODEL",
        help="Prepend MODEL to all role model lists (e.g. 'claude-sonnet-4-6')",
    )

    # Paths
    parser.add_argument(
        "--runs-dir", type=Path, default=Path("runs"),
        help="Base directory for all per-run artefacts (default: runs/)",
    )

    # Concurrency override (default comes from config.yaml)
    parser.add_argument("--concurrency", type=int, default=None,
                        help="Override config concurrency.default")

    # Run identity
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Reuse an existing run ID (for --resume); a new UUID is generated otherwise",
    )

    args = parser.parse_args(raw_argv)
    if _input_file and not args.input:
        args.input = _input_file

    # Handle subcommands first
    if args.command == "tail":
        from know_expand.observe import cmd_tail
        cmd_tail(args.run_id, args.runs_dir)
        return

    if args.command == "serve":
        from know_expand.observe import cmd_serve
        cmd_serve(args.runs_dir, port=args.port)
        return

    runs_dir: Path = args.runs_dir
    run_id = args.run_id
    if not run_id and args.resume:
        latest_sd = _find_latest_state_dir(runs_dir)
        if latest_sd:
            run_id = latest_sd.parent.name
            print(f"Resuming from latest run: {run_id}", flush=True)
    if not run_id:
        run_id = new_run_id()
    run_dir = runs_dir / run_id
    state_dir = run_dir / "state"
    output_dir = run_dir / "output"
    log_dir = run_dir / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        # If no explicit run_id, find the most recently modified run with a pipeline.json
        if not args.run_id:
            state_dir = _find_latest_state_dir(runs_dir) or state_dir
        setup_logging(run_id, log_dir=log_dir)
        print(f"logs → {log_dir}", flush=True)
        _status(state_dir)
        return

    if not args.input:
        parser.error("input is required unless --status is passed")

    setup_logging(run_id, log_dir=log_dir)
    _install_sigterm_handler()
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

    if args.concurrency is not None:
        cfg.concurrency.default = args.concurrency

    from know_expand.pipeline import run_pipeline

    try:
        asyncio.run(run_pipeline(
            input_path=input_path,
            state_dir=state_dir,
            output_dir=output_dir,
            depth=args.depth,
            cfg=cfg,
            auto_taxonomy=args.auto_taxonomy,
            primary_model=args.primary_model,
            no_pdf=args.no_pdf,
            resume_stage=int(args.resume) if args.resume else None,
            only_stage=int(args.stage) if args.stage else None,
            user_profile_path=args.user_profile,
            no_bibliography_fetch=args.no_bibliography_fetch,
            bypass_license_gate=args.bypass_license_gate,
        ))
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        emit({
            "event": "run_failed",
            "run_id": run_id,
            "error": str(e),
            "traceback": tb,
        })
        print(f"Run failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
