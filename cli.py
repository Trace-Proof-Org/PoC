"""
traceproof-poc CLI entry point.
Subcommands: options, run, index, generate, verify
All four phases are implemented: run, index, generate, verify.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def cmd_options(args: argparse.Namespace) -> int:
    from setup_phase import suggest_options
    suggest_options(source=args.source[0] if args.source else None)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from setup_phase import setup_run, SetupError
    try:
        path = setup_run(
            source_paths=args.source,
            docs_paths=args.docs,
            output_dir=args.out,
            provider=args.provider,
            model_strong=args.model_strong or "",
            model_cheap=args.model_cheap or "",
            scenario=args.scenario,
            self_repair_cap=args.self_repair_cap,
            notes=args.notes or "",
            fresh=args.fresh,
        )
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Setup complete — {path}")
    return 0



def cmd_index(args: argparse.Namespace) -> int:
    from index_phase import index_run, IndexError as IdxError
    try:
        index_run(output_dir=args.out)
    except IdxError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0



def cmd_generate(args: argparse.Namespace) -> int:
    from generate_phase import generate_run, GenerateError
    try:
        generate_run(output_dir=args.out)
    except GenerateError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0



def cmd_verify(args: argparse.Namespace) -> int:
    from verify_phase import verify_run, VerifyError
    try:
        verify_run(output_dir=args.out)
    except VerifyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0



def cmd_verify(args: argparse.Namespace) -> int:
    from verify_phase import verify_run, VerifyError
    try:
        verify_run(output_dir=args.out)
    except VerifyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _not_implemented(phase: str):
    def _cmd(_args: argparse.Namespace) -> int:
        print(f"error: phase '{phase}' is not yet implemented.", file=sys.stderr)
        return 1
    return _cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="traceproof-poc",
        description="Code + docs → TLA+ spec generation pipeline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── options ──────────────────────────────────────────────────────────────
    p_opts = sub.add_parser("options", help="Print provider/model options.")
    p_opts.add_argument("--source", metavar="PATH", nargs="?",
                        type=lambda v: [v] if v else None, default=None,
                        help="Optional source path to inspect.")
    p_opts.set_defaults(func=cmd_options)

    # ── run (Phase 1) ─────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Phase 1 — write run-config.md.")
    p_run.add_argument("--source", metavar="PATH", action="append", required=True,
                       help="Source file/dir to analyse (repeatable).")
    p_run.add_argument("--docs", metavar="PATH", action="append", default=[],
                       help="Extra docs root (repeatable).")
    p_run.add_argument("--out", metavar="DIR", default=".traceproof-poc",
                       help="Output directory (default: .traceproof-poc).")
    p_run.add_argument("--provider", default="anthropic",
                       help="LLM provider name (default: anthropic).")
    p_run.add_argument("--model-strong", metavar="MODEL", default="",
                       help="Model for TLA+ drafting (uses provider default if omitted).")
    p_run.add_argument("--model-cheap", metavar="MODEL", default="",
                       help="Model for classification (uses provider default if omitted).")
    p_run.add_argument("--scenario", metavar="TEXT", default=None,
                       help="Scenario to scope Phase 3 (optional).")
    p_run.add_argument("--self-repair-cap", metavar="N", type=int, default=3,
                       help="Max Phase 4 repair attempts (default: 3).")
    p_run.add_argument("--notes", metavar="TEXT", default="",
                       help="Free-text notes stored in run-config.md.")
    p_run.add_argument("--fresh", action="store_true",
                       help="Overwrite existing run-config.md.")
    p_run.set_defaults(func=cmd_run)

    # ── index (Phase 2) ──────────────────────────────────────────────────────
    p_idx = sub.add_parser("index", help="Phase 2 — walk source/docs, write knowledge/.")
    p_idx.add_argument("--out", metavar="DIR", default=".traceproof-poc",
                       help="Directory containing run-config.md (default: .traceproof-poc).")
    p_idx.set_defaults(func=cmd_index)

    # ── generate (Phase 3) ───────────────────────────────────────────────────
    p_gen = sub.add_parser("generate", help="Phase 3 — draft TLA+ model from knowledge cache.")
    p_gen.add_argument("--out", metavar="DIR", default=".traceproof-poc",
                       help="Directory containing run-config.md (default: .traceproof-poc).")
    p_gen.set_defaults(func=cmd_generate)

    # ── verify (Phase 4) ─────────────────────────────────────────────────────
    p_ver = sub.add_parser("verify", help="Phase 4 — tla-rs validate, model-check, export.")
    p_ver.add_argument("--out", metavar="DIR", default=".traceproof-poc",
                       help="Directory containing run-config.md (default: .traceproof-poc).")
    p_ver.set_defaults(func=cmd_verify)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # options subcommand: --source is passed as positional-like optional
    if args.command == "options" and args.source is None:
        args.source = None

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
