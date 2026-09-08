"""
Verify & Export
Drives tla-rs directly as a subprocess (validate_spec → check_spec),
runs a capped self-repair loop on syntax failures,
and exports to export/ only after a passing verification.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RUN_CONFIG = "run-config.md"
LOG_FILE   = "model/generation-log.md"
BASE_TLA   = "model/base.tla"
BASE_CFG   = "model/base.cfg"


# Errors
class VerifyError(Exception):
    pass


# run-config.md reader
def _parse_run_config(out: Path) -> dict[str, str]:
    cfg = out / RUN_CONFIG
    if not cfg.exists():
        raise VerifyError(f"No run-config.md under {out}. Run 'traceproof-poc run' first.")
    data: dict[str, str] = {}
    for line in cfg.read_text().splitlines():
        m = re.match(r"^-\s+\*\*(.+?)\*\*:\s*(.+)$", line)
        if m:
            data[m.group(1).strip()] = m.group(2).strip()
    return data


# tla-rs subprocess client
class TlaRsClient:
    """
    Drives tla-rs directly as a subprocess.
    Exposes the same call(canonical, arguments) interface used by the rest
    of the verify phase so no other code needs to change.

    Supported canonicals:
      "validate", runs: tla-rs <spec.tla> --validate --json
      "check"   , runs: tla-rs <spec.tla> --config <cfg> [limits] --json
    """

    def __init__(self, binary: str) -> None:
        self._bin = binary

    def start(self) -> None:
        pass  # nothing to start

    def stop(self) -> None:
        pass  # nothing to stop

    def call(self, canonical: str, arguments: dict) -> dict:
        binary = self._bin
        spec_text = arguments.get("spec", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            tla_path = Path(tmpdir) / "base.tla"
            tla_path.write_text(spec_text)

            if canonical == "validate":
                cmd = [binary, str(tla_path), "--validate", "--json"]
            elif canonical == "check":
                cfg_text = arguments.get("config", "")
                # tla-rs does not support the TLC SPECIFICATION keyword, strip it
                cfg_text = "\n".join(
                    line for line in cfg_text.splitlines()
                    if not line.strip().upper().startswith("SPECIFICATION")
                )
                cfg_path = Path(tmpdir) / "base.cfg"
                cfg_path.write_text(cfg_text)
                cmd = [binary, str(tla_path), "--config", str(cfg_path),
                       "--allow-deadlock", "--json"]
                if "max_states" in arguments:
                    cmd += ["--max-states", str(arguments["max_states"])]
                if "max_depth" in arguments:
                    cmd += ["--max-depth", str(arguments["max_depth"])]
            else:
                raise VerifyError(f"Unknown canonical tool: {canonical!r}")

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=int(arguments.get("max_seconds", 60)),
                )
            except subprocess.TimeoutExpired:
                return {"status": "limit", "raw": "tla-rs timed out"}
            except FileNotFoundError:
                raise VerifyError(
                    f"tla-rs binary not found at {binary!r}. "
                    "Set TLA_RS_MCP_COMMAND to the correct path."
                )

            output = (result.stdout or "") + (result.stderr or "")
            try:
                return json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                # tla-rs may emit plain text; wrap it so callers can inspect
                success = result.returncode == 0
                return {"success": success, "raw": output, "returncode": result.returncode}


def _call_client(client, canonical: str, arguments: dict) -> dict:
    return client.call(canonical, arguments)


def _connect_tla_rs() -> TlaRsClient:
    binary = os.environ.get("TLA_RS_MCP_COMMAND", "tla-rs")
    client = TlaRsClient(binary)
    client.start()
    return client


# validate_spec call
def _validate(client: McpClient, tla_text: str) -> tuple[bool, str]:
    """Returns (passed, error_text)."""
    try:
        result = _call_client(client, "validate", {"spec": tla_text})
    except Exception as e:
        return False, str(e)

    raw = result.get("raw", "")
    if isinstance(result, dict) and "raw" not in result:
        raw = json.dumps(result)

    # Interpret result
    passed = (
        result.get("success") is True
        or result.get("valid")  is True
        or result.get("status") == "ok"
        or (not result.get("errors") and "error" not in raw.lower() and "fail" not in raw.lower())
    )
    errors = result.get("errors") or result.get("error") or (raw if not passed else "")
    if isinstance(errors, list):
        errors = "\n".join(str(e) for e in errors)
    return passed, str(errors)


# check_spec call
def _check(client: McpClient, tla_text: str, cfg_text: str) -> tuple[str, str]:
    """Returns (outcome, detail) where outcome is 'pass'|'counterexample'|'error'|'limit'."""
    max_states  = int(os.environ.get("TLA_RS_MAX_STATES",  "1000"))
    max_depth   = int(os.environ.get("TLA_RS_MAX_DEPTH",   "30"))
    max_seconds = int(os.environ.get("TLA_RS_MAX_SECONDS", "15"))

    try:
        result = _call_client(client, "check", {
            "spec":        tla_text,
            "config":      cfg_text,
            "max_states":  max_states,
            "max_depth":   max_depth,
            "max_seconds": max_seconds,
        })
    except Exception as e:
        return "error", str(e)

    raw = result.get("raw", json.dumps(result))

    status = (
        result.get("status")
        or result.get("outcome")
        or ""
    )
    status = str(status).lower()

    if "limit" in status or "limit reached" in raw.lower() or "limit_reached" in raw.lower():
        return "limit", raw
    if "counterexample" in status or "counterexample" in raw.lower() or ("violation" in raw.lower() and "no violation" not in raw.lower()):
        ce = result.get("counterexample") or result.get("trace") or raw
        return "counterexample", str(ce)
    if result.get("success") is True or status in ("ok", "pass", "passed") or "no violation" in raw.lower():
        return "pass", ""
    if "error" in status or "fail" in status:
        return "error", raw
    if "error" not in raw.lower() and "fail" not in raw.lower():
        return "pass", ""
    return "error", raw


# Self-repair via LLM
# Credential resolution is centralised in setup_phase.
from setup_phase import credential_present as _credential_present, get_api_key as _get_api_key, get_base_url as _get_base_url


def _repair_call(
    tla_text: str,
    errors:   str,
    provider: str,
    model:    str,
) -> str | None:
    api_key = _get_api_key(provider)
    if not api_key:
        return None

    base_url = _get_base_url(provider)

    prompt = f"""Fix the following TLA+ specification so it passes syntax validation.
