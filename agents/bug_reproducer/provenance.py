"""Cryptographic Provenance Binding for Phase 7 Bug Reproducer.

Computes and verifies SHA-256 cryptographic fingerprints across:
1. Target source implementation
2. Formal TLA+ specification (base.tla)
3. Model checking configuration (base.cfg)
4. Counterexample trace artifact (manifest.md / JSON trace)

Provides tamper-evident binding ensuring runtime reproduction scripts cannot
be faked, substituted, or run against mismatched source revisions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Union


class ProvenanceError(Exception):
    """Raised when provenance computation or verification fails."""
    pass


class MissingArtifactError(ProvenanceError):
    """Raised when an artifact required for provenance is missing."""
    pass


@dataclass(frozen=True)
class ProvenanceReceipt:
    target_source_hash: str
    spec_hash: str
    cfg_hash: str
    counterexample_hash: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def compute_bytes_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest for arbitrary bytes."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def compute_file_sha256(path: Union[str, Path]) -> str:
    """
    Compute deterministic SHA-256 hex digest of a file or directory tree.
    For a directory, hashes relative paths and file contents in sorted order.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise MissingArtifactError(f"Provenance artifact path does not exist: {p}")

    if p.is_file():
        return compute_bytes_sha256(p.read_bytes())

    if p.is_dir():
        py_files = sorted(f for f in p.glob("**/*.py") if not f.name.startswith("."))
        if not py_files:
            raise MissingArtifactError(f"Target directory contains no python source files: {p}")
        hasher = hashlib.sha256()
        for f in py_files:
            rel = str(f.relative_to(p)).encode("utf-8")
            hasher.update(rel)
            hasher.update(f.read_bytes())
        return f"sha256:{hasher.hexdigest()}"

    raise ProvenanceError(f"Unsupported artifact file type: {p}")


def canonicalize_counterexample_bytes(ce_data: Union[str, bytes, list, dict]) -> bytes:
    """Produce deterministic byte representation of a counterexample trace."""
    if isinstance(ce_data, (list, dict)):
        return json.dumps(ce_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if isinstance(ce_data, str):
        try:
            parsed = json.loads(ce_data)
            return json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except Exception:
            return ce_data.strip().encode("utf-8")
    if isinstance(ce_data, bytes):
        return ce_data.strip()
    raise ProvenanceError(f"Invalid counterexample data type: {type(ce_data)}")


def compute_provenance(
    target_source: Union[str, Path],
    spec_path: Union[str, Path],
    cfg_path: Union[str, Path],
    counterexample_data: Union[str, bytes, list, dict],
) -> ProvenanceReceipt:
    """
    Computes cryptographic SHA-256 provenance hashes for all verification inputs.
    Fails closed if any required artifact is missing or invalid.
    """
    src_p = Path(target_source)
    if not src_p.exists():
        raise MissingArtifactError(f"Target source file not found: {src_p}")

    spec_p = Path(spec_path)
    if not spec_p.exists():
        raise MissingArtifactError(f"TLA+ specification file not found: {spec_p}")

    cfg_p = Path(cfg_path)
    if not cfg_p.exists():
        raise MissingArtifactError(f"TLA+ configuration file not found: {cfg_p}")

    ce_bytes = canonicalize_counterexample_bytes(counterexample_data)
    if not ce_bytes or ce_bytes in (b"[]", b"{}"):
        raise MissingArtifactError("Counterexample data is empty or missing.")

    return ProvenanceReceipt(
        target_source_hash=compute_file_sha256(src_p),
        spec_hash=compute_file_sha256(spec_p),
        cfg_hash=compute_file_sha256(cfg_p),
        counterexample_hash=compute_bytes_sha256(ce_bytes),
    )


def generate_provenance_header_code(receipt: ProvenanceReceipt) -> str:
    """Generates Python docstring / comments declaring cryptographic provenance."""
    return f'''# ── Non-Fungible TLC Provenance Receipt ────────────────────────────────────────
# Target Source SHA-256:    {receipt.target_source_hash}
# TLA+ Spec SHA-256:        {receipt.spec_hash}
# TLA+ Config SHA-256:      {receipt.cfg_hash}
# Counterexample SHA-256:   {receipt.counterexample_hash}
# ──────────────────────────────────────────────────────────────────────────────'''


def generate_provenance_assertion_code(receipt: ProvenanceReceipt, target_path_expr: str) -> str:
    """Generates Python runtime code to verify source integrity before replay execution."""
    return f'''    # [Provenance Check] Verify target source file has not been altered or tampered with
    _target_resolved = Path({target_path_expr}).resolve()
    if not _target_resolved.exists():
        raise FileNotFoundError(f"Provenance Error: Target file {{_target_resolved}} missing.")
    
    import hashlib
    _hasher = hashlib.sha256()
    if _target_resolved.is_file():
        _current_hash = "sha256:" + hashlib.sha256(_target_resolved.read_bytes()).hexdigest()
    else:
        _py_files = sorted(f for f in _target_resolved.glob("**/*.py") if not f.name.startswith("."))
        for _f in _py_files:
            _hasher.update(str(_f.relative_to(_target_resolved)).encode("utf-8"))
            _hasher.update(_f.read_bytes())
        _current_hash = "sha256:" + _hasher.hexdigest()

    _expected_hash = "{receipt.target_source_hash}"
    if _current_hash != _expected_hash:
        raise RuntimeError(
            f"Cryptographic Provenance Mismatch!\\n"
            f"Target source has changed since TLC verification was performed.\\n"
            f"Expected: {{_expected_hash}}\\n"
            f"Found:    {{_current_hash}}\\n"
            f"Re-run Phase 5 formal verification to update counterexample attachment."
        )
'''
