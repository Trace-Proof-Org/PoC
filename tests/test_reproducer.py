"""
Unit and Integration Tests for Non-Fungible TLC Attachment to Runtime Reproducer.
Tests all 6 verification scenarios required by Mentor Issue #3:
1. Valid leased-lock counterexample and target (deterministic reproduction + TLC step citations + SHA-256 hashes).
2. Structurally different concurrency target (proves adapter is not target-name hard-coded).
3. Unknown-action counterexample (fails closed with UnmappedActionError).
4. Incomplete transition mapping (fails closed).
5. Deliberately inconsistent target/trace pair (fails closed before execution).
6. Missing or malformed counterexample artifact (fails closed on missing/corrupted/tampered inputs).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agents.bug_reproducer.adapters import (
    AdapterRegistry,
    DistLockAdapter,
    GenericFunctionAdapter,
    UnmappedActionError,
)
from agents.bug_reproducer.provenance import (
    MissingArtifactError,
    ProvenanceError,
    compute_provenance,
)
from agents.bug_reproducer.reproducer import (
    MalformedCounterexampleError,
    MissingCounterexampleError,
    confirm_bug,
    parse_counterexample_from_manifest,
)


class TestReproducerAttachment(unittest.TestCase):

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.lock_py = self.repo_root / "examples" / "dist_lock" / "lock.py"
        self.assertTrue(self.lock_py.exists(), f"lock.py missing at {self.lock_py}")

    def _setup_dist_lock_manifest(self, export_dir: Path, custom_trace: list[dict] | None = None) -> Path:
        """Helper to create a standard dist_lock export/manifest.md."""
        export_dir.mkdir(parents=True, exist_ok=True)
        trace = custom_trace or [
            {
                "step": 1,
                "action": "Init",
                "state": {
                    "current_owner": "none",
                    "lease_expiry": 0,
                    "active_workers": [],
                    "storage": [],
                },
            },
            {
                "step": 2,
                "action": 'Acquire("w1")',
                "state": {
                    "current_owner": "w1",
                    "lease_expiry": 1,
                    "active_workers": ["w1"],
                    "storage": [],
                },
            },
            {
                "step": 3,
                "action": "ExpireLease",
                "state": {
                    "current_owner": "w1",
                    "lease_expiry": 0,
                    "active_workers": ["w1"],
                    "storage": [],
                },
            },
            {
                "step": 4,
                "action": 'Acquire("w2")',
                "state": {
                    "current_owner": "w2",
                    "lease_expiry": 1,
                    "active_workers": ["w1", "w2"],
                    "storage": [],
                },
            },
        ]

        manifest_text = f"""# Export Manifest
- **Exported**: 2026-09-13T00:00:00Z
- **Scenario**: mutual exclusion under lease expiration
- **Model files**: export/base.tla, export/base.cfg

## Verification (tla-rs)
- **Syntax validation**: PASS
- **Model checking**: COUNTEREXAMPLE FOUND
- **Self-repair attempts used**: 0 / 5

### Counterexample
{json.dumps(trace, indent=2)}

