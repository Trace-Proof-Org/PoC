"""Core data types that flow between pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SpecStatus(str, Enum):
    PENDING = "pending"
    DRAFT = "draft"
    LINTED = "linted"
    FAILED = "failed"


@dataclass
class SpecDraft:
    """Output of the spec-generator stage."""

    # Module name (used as filename stem, e.g. "MC")
    module_name: str

    # Raw TLA+ text for the base spec
    base_tla: str = ""

    # Raw TLA+ text for the MC wrapper spec
    mc_tla: str = ""

    # Raw .cfg file contents
    mc_cfg: str = ""

    # Free-form notes the LLM produced about its own spec
    notes: str = ""

    status: SpecStatus = SpecStatus.PENDING

    # Source reference fed in (file paths or inline code)
    source_ref: str = ""


@dataclass
class LintError:
    line: int
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


@dataclass
class LintResult:
    """Output of the repair-loop stage."""

    success: bool
    errors: list[LintError] = field(default_factory=list)
    iterations_used: int = 0
    final_tla: str = ""

    def error_summary(self) -> str:
        if not self.errors:
            return ""
        return "\n".join(str(e) for e in self.errors)


@dataclass
class TLCResult:
    """Output of the model-checker stage."""

    # True = TLC found a violation; False = clean
    violated: bool = False

    # Raw stdout+stderr from TLC
    raw_output: str = ""

    # Parsed counterexample steps if violated
    counterexample: list[dict] = field(default_factory=list)

    # States generated / explored (informational)
    states_generated: int = 0
    states_explored: int = 0

    # Which invariant was violated (if any)
    violated_invariant: str = ""

    # Error from launching TLC itself (not a spec violation)
    tool_error: str = ""

    @property
    def clean(self) -> bool:
        return not self.violated and not self.tool_error

    def summary(self) -> str:
        if self.tool_error:
            return f"TLC error: {self.tool_error}"
        if self.violated:
            lines = [f"VIOLATION: {self.violated_invariant}"]
            if self.counterexample:
                lines.append(f"Counterexample ({len(self.counterexample)} states):")
                for step in self.counterexample:
                    action = step.get("action", "")
                    lines.append(f"  State {step['state']}: {action}")
                    for var, val in step.get("vars", {}).items():
                        lines.append(f"    {var} = {val}")
            return "\n".join(lines)
        return (
            f"No violations found. "
            f"States generated: {self.states_generated}, explored: {self.states_explored}"
        )


@dataclass
class PipelineResult:
    """Top-level result returned by harness/pipeline.py."""

    target_name: str = ""
    spec: Optional[SpecDraft] = None
    lint: Optional[LintResult] = None
    tlc: Optional[TLCResult] = None

    # Paths to written files
    output_dir: Optional[Path] = None

    def summary(self) -> str:
        parts: list[str] = [f"=== TraceProof — {self.target_name} ==="]
        if self.lint:
            status = "OK" if self.lint.success else f"FAILED ({len(self.lint.errors)} errors)"
            parts.append(f"Spec generation: {status} ({self.lint.iterations_used} repair iterations)")
        if self.tlc:
            parts.append(f"Model check:     {self.tlc.summary()}")
        if self.output_dir:
            parts.append(f"Output:          {self.output_dir}")
        return "\n".join(parts)
