"""Model checker agent: run TLC on a spec and parse the results."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from shared import config
from shared.schemas import SpecDraft, TLCResult


def _parse_tlc_output(output: str) -> TLCResult:
    """Extract violation info, counterexample states, and statistics from TLC stdout."""
    result = TLCResult(raw_output=output)

    # States generated / explored
    m = re.search(r"(\d+)\s+states generated", output)
    if m:
        result.states_generated = int(m.group(1))
    m = re.search(r"(\d+)\s+distinct states found", output)
    if m:
        result.states_explored = int(m.group(1))

    # Invariant violation — "Invariant Foo is violated."
    m = re.search(r"Invariant\s+(\S+)\s+is violated", output)
    if m:
        result.violated = True
        result.violated_invariant = m.group(1)

    # Generic "Error:" (parse errors, assertion failures, etc.)
    # But skip lines like "Error: TLC Version warning" which are informational.
    _skip_patterns = (
        "error when trying to read",  # config not found — tool error, not invariant
    )
    if not result.violated:
        for line in output.splitlines():
            if line.startswith("Error:") and not any(p in line.lower() for p in _skip_patterns):
                result.violated = True
                result.violated_invariant = line.strip()
                break

    # Parse state trace.  TLC format:
    #   State 1: <Initial predicate>
    #   var1 = value1
    #   var2 = value2
    #
    #   State 2: <ActionName line N ...>
    #   var1 = value1
    states: list[dict] = []
    current: dict | None = None
    for line in output.splitlines():
        # State header
        sm = re.match(r"^State\s+(\d+):\s*(.*)$", line)
        if sm:
            if current is not None:
                states.append(current)
            current = {"state": int(sm.group(1)), "action": sm.group(2).strip(), "vars": {}}
            continue
        # Variable assignment on its own line: "varname = value"
        if current is not None:
            am = re.match(r"^(\w+)\s*=\s*(.+)$", line.strip())
            if am:
                current["vars"][am.group(1)] = am.group(2).strip()
    if current is not None:
        states.append(current)
    result.counterexample = states

    return result


def run_tlc(
    draft: SpecDraft,
    output_dir: Path,
    timeout: int = config.TLC_TIMEOUT_SECONDS,
    workers: str = config.TLC_WORKERS,
    memory_mb: int = config.TLC_MEMORY_MB,
) -> TLCResult:
    """Write spec files to output_dir, run TLC, return parsed result.

    Args:
        draft: SpecDraft with mc_tla and mc_cfg populated.
        output_dir: Directory where spec files are written and TLC is run.
        timeout: Seconds before killing TLC.
        workers: TLC worker count ('auto' = #CPUs).
        memory_mb: JVM heap in MB.

    Returns:
        TLCResult with violation info and raw output.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    mc_name = "MC"
    base_name = draft.module_name

    # Write spec files
    base_path = output_dir / f"{base_name}.tla"
    mc_path = output_dir / f"{mc_name}.tla"
    cfg_path = output_dir / f"{mc_name}.cfg"

    if draft.base_tla:
        base_path.write_text(draft.base_tla)
    if draft.mc_tla:
        mc_path.write_text(draft.mc_tla)
    if draft.mc_cfg:
        cfg_path.write_text(draft.mc_cfg)

    if not cfg_path.is_file():
        return TLCResult(tool_error="No .cfg file to run TLC with")
    if not mc_path.is_file() and not base_path.is_file():
        return TLCResult(tool_error="No TLA+ spec file found")

    # Decide which spec file to pass to TLC.
    # Use just the filename — TLC resolves relative to cwd (output_dir).
    spec_name = mc_path.name if mc_path.is_file() else base_path.name
    cfg_name = cfg_path.name

    # Worker flag
    if workers == "auto":
        import os
        workers = str(os.cpu_count() or 2)

    cmd = [
        "java",
        f"-Xmx{memory_mb}m",
        "-jar", config.TLC_JAR,
        spec_name,
        "-config", cfg_name,
        "-workers", workers,
        "-noGenerateSpecTE",  # don't write TE.tla to avoid clutter
        "-cleanup",           # clean up states dir after run
        "-deadlock",          # disable deadlock checking (bounded specs reach terminal states normally)
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(output_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return TLCResult(tool_error=f"TLC timed out after {timeout}s")
    except FileNotFoundError as exc:
        return TLCResult(tool_error=f"TLC not found: {exc}")

    result = _parse_tlc_output(output)

    # Non-zero exit with no parsed violation → treat as tool error
    if proc.returncode not in (0, 12) and not result.violated:
        # rc 12 = model checking completed with no errors
        result.tool_error = f"TLC exited {proc.returncode}"

    return result