Apply ONLY the minimal targeted fix for the reported errors, do not rewrite the spec.
Return ONLY the corrected TLA+ text, no explanation, no markdown fences.

Errors from tla-rs:
{errors}

Current spec:
{tla_text}
"""
    try:
        if provider == "anthropic":
            import urllib.request
            payload = json.dumps({
                "model": model,
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": prompt}],
            }).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            return data["content"][0]["text"]
        else:
            import urllib.request
            payload = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2048,
                "temperature": 0,
            }).encode()
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/chat/completions",
                data=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  warning: repair LLM call failed: {e}", file=sys.stderr)
        return None


# Export
def _export(
    out:         Path,
    scenario:    str | None,
    check_outcome: str,
    check_detail:  str,
    syntax_pass:   bool,
    repair_count:  int,
    repair_cap:    int,
    knowledge_files: list[str],
) -> Path:
    export_dir = out / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(out / BASE_TLA, export_dir / "base.tla")
    shutil.copy2(out / BASE_CFG, export_dir / "base.cfg")

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    scenario_str = scenario if scenario else "module-scoped run"

    syntax_str = "PASS" if syntax_pass else "FAIL"
    if check_outcome == "pass":
        check_str = "PASS"
    elif check_outcome == "counterexample":
        check_str = "COUNTEREXAMPLE FOUND"
    elif check_outcome == "limit":
        check_str = "FAIL (limit_reached, raise TLA_RS_MAX_* and re-run verify)"
    else:
        check_str = "FAIL"

    ce_section = ""
    if check_outcome == "counterexample":
        ce_section = f"\n### Counterexample\n{check_detail}\n"

    kf_list = "\n".join(f"- {f}" for f in knowledge_files) or "- (none recorded)"

    manifest = f"""# Export Manifest

- **Exported**: {ts}
- **Scenario**: {scenario_str}
- **Model files**: export/base.tla, export/base.cfg
- **Knowledge files used**:
{kf_list}

## Verification (tla-rs)

- **Syntax validation**: {syntax_str}
- **Model checking**: {check_str}
- **Self-repair attempts used**: {repair_count} / {repair_cap}
{ce_section}
## Handoff