## Handoff
Ready for Phase 7.
"""
        manifest_path = export_dir / "manifest.md"
        manifest_path.write_text(manifest_text, encoding="utf-8")

        # Also write dummy base.tla and base.cfg for provenance
        (export_dir / "base.tla").write_text("---- MODULE base ----\nVARIABLE current_owner\n====")
        (export_dir / "base.cfg").write_text("INIT Init\nNEXT Next\nINVARIANT MutualExclusion")

        return manifest_path

    # ── Test 1: Valid Leased-Lock Target & Counterexample ──────────────────────
    def test_valid_leased_lock_counterexample_and_target(self):
        """
        Scenario 1: A valid leased-lock counterexample and target reproduces the
        bug deterministically, cites TLC transitions step-by-step, records the
        transition mapping table, and computes valid SHA-256 provenance hashes.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            export_dir = out / "export"
            self._setup_dist_lock_manifest(export_dir)

            # Run reproducer
            res = confirm_bug(output_dir=str(out), target_source=str(self.lock_py))

            # 1. Verification results
            self.assertTrue(res.reproduced)
            self.assertEqual(res.verdict, "CONFIRMED_REAL_BUG")
            self.assertGreater(res.violations_detected, 0)

            # 2. Provenance hashes
            self.assertIsNotNone(res.provenance)
            self.assertTrue(res.target_source_hash.startswith("sha256:"))
            self.assertTrue(res.spec_hash.startswith("sha256:"))
            self.assertTrue(res.cfg_hash.startswith("sha256:"))
            self.assertTrue(res.counterexample_hash.startswith("sha256:"))

            # 3. Transition Mapping Table
            self.assertIsNotNone(res.transition_mapping_table)
            self.assertEqual(len(res.transition_mapping_table), 4)
            self.assertEqual(res.transition_mapping_table[0]["tlc_action"], "Init")
            self.assertIn("Acquire", res.transition_mapping_table[1]["tlc_action"])
            self.assertEqual(res.transition_mapping_table[2]["tlc_action"], "ExpireLease")
            self.assertIn("Acquire", res.transition_mapping_table[3]["tlc_action"])

            # 4. Generated test harness cites TLC transitions
            harness_content = Path(res.test_script_path).read_text()
            self.assertIn("Non-Fungible TLC Provenance Receipt", harness_content)
            self.assertIn("Step 1: TLC Init", harness_content)
            self.assertIn("Step 2: TLC Action Acquire", harness_content)
            self.assertIn("Step 3: TLC Action ExpireLease", harness_content)
            self.assertIn("Step 4: TLC Action Acquire", harness_content)
            self.assertIn("Cites TLC Transition", harness_content)

    # ── Test 2: Structurally Different Concurrency Target ─────────────────────
    def test_structurally_different_concurrency_target(self):
        """
        Scenario 2: A second, structurally different concurrency target (test fixture)
        demonstrates that the target adapter and scheduler are dynamic and not
        hard-coded to 'dist_lock' or 'Worker-1'.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            target_dir = out / "custom_target"
            target_dir.mkdir(parents=True, exist_ok=True)

            # Create a completely different target: SharedQueue
            queue_py = target_dir / "bounded_queue.py"
            queue_py.write_text("""# BoundedQueue concurrency target
import threading

queue = []
max_size = 1
overflow_count = 0

def push(item):
    global queue, overflow_count
    if len(queue) >= max_size:
        overflow_count += 1
    queue.append(item)

def pop():
    global queue
    if queue:
        return queue.pop(0)
    return None
