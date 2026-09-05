"""End-to-end TraceProof pipeline.

Usage (programmatic):
    from harness.pipeline import run
    result = run(source_code=..., module_name="MyModule", description="...")
    print(result.summary())

Usage (CLI):
    python -m harness.pipeline examples/mutex/mutex.py
    python -m harness.pipeline --module MyMutex --desc "A simple mutex" examples/mutex/mutex.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from agents.model_checker import run_tlc
from agents.repair_loop import lint_and_repair
from agents.spec_generator import generate_spec
from shared import config
from shared.llm_client import LLMClient
from shared.schemas import PipelineResult


def run(
    source_code: str,
    module_name: str,
    description: str = "",
    output_dir: Optional[Path] = None,
    llm: Optional[LLMClient] = None,
) -> PipelineResult:
    """Run the full pipeline for a single target.

    Stages:
      1. spec_generator  — LLM writes base.tla + MC.tla + MC.cfg
      2. repair_loop     — parse-and-fix loop (up to N iterations)
      3. model_checker   — run TLC, parse results

    Args:
        source_code: The implementation source text.
        module_name: TLA+ module name (also the file stem).
        description: Optional system description injected into the prompt.
        output_dir: Where to write spec files and TLC output.
        llm: Optional shared LLMClient (created if None).

    Returns:
        PipelineResult with spec, lint, and tlc sub-results.
    """
    if llm is None:
        llm = LLMClient()

    run_id = time.strftime("%Y%m%d-%H%M%S")
    if output_dir is None:
        output_dir = config.OUTPUT_ROOT / f"{run_id}-{module_name.lower()}"

    result = PipelineResult(target_name=module_name, output_dir=output_dir)

    # ── Stage 1: Spec generation ─────────────────────────────────────────
    print(f"[traceproof] [{module_name}] Stage 1: generating TLA+ spec...")
    spec = generate_spec(
        source_code=source_code,
        module_name=module_name,
        description=description,
        llm=llm,
    )
    result.spec = spec

    if not spec.base_tla and not spec.mc_tla:
        print(f"[traceproof] [{module_name}] Stage 1 FAILED — LLM produced no spec")
        print(spec.notes)
        return result

    print(f"[traceproof] [{module_name}] Stage 1 done — base_tla: {len(spec.base_tla)} chars, mc_tla: {len(spec.mc_tla)} chars")

    # ── Stage 2: Repair loop ─────────────────────────────────────────────
    print(f"[traceproof] [{module_name}] Stage 2: lint + repair (max {config.REPAIR_MAX_ITERATIONS} iters)...")
    lint = lint_and_repair(spec, llm=llm)
    result.lint = lint

    if not lint.success:
        print(f"[traceproof] [{module_name}] Stage 2 FAILED after {lint.iterations_used} iterations")
        print(lint.error_summary())
        # Still attempt model checking with what we have
    else:
        print(f"[traceproof] [{module_name}] Stage 2 passed in {lint.iterations_used} iterations")

    # ── Stage 3: Model checking ──────────────────────────────────────────
    print(f"[traceproof] [{module_name}] Stage 3: running TLC...")
    spec_dir = output_dir / "spec"
    tlc = run_tlc(spec, output_dir=spec_dir)
    result.tlc = tlc

    if tlc.tool_error:
        print(f"[traceproof] [{module_name}] Stage 3 ERROR: {tlc.tool_error}")
    elif tlc.violated:
        print(f"[traceproof] [{module_name}] Stage 3 VIOLATION: {tlc.violated_invariant}")
    else:
        print(f"[traceproof] [{module_name}] Stage 3 CLEAN — {tlc.states_generated} states generated")

    return result


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="TraceProof: source code → TLA+ spec → TLC model checking"
    )
    parser.add_argument("source", nargs="?", help="Path to source file (or '-' for stdin)")
    parser.add_argument("--module", "-m", default="Target", help="TLA+ module name (default: Target)")
    parser.add_argument("--desc", "-d", default="", help="System description")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument("--inline", "-i", help="Inline source code instead of a file")
    args = parser.parse_args()

    if args.inline:
        source_code = args.inline
    elif args.source == "-" or args.source is None:
        source_code = sys.stdin.read()
    else:
        source_code = Path(args.source).read_text()

    output_dir = Path(args.output) if args.output else None

    result = run(
        source_code=source_code,
        module_name=args.module,
        description=args.desc,
        output_dir=output_dir,
    )
    print()
    print(result.summary())
    return 0 if (result.tlc and result.tlc.clean) else 1


if __name__ == "__main__":
    sys.exit(_cli())
