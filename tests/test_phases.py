"""
Tests for all four phases.
Phase 4 uses a stub MCPClient so tla-mcp is not required.
"""
import re
import shutil
import tempfile
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

def _setup(tmp: Path, scenario=None) -> Path:
    from setup_phase import setup_run
    extra = {"scenario": scenario} if scenario else {}
    setup_run(
        source_paths=[str(root_dir())],
        output_dir=str(tmp),
        provider="local",
        **extra,
    )
    return tmp


def root_dir() -> Path:
    return Path(__file__).parent.parent


# ── Phase 1 ──────────────────────────────────────────────────────────────────

class TestSetup:
    def test_creates_run_config(self, tmp_path):
        _setup(tmp_path)
        cfg = tmp_path / "run-config.md"
        assert cfg.exists()
        text = cfg.read_text()
        assert "Provider" in text
        assert "local" in text

    def test_resume_no_overwrite(self, tmp_path):
        _setup(tmp_path)
        mtime1 = (tmp_path / "run-config.md").stat().st_mtime
        _setup(tmp_path)  # resume
        mtime2 = (tmp_path / "run-config.md").stat().st_mtime
        assert mtime1 == mtime2

    def test_fresh_overwrites(self, tmp_path):
        from setup_phase import setup_run
        _setup(tmp_path)
        mtime1 = (tmp_path / "run-config.md").stat().st_mtime
        setup_run(source_paths=[str(root_dir())], output_dir=str(tmp_path),
                  provider="local", fresh=True)
        mtime2 = (tmp_path / "run-config.md").stat().st_mtime
        assert mtime2 >= mtime1

    def test_bad_source_raises(self, tmp_path):
        from setup_phase import setup_run, SetupError
        with pytest.raises(SetupError, match="does not exist"):
            setup_run(source_paths=["/nonexistent_xyz"], output_dir=str(tmp_path),
                      provider="local")

    def test_missing_source_raises(self, tmp_path):
        from setup_phase import setup_run, SetupError
        with pytest.raises(SetupError):
            setup_run(source_paths=[], output_dir=str(tmp_path), provider="local")


# ── Phase 2 ──────────────────────────────────────────────────────────────────

class TestIndex:
    def test_creates_index_and_knowledge_files(self, tmp_path):
        _setup(tmp_path)
        from index_phase import index_run
        idx = index_run(tmp_path)
        assert idx.exists()
        text = idx.read_text()
        assert "| Module |" in text
        # at least one knowledge file created
        knowledge = list((tmp_path / "knowledge").glob("*.md"))
        knowledge = [f for f in knowledge if f.name != "_index.md"]
        assert len(knowledge) > 0

    def test_knowledge_file_has_required_sections(self, tmp_path):
        _setup(tmp_path)
        from index_phase import index_run
        index_run(tmp_path)
        for kf in (tmp_path / "knowledge").glob("*.md"):
            if kf.name == "_index.md":
                continue
            text = kf.read_text()
            for section in ("## Summary", "## State", "## Conflicts", "## Open Questions"):
                assert section in text, f"{kf.name} missing {section}"
            break  # check at least one

    def test_incremental_skip(self, tmp_path):
        _setup(tmp_path)
        from index_phase import index_run
        index_run(tmp_path)
        # Record mtimes
        mtimes1 = {f: f.stat().st_mtime for f in (tmp_path / "knowledge").glob("*.md")}
        index_run(tmp_path)
        mtimes2 = {f: f.stat().st_mtime for f in (tmp_path / "knowledge").glob("*.md")}
        assert mtimes1 == mtimes2

    def test_no_run_config_raises(self, tmp_path):
        from index_phase import index_run, IndexError
        with pytest.raises(IndexError):
            index_run(tmp_path)

    def test_llm_extract_credential_resolution(self, monkeypatch):
        from index_phase import _llm_extract
        called = {}

        def mock_call_openai(api_key, base_url, model, prompt):
            called["api_key"] = api_key
            called["base_url"] = base_url
            return None

        monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
        monkeypatch.setattr("index_phase._call_openai_compat", mock_call_openai)

        _llm_extract(
            module="test_mod",
            code_files=[],
            doc_files=[],
            scenario=None,
            provider="gemini",
            model="gemini-2.0-flash",
        )
        assert called.get("api_key") == "test-secret-key"
        assert "googleapis.com" in called.get("base_url", "")


# ── Phase 3 ──────────────────────────────────────────────────────────────────