""")

            # Counterexample for queue overflow
            queue_trace = [
                {
                    "step": 1,
                    "action": "Init",
                    "state": {"queue": [], "overflow_count": 0},
                },
                {
                    "step": 2,
                    "action": 'Push("msg1")',
                    "state": {"queue": ["msg1"], "overflow_count": 0},
                },
                {
                    "step": 3,
                    "action": 'Push("msg2")',
                    "state": {"queue": ["msg1", "msg2"], "overflow_count": 1},
                },
            ]

            export_dir = out / "export"
            self._setup_dist_lock_manifest(export_dir, custom_trace=queue_trace)

            # Verify adapter resolution chooses GenericFunctionAdapter, NOT DistLockAdapter
            actions = {s["action"] for s in queue_trace}
            adapter = AdapterRegistry.get_adapter(queue_py, actions)
            self.assertIsInstance(adapter, GenericFunctionAdapter)

            # Run confirm_bug on this structurally different target
            res = confirm_bug(output_dir=str(out), target_source=str(queue_py))

            self.assertTrue(res.reproduced)
            self.assertEqual(res.verdict, "CONFIRMED_REAL_BUG")
            self.assertEqual(len(res.transition_mapping_table), 3)
            self.assertEqual(res.transition_mapping_table[1]["tlc_action"], 'Push("msg1")')
            self.assertEqual(res.transition_mapping_table[2]["tlc_action"], 'Push("msg2")')

            # Verify the generated test imports bounded_queue and has no dist_lock references
            harness_code = Path(res.test_script_path).read_text()
            self.assertIn("bounded_queue.py", harness_code)
            self.assertNotIn("dist_lock", harness_code)
            self.assertNotIn("Worker-1", harness_code)

    # ── Test 3: Unknown-Action Counterexample ──────────────────────────────────
    def test_unknown_action_counterexample_fails_closed(self):
        """
        Scenario 3: An unknown action in the counterexample trace cannot be mapped
        by any adapter and fails closed with UnmappedActionError, refusing to substitute
        a generic schedule.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            export_dir = out / "export"

            alien_trace = [
                {"step": 1, "action": "Init", "state": {"current_owner": "none"}},
                {"step": 2, "action": 'AlienUnmappedAction("x")', "state": {"current_owner": "x"}},
            ]
            self._setup_dist_lock_manifest(export_dir, custom_trace=alien_trace)

            with self.assertRaises(UnmappedActionError) as ctx:
                confirm_bug(output_dir=str(out), target_source=str(self.lock_py))

            self.assertIn("AlienUnmappedAction", str(ctx.exception))
            self.assertIn("Reproduction aborted to prevent substituting an arbitrary schedule", str(ctx.exception))

    # ── Test 4: Incomplete Transition Mapping ─────────────────────────────────
    def test_incomplete_transition_mapping_fails_closed(self):
        """
        Scenario 4: An incomplete transition mapping (e.g. missing action parameters
        or an unsupported action on a specific adapter) fails closed.
        """
        adapter = DistLockAdapter()
        with self.assertRaises(UnmappedActionError):
            adapter.map_transition(
                step=2,
                action_name="NonExistentLockAction",
                action_args=[],
                state_before={},
                state_after={},
                module_var="lock",
            )

    # ── Test 5: Deliberately Inconsistent Target/Trace Pair ────────────────────
    def test_inconsistent_target_trace_pair_fails_closed(self):
        """
        Scenario 5: A deliberately inconsistent target and trace pair (e.g. supplying
        a trace for a lock to a target that is just an empty file) fails closed before
        executing any test.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            incompatible_py = out / "empty_target.py"
            incompatible_py.write_text("# empty file\nx = 1\n")

            export_dir = out / "export"
            self._setup_dist_lock_manifest(export_dir)

            # Target has no lock primitives and no functions matching actions
            with self.assertRaises(UnmappedActionError) as ctx:
                confirm_bug(output_dir=str(out), target_source=str(incompatible_py))

            self.assertIn("No target adapter can map actions", str(ctx.exception))

    # ── Test 6: Missing or Malformed Counterexample Artifact ───────────────────
    def test_missing_counterexample_artifact_fails_closed(self):
        """
        Scenario 6A: Absent manifest.md strictly fails closed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            with self.assertRaises(MissingCounterexampleError):
                confirm_bug(output_dir=str(out), target_source=str(self.lock_py))

    def test_no_counterexample_in_manifest_fails_closed(self):
        """
        Scenario 6B: Manifest with PASS but no counterexample fails closed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            export_dir = out / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            (export_dir / "manifest.md").write_text("# Export Manifest\n- **Model checking**: PASS\n")

            with self.assertRaises(MissingCounterexampleError):
                confirm_bug(output_dir=str(out), target_source=str(self.lock_py))

    def test_malformed_counterexample_json_fails_closed(self):
        """
        Scenario 6C: Corrupted or truncated JSON in counterexample fails closed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            export_dir = out / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            (export_dir / "manifest.md").write_text(
                "# Export Manifest\nCOUNTEREXAMPLE FOUND\n### Counterexample\n[ { incomplete json ...\n"
            )

            with self.assertRaises(MalformedCounterexampleError):
                confirm_bug(output_dir=str(out), target_source=str(self.lock_py))

    def test_empty_counterexample_array_fails_closed(self):
        """
        Scenario 6D: Empty counterexample array [] fails closed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            export_dir = out / "export"
            export_dir.mkdir(parents=True, exist_ok=True)
            (export_dir / "manifest.md").write_text(
                "# Export Manifest\nCOUNTEREXAMPLE FOUND\n### Counterexample\n[]\n"
            )

            with self.assertRaises(MalformedCounterexampleError):
                confirm_bug(output_dir=str(out), target_source=str(self.lock_py))

    def test_runtime_tamper_detection_on_altered_source(self):
        """
        Scenario 6E: If the target source file is altered after test generation,
        the non-fungible provenance check in the generated test harness halts
        execution with a RuntimeError before any schedule is executed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            export_dir = out / "export"
            self._setup_dist_lock_manifest(export_dir)

            # Copy lock.py to a mutable location
            target_copy = out / "lock_copy.py"
            target_copy.write_text(self.lock_py.read_text())

            res = confirm_bug(output_dir=str(out), target_source=str(target_copy))
            self.assertTrue(res.reproduced)

            # Now tamper with target_copy.py
            target_copy.write_text(target_copy.read_text() + "\n# TAMPERED LINE\n")

            # Executing the synthesized test harness MUST fail with RuntimeError
            from agents.bug_reproducer.reproducer import run_reproduction_test
            tamper_res = run_reproduction_test(Path(res.test_script_path))
            self.assertNotEqual(tamper_res["returncode"], 0)
            self.assertIn("Cryptographic Provenance Mismatch", tamper_res["combined_output"])


if __name__ == "__main__":
    unittest.main()
