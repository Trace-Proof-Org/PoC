"""
Tests for Phase 4 — verify & export.
Uses a scripted fake MCP client; no real tla-mcp required.
"""
import shutil, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from verify_phase import verify_run, VerifyError, McpClient


# ---------------------------------------------------------------------------
# Fake MCP client builder
# ---------------------------------------------------------------------------

class FakeMcpClient:
    """Scriptable stand-in for McpClient."""

    def __init__(self, validate_results, check_results):
        """
        validate_results: list of (pass: bool, error: str)  — consumed in order
        check_results:    list of (outcome: str, detail: str)
        """
        self._validate = list(validate_results)
        self._check    = list(check_results)
        self._tools    = {"validate": "validate_spec", "check": "check_spec"}

    def start(self):  pass
    def stop(self):   pass

    def call(self, canonical: str, arguments: dict) -> dict:
        if canonical == "validate":
            ok, err = self._validate.pop(0)
            if ok:
                return {"success": True}
            return {"success": False, "errors": [err]}
        if canonical == "check":
            outcome, detail = self._check.pop(0)
            if outcome == "pass":
                return {"status": "ok"}
            if outcome == "counterexample":
                return {"status": "counterexample", "counterexample": detail}
            if outcome == "limit":
                return {"status": "limit_reached"}
            return {"status": "error", "error": detail}
        raise RuntimeError(f"unknown tool: {canonical}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_TLA = """---- MODULE base ----
EXTENDS Naturals
VARIABLES pc
Init == pc = "init"
Next == pc' = "done"
Spec == Init /\\ [][Next]_pc
TypeOK == pc \\in {"init", "done"}
====
"""

MINIMAL_CFG = """SPECIFICATION Spec
INVARIANT TypeOK
CONSTANTS
  MaxSteps = 2
"""


def _setup_run(tmp_path: Path) -> Path:
    out = tmp_path / "poc"
    out.mkdir()
    (out / "run-config.md").write_text(
        "# Run Config\n"
        "- **Created**: 2026-01-01T00:00:00+00:00\n"
        "- **Source paths**: /tmp\n"
        "- **Docs paths**: none\n"
        "- **Output dir**: " + str(out) + "\n"
        "- **Scenario**: none — module-scoped run\n"
        "- **Provider**: local\n"
        "- **Model (strong / drafting)**: local\n"
        "- **Model (cheap / classification)**: local\n"
        "- **Self-repair cap**: 3\n"
        "\n## Notes\n"
    )
    model_dir = out / "model"
    model_dir.mkdir()
    (model_dir / "base.tla").write_text(MINIMAL_TLA)
    (model_dir / "base.cfg").write_text(MINIMAL_CFG)
    (model_dir / "generation-log.md").write_text(
        "## Generation entry — 2026-01-01\n- Modules used: mymodule\ncache-key: abc123\n"
    )
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_happy_path_pass(tmp_path):
    out = _setup_run(tmp_path)
    client = FakeMcpClient(
        validate_results=[(True, "")],
        check_results=[(("pass", ""))],
    )
    manifest = verify_run(out, client=client)
    assert (out / "export" / "base.tla").exists()
    assert (out / "export" / "base.cfg").exists()
    assert manifest.exists()
    text = manifest.read_text()
    assert "PASS" in text
    assert "0 / 3" in text


def test_counterexample_is_exported(tmp_path):
    out = _setup_run(tmp_path)
    client = FakeMcpClient(
        validate_results=[(True, "")],
        check_results=[("counterexample", "state1 → state2 violates TypeOK")],
    )
    manifest = verify_run(out, client=client)
    text = manifest.read_text()
    assert "COUNTEREXAMPLE FOUND" in text
    assert (out / "export" / "base.tla").exists()


def test_limit_reached_raises_no_export(tmp_path):
    out = _setup_run(tmp_path)
    client = FakeMcpClient(
        validate_results=[(True, "")],
        check_results=[("limit", "")],
    )
    with pytest.raises(VerifyError, match="limit_reached"):
        verify_run(out, client=client)
    assert not (out / "export").exists()


def test_syntax_fail_no_repair_cap_raises(tmp_path):
    out = _setup_run(tmp_path)
    # No LLM available (provider=local), so repair is skipped immediately
    client = FakeMcpClient(
        validate_results=[(False, "parse error: unexpected token")],
        check_results=[],
    )
    with pytest.raises(VerifyError, match="Syntax validation failed"):
        verify_run(out, client=client)
    assert not (out / "export").exists()


def test_missing_tla_raises(tmp_path):
    out = _setup_run(tmp_path)
    (out / "model" / "base.tla").unlink()
    client = FakeMcpClient([], [])
    with pytest.raises(VerifyError, match="No model/base.tla"):
        verify_run(out, client=client)


def test_manifest_has_knowledge_files(tmp_path):
    out = _setup_run(tmp_path)
    client = FakeMcpClient(
        validate_results=[(True, "")],
        check_results=[("pass", "")],
    )
    manifest = verify_run(out, client=client)
    text = manifest.read_text()
    assert "knowledge/mymodule.md" in text
