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


# tla-rs MCP client
class TlaRsClient:
    """
    Drives tla-rs / tla-mcp via the Model Context Protocol (JSON-RPC over stdio).
    Exposes call(canonical, arguments) matching the verify phase interface.

    Supported canonicals:
      "validate" -> calls MCP tool 'validate_spec'
      "check"    -> calls MCP tool 'check_spec'
    """

    def __init__(self, binary: str) -> None:
        self._bin = binary
        self._proc: subprocess.Popen | None = None
        self._req_id = 0

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        if not shutil.which(self._bin) and not os.path.exists(self._bin):
            raise VerifyError(
                f"tla-rs binary not found at {self._bin!r}. "
                "Set TLA_RS_MCP_COMMAND to the correct path."
            )
        self._proc = subprocess.Popen(
            [self._bin],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        # Perform MCP initialize handshake
        self._req_id += 1
        self._send({
            "jsonrpc": "2.0",
            "id": self._req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "traceproof-poc", "version": "1.0"},
            },
        })
        self._recv()
        # Initialized notification (no response expected)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise VerifyError("tla-mcp process is not running.")
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _recv(self) -> dict:
        if not self._proc or not self._proc.stdout:
            raise VerifyError("tla-mcp process is not running.")
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise VerifyError(f"tla-mcp terminated unexpectedly: {err}")
        return json.loads(line)

    def stop(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def call(self, canonical: str, arguments: dict) -> dict:
        self.start()
        spec_text = arguments.get("spec", "")

        with tempfile.TemporaryDirectory() as tmpdir:
            tla_path = Path(tmpdir) / "base.tla"
            tla_path.write_text(spec_text)

            self._req_id += 1
            if canonical == "validate":
                tool_args: dict = {"spec_path": str(tla_path)}
                req = {
                    "jsonrpc": "2.0",
                    "id": self._req_id,
                    "method": "tools/call",
                    "params": {"name": "validate_spec", "arguments": tool_args},
                }
            elif canonical == "check":
                cfg_text = arguments.get("config", "")
                cfg_path = Path(tmpdir) / "base.cfg"
                cfg_path.write_text(cfg_text)
                tool_args = {
                    "spec_path": str(tla_path),
                    "config_path": str(cfg_path),
                    "max_states": int(arguments.get("max_states", 1000)),
                    "max_depth": int(arguments.get("max_depth", 30)),
                    "max_seconds": int(arguments.get("max_seconds", 15)),
                    "allow_deadlock": True,
                }
                req = {
                    "jsonrpc": "2.0",
                    "id": self._req_id,
                    "method": "tools/call",
                    "params": {"name": "check_spec", "arguments": tool_args},
                }
            else:
                raise VerifyError(f"Unknown canonical tool: {canonical!r}")

            self._send(req)
            resp = self._recv()

            if "error" in resp:
                return {"status": "error", "error": resp["error"].get("message", str(resp["error"]))}

            content = resp.get("result", {}).get("content", [])
            text = content[0].get("text", "") if content else ""
            try:
                data = json.loads(text)
            except Exception:
                data = {"raw": text}

            if canonical == "validate":
                if data.get("status") == "ok":
                    return {"success": True, "raw": text, "spec": data.get("spec")}
                errors = data.get("errors") or [data.get("error") or text]
                return {"success": False, "errors": errors, "raw": text}
            elif canonical == "check":
                status = data.get("status", "")
                if status == "ok":
                    return {"status": "ok", "stats": data.get("stats"), "raw": text}
                elif status == "invariant_violation":
                    trace_steps = data.get("trace", [])
                    return {
                        "status": "counterexample",
                        "invariant": data.get("invariant", ""),
                        "counterexample": trace_steps,
                        "stats": data.get("stats"),
                        "raw": text,
                    }
                elif status == "limit_reached":
                    return {"status": "limit", "raw": text}
                else:
                    return {"status": "error", "error": text, "raw": text}


def _call_client(client, canonical: str, arguments: dict) -> dict:
    return client.call(canonical, arguments)


def _connect_tla_rs() -> TlaRsClient:
    binary = (
        os.environ.get("TLA_RS_MCP_COMMAND")
        or shutil.which("tla-mcp")
        or shutil.which("tla-rs")
        or "/usr/local/bin/tla-mcp"
    )
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
        if isinstance(ce, (list, dict)):
            ce = json.dumps(ce, indent=2)
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
from shared.setup import credential_present as _credential_present, get_api_key as _get_api_key, get_base_url as _get_base_url
from agents.spec_generator.prompts import build_syntax_repair_prompt


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
    prompt = build_syntax_repair_prompt(tla_text, errors)
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