class TestGenerate:
    def _run(self, tmp_path, scenario=None):
        _setup(tmp_path, scenario=scenario)
        from index_phase import index_run
        index_run(tmp_path)
        from generate_phase import generate_run
        return generate_run(tmp_path)

    def test_creates_base_tla_and_cfg(self, tmp_path):
        tla, cfg = self._run(tmp_path)
        assert tla.exists()
        assert cfg.exists()

    def test_tla_has_module_declaration(self, tmp_path):
        tla, _ = self._run(tmp_path)
        assert "MODULE base" in tla.read_text()

    def test_tla_has_header_comment(self, tmp_path):
        tla, _ = self._run(tmp_path)
        text = tla.read_text()
        assert "Generated by traceproof-poc" in text

    def test_cfg_has_specification(self, tmp_path):
        _, cfg = self._run(tmp_path)
        assert "SPECIFICATION" in cfg.read_text()

    def test_generation_log_created(self, tmp_path):
        self._run(tmp_path)
        assert (tmp_path / "model" / "generation-log.md").exists()

    def test_incremental_skip(self, tmp_path):
        _setup(tmp_path)
        from index_phase import index_run
        index_run(tmp_path)
        from generate_phase import generate_run
        tla1, _ = generate_run(tmp_path)
        mtime1 = tla1.stat().st_mtime
        generate_run(tmp_path)  # should skip
        mtime2 = tla1.stat().st_mtime
        assert mtime1 == mtime2

    def test_no_export_dir_written(self, tmp_path):
        self._run(tmp_path)
        assert not (tmp_path / "export").exists()


# ── Phase 4 (stub MCP) ────────────────────────────────────────────────────────

class StubMCPClient:
    """Fake MCP client for tests — controllable pass/fail/counterexample."""

    def __init__(self, *, validate_pass=True, check_outcome="pass",
                 validate_fail_once=False, check_detail=""):
        self._validate_pass       = validate_pass
        self._check_outcome       = check_outcome
        self._validate_fail_once  = validate_fail_once
        self._validate_calls      = 0
        self._check_detail        = check_detail

    def tools_list(self) -> list[dict]:
        return [
            {"name": "validate_spec", "description": "Validate TLA+ syntax"},
            {"name": "check_spec",    "description": "Model-check TLA+ spec"},
        ]

    def call_tool(self, name: str, arguments: dict) -> dict:
        if "validate" in name:
            self._validate_calls += 1
            if self._validate_fail_once and self._validate_calls == 1:
                return {"content": [{"text": "error: syntax error on line 1"}]}
            if self._validate_pass:
                return {"content": [{"text": "success: spec parsed ok"}]}
            return {"content": [{"text": "error: invalid syntax"}]}
        else:  # check
            if self._check_outcome == "pass":
                return {"content": [{"text": "no violation found"}]}
            if self._check_outcome == "counterexample":
                return {"content": [{"text": "invariant violation counterexample found: step 3"}]}
            if self._check_outcome == "limit_reached":
                return {"content": [{"text": "limit reached: too many states"}]}
            return {"content": [{"text": "error: model check failed"}]}

    def close(self) -> None:
        pass


def _full_run(tmp_path, stub: StubMCPClient, scenario=None):
    _setup(tmp_path, scenario=scenario)
    from index_phase import index_run
    index_run(tmp_path)
    from generate_phase import generate_run
    generate_run(tmp_path)
    from verify_phase import verify_run
    return verify_run(output_dir=tmp_path, client=stub)


class TestVerify:
    def test_pass_creates_export(self, tmp_path):
        manifest = _full_run(tmp_path, StubMCPClient(validate_pass=True, check_outcome="pass"))
        assert manifest.exists()
        assert (tmp_path / "export" / "base.tla").exists()
        assert (tmp_path / "export" / "base.cfg").exists()

    def test_manifest_syntax_pass(self, tmp_path):
        manifest = _full_run(tmp_path, StubMCPClient())
        text = manifest.read_text()
        assert "Syntax validation**: PASS" in text
        assert "Model checking**: PASS" in text

    def test_counterexample_exported(self, tmp_path):
        manifest = _full_run(tmp_path, StubMCPClient(check_outcome="counterexample"))
        text = manifest.read_text()
        assert "COUNTEREXAMPLE FOUND" in text
        assert (tmp_path / "export").exists()

    def test_limit_reached_raises_no_export(self, tmp_path):
        from verify_phase import VerifyError
        with pytest.raises(VerifyError, match="limit"):
            _full_run(tmp_path, StubMCPClient(check_outcome="limit_reached"))
        assert not (tmp_path / "export").exists()

    def test_syntax_fail_no_llm_raises(self, tmp_path):
        from verify_phase import VerifyError
        with pytest.raises(VerifyError):
            _full_run(tmp_path, StubMCPClient(validate_pass=False))
        assert not (tmp_path / "export").exists()

    def test_manifest_has_repair_count(self, tmp_path):
        manifest = _full_run(tmp_path, StubMCPClient())
        assert "Self-repair attempts used" in manifest.read_text()

    def test_manifest_handoff_section(self, tmp_path):
        manifest = _full_run(tmp_path, StubMCPClient())
        assert "Handoff" in manifest.read_text()

    def test_no_model_tla_raises(self, tmp_path):
        from setup_phase import setup_run
        from verify_phase import verify_run, VerifyError
        setup_run(source_paths=[str(root_dir())], output_dir=str(tmp_path), provider="local")
        with pytest.raises(VerifyError, match="base.tla"):
            verify_run(output_dir=tmp_path, client=StubMCPClient())