This model is ready as input to the next phase (Trace Mapper / Conformance
Checker / Ticket Agent), not implemented in this POC.
"""
    manifest_path = export_dir / "manifest.md"
    manifest_path.write_text(manifest)
    return manifest_path


# generation-log append
def _log_verify(
    out: Path,
    repair_count: int,
    syntax_pass: bool,
    check_outcome: str,
    errors: str,
) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = f"""
---
## Verify entry, {ts}
- Syntax validation: {'PASS' if syntax_pass else 'FAIL'}
- Self-repair attempts: {repair_count}
- Model-check outcome: {check_outcome}
{('- Last error: ' + errors[:400]) if errors else ''}
"""
    log = out / LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(entry)


# knowledge files used (from generation-log)
def _knowledge_files_used(out: Path) -> list[str]:
    log = out / LOG_FILE
    if not log.exists():
        return []
    text = log.read_text()
    # find last "Modules used:" line
    for line in reversed(text.splitlines()):
        m = re.match(r"^- Modules used:\s*(.+)$", line)
        if m:
            mods = [x.strip() for x in m.group(1).split(",")]
            return [f"knowledge/{mod}.md" for mod in mods]
    return []


# Public API
def verify_run(
    output_dir: str | Path = ".traceproof-poc",
    *,
    client: TlaRsClient | None = None,  # injectable for tests
) -> Path:
    """
    Phase 4 entry point.
    Returns path to export/manifest.md on success.
    Raises VerifyError on unrecoverable failure.
    """
    out = Path(output_dir).expanduser().resolve()
    cfg = _parse_run_config(out)

    tla_path = out / BASE_TLA
    cfg_path = out / BASE_CFG
    if not tla_path.exists():
        raise VerifyError(f"No model/base.tla under {out}. Run 'traceproof-poc generate' first.")
    if not cfg_path.exists():
        raise VerifyError(f"No model/base.cfg under {out}. Run 'traceproof-poc generate' first.")

    scenario_raw = cfg.get("Scenario", "none, module-scoped run")
    scenario     = None if "none" in scenario_raw.lower() else scenario_raw
    provider     = cfg.get("Provider", "local")
    model_strong = cfg.get("Model (strong / drafting)", "")
    repair_cap   = int(cfg.get("Self-repair cap", "3"))

    knowledge_files = _knowledge_files_used(out)

    # Start tla-rs client
    owned_client = client is None
    if client is None:
        client = _connect_tla_rs()

    try:
        tla_text = tla_path.read_text()
        cfg_text = cfg_path.read_text()

        # Step 1: validate + capped self-repair
        repair_count = 0
        syntax_pass, errors = _validate(client, tla_text)
        print(f"Syntax validation: {'PASS' if syntax_pass else 'FAIL'}")

        while not syntax_pass and repair_count < repair_cap:
            if not _credential_present(provider) or not model_strong:
                print(
                    f"  Syntax errors present but no LLM credentials, "
                    f"cannot self-repair. Fix model/base.tla manually.",
                    file=sys.stderr,
                )
                break
            print(f"  Self-repair attempt {repair_count + 1}/{repair_cap} …")
            fixed = _repair_call(tla_text, errors, provider, model_strong)
            if not fixed:
                break
            repair_count += 1
            tla_text = fixed.strip()
            tla_path.write_text(tla_text)
            syntax_pass, errors = _validate(client, tla_text)
            print(f"  Re-validate: {'PASS' if syntax_pass else 'FAIL'}")

        if not syntax_pass:
            _log_verify(out, repair_count, False, "not_reached", errors)
            raise VerifyError(
                f"Syntax validation failed after {repair_count} repair attempt(s) "
                f"(cap={repair_cap}). Read model/generation-log.md for details."
            )

        # Step 2: model-check
        print("Model checking …")
        check_outcome, check_detail = _check(client, tla_text, cfg_text)
        print(f"Model check: {check_outcome.upper()}")

        if check_outcome == "limit":
            _log_verify(out, repair_count, True, check_outcome, check_detail)
            raise VerifyError(
                "Model check hit exploration limit (limit_reached). "
                "Raise TLA_RS_MAX_STATES / TLA_RS_MAX_DEPTH / TLA_RS_MAX_SECONDS and re-run verify. "
                "Nothing written to export/."
            )

        if check_outcome == "error":
            # Model-internal bug (not a real system counterexample), try to repair
            print(f"  Model-check error (not a system counterexample), attempting repair …")
            model_errors = check_detail
            model_repair_count = 0
            while check_outcome == "error" and model_repair_count < repair_cap:
                if not _credential_present(provider) or not model_strong:
                    break
                fixed = _repair_call(tla_text, model_errors, provider, model_strong)
                if not fixed:
                    break
                model_repair_count += 1
                repair_count += 1
                tla_text = fixed.strip()
                tla_path.write_text(tla_text)
                syntax_pass_2, _ = _validate(client, tla_text)
                if syntax_pass_2:
                    check_outcome, check_detail = _check(client, tla_text, cfg_text)
                    print(f"  Re-check: {check_outcome.upper()}")
                else:
                    check_outcome = "error"
                    check_detail = "Repaired spec failed re-validation"

            if check_outcome == "error":
                _log_verify(out, repair_count, syntax_pass, check_outcome, check_detail)
                raise VerifyError(
                    f"Model-check error could not be repaired within cap={repair_cap}. "
                    f"Nothing written to export/."
                )

        # Step 3: export
        manifest = _export(
            out=out,
            scenario=scenario,
            check_outcome=check_outcome,
            check_detail=check_detail,
            syntax_pass=syntax_pass,
            repair_count=repair_count,
            repair_cap=repair_cap,
            knowledge_files=knowledge_files,
        )
        _log_verify(out, repair_count, syntax_pass, check_outcome, check_detail)

        outcome_label = {
            "pass":            "PASS, model and manifest written to export/",
            "counterexample":  "COUNTEREXAMPLE FOUND, this is a system result, not a pipeline error. See export/manifest.md",
        }.get(check_outcome, check_outcome.upper())
        print(f"\n{outcome_label}")
        print(f"Manifest: {manifest}")
        return manifest

    finally:
        if owned_client:
            if hasattr(client, "stop"): client.stop()
            elif hasattr(client, "close"): client.close()
