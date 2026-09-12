"""
End-to-End Live Verification Test against real tla-mcp binary.
Demonstrates:
1. A genuine clean concurrent model passes all 5 gates using live tla-mcp model checker.
2. An intentionally corrupted vacuous spec (Inv == TRUE) is strictly rejected by verify_run.
3. An unfaithful spec (missing critical action) is strictly rejected by verify_run.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from agents.spec_generator.verify import verify_run, VerifyError, _connect_tla_rs


class TestLiveE2EVerify(unittest.TestCase):

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.lock_py = self.repo_root / "examples" / "dist_lock" / "lock.py"
        self.has_tla_mcp = bool(shutil.which("tla-mcp") or Path("/usr/local/bin/tla-mcp").exists())

    def test_e2e_clean_spec_passes_live_tla_rs(self):
        if not self.has_tla_mcp:
            self.skipTest("tla-mcp binary not found")

        # Set up a real run output directory
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            model_dir = out / "model"
            model_dir.mkdir(parents=True, exist_ok=True)

            # 1. Write run-config.md
            (out / "run-config.md").write_text(f"""# Run Config
- **Created**: 2026-09-12T00:00:00Z
- **Source paths**: {self.lock_py}
- **Scenario**: mutual exclusion under lease expiration
- **Provider**: local
- **Self-repair cap**: 3
""")

            # 2. Write legitimate base.tla
            (model_dir / "base.tla").write_text("""---- MODULE base ----
EXTENDS Naturals, FiniteSets, Sequences
VARIABLES current_owner, active_workers, lease_expiry, storage

Init == current_owner = "none" /\\ active_workers = {} /\\ lease_expiry = 0 /\\ storage = <<>>

Acquire(w) ==
    /\\ current_owner = "none"
    /\\ current_owner' = w
    /\\ active_workers' = active_workers \\cup {w}
    /\\ lease_expiry' = 1
    /\\ UNCHANGED <<storage>>

Release(w) ==
    /\\ current_owner = w
    /\\ current_owner' = "none"
    /\\ active_workers' = active_workers \\ {w}
    /\\ UNCHANGED <<lease_expiry, storage>>

DoWork(w) ==
    /\\ current_owner = w
    /\\ Len(storage) < 2
    /\\ storage' = Append(storage, w)
    /\\ UNCHANGED <<current_owner, active_workers, lease_expiry>>

ExpireLease ==
    /\\ lease_expiry = 1
    /\\ lease_expiry' = 0
    /\\ UNCHANGED <<current_owner, active_workers, storage>>

Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w) \\/ ExpireLease

MutualExclusion == Cardinality(active_workers) <= 1
====
""")

            # 3. Write base.cfg
            (model_dir / "base.cfg").write_text("""INIT Init
NEXT Next
INVARIANT MutualExclusion
""")

            # Run verify_run with real tla-mcp
            manifest_path = verify_run(output_dir=out)
            self.assertTrue(manifest_path.exists())

            manifest_content = manifest_path.read_text()
            self.assertIn("VERIFIED_NON_VACUOUS_PASS_WITH_CORRESPONDENCE", manifest_content)
            self.assertIn("Gatekeeper Verdict", manifest_content)
            self.assertIn("LLM-as-a-Judge Supporting Evidence", manifest_content)
            self.assertIn("AST Tautology Check", manifest_content)
            self.assertIn("Config Completeness", manifest_content)
            self.assertIn("Implementation Correspondence", manifest_content)

    def test_e2e_vacuous_spec_rejected_live(self):
        if not self.has_tla_mcp:
            self.skipTest("tla-mcp binary not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            model_dir = out / "model"
            model_dir.mkdir(parents=True, exist_ok=True)

            (out / "run-config.md").write_text(f"""# Run Config
- **Source paths**: {self.lock_py}
- **Scenario**: mutual exclusion
""")

            # Corrupted spec: Inv == TRUE (reward hacking)
            (model_dir / "base.tla").write_text("""---- MODULE base ----
VARIABLES current_owner, active_workers, lease_expiry, storage
Init == current_owner = "none" /\\ active_workers = {} /\\ lease_expiry = 0 /\\ storage = <<>>
Next == current_owner' = current_owner /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
Inv == TRUE
====
""")
            (model_dir / "base.cfg").write_text("""INIT Init
NEXT Next
INVARIANT Inv
""")

            # verify_run MUST reject and raise VerifyError
            with self.assertRaises(VerifyError) as ctx:
                verify_run(output_dir=out)

            self.assertIn("trivial literal TRUE", str(ctx.exception))
            # Manifest should NOT exist
            self.assertFalse((out / "export" / "manifest.md").exists())

    def test_e2e_unfaithful_deleted_action_rejected_live(self):
        if not self.has_tla_mcp:
            self.skipTest("tla-mcp binary not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            model_dir = out / "model"
            model_dir.mkdir(parents=True, exist_ok=True)

            (out / "run-config.md").write_text(f"""# Run Config
- **Source paths**: {self.lock_py}
- **Scenario**: mutual exclusion
""")

            # Unfaithful spec: ExpireLease was deleted
            (model_dir / "base.tla").write_text("""---- MODULE base ----
VARIABLES current_owner, active_workers, lease_expiry, storage
Init == current_owner = "none" /\\ active_workers = {} /\\ lease_expiry = 0 /\\ storage = <<>>
Acquire(w) == current_owner = "none" /\\ current_owner' = w /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
Release(w) == current_owner = w /\\ current_owner' = "none" /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
DoWork(w) == current_owner = w /\\ UNCHANGED <<current_owner, active_workers, lease_expiry, storage>>

Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w)
MutualExclusion == current_owner # "invalid"
====
""")
            (model_dir / "base.cfg").write_text("""INIT Init
NEXT Next
INVARIANT MutualExclusion
""")

            # verify_run MUST reject because ExpireLease was omitted
            with self.assertRaises(VerifyError) as ctx:
                verify_run(output_dir=out)

            self.assertIn("CORRESPONDENCE REJECT", str(ctx.exception))
            self.assertFalse((out / "export" / "manifest.md").exists())

    def test_e2e_guard_mutation_rejects_decoupled_invariant_live(self):
        """
        Tests that an invariant which passes TLC exploration but fails to constrain
        action guards is detected by Gate 5 guard mutation check and rejected
        in an end-to-end execution against the live tla-mcp binary.
        """
        if not self.has_tla_mcp:
            self.skipTest("tla-mcp binary not found")

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            model_dir = out / "model"
            model_dir.mkdir(parents=True, exist_ok=True)

            (out / "run-config.md").write_text(f"""# Run Config
- **Source paths**: {self.lock_py}
- **Scenario**: mutual exclusion under lease expiration
""")

            # A spec with a decoupled/weak invariant that passes standard TLC,
            # but when DoWork's guard (current_owner = w) is weakened to TRUE,
            # WeakInv is STILL not violated because storage length remains <= 10.
            # Thus, the mutant survives and Gate 5 GuardMutationSurvivor rejects it!
            (model_dir / "base.tla").write_text("""---- MODULE base ----
EXTENDS Naturals, FiniteSets, Sequences
VARIABLES current_owner, active_workers, lease_expiry, storage

Init == current_owner = "none" /\\ active_workers = {} /\\ lease_expiry = 0 /\\ storage = <<>>

Acquire(w) ==
    /\\ current_owner = "none"
    /\\ current_owner' = w
    /\\ active_workers' = active_workers \\cup {w}
    /\\ lease_expiry' = 1
    /\\ UNCHANGED <<storage>>

Release(w) ==
    /\\ current_owner = w
    /\\ current_owner' = "none"
    /\\ active_workers' = active_workers \\ {w}
    /\\ UNCHANGED <<lease_expiry, storage>>

DoWork(w) ==
    /\\ current_owner = w
    /\\ Len(storage) < 2
    /\\ storage' = Append(storage, w)
    /\\ UNCHANGED <<current_owner, active_workers, lease_expiry>>

ExpireLease ==
    /\\ lease_expiry = 1
    /\\ lease_expiry' = 0
    /\\ UNCHANGED <<current_owner, active_workers, storage>>

Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w) \\/ ExpireLease

WeakInv == Len(storage) <= 10
====
""")
            (model_dir / "base.cfg").write_text("""INIT Init
NEXT Next
INVARIANT WeakInv
""")

            with self.assertRaises(VerifyError) as ctx:
                verify_run(output_dir=out)

            self.assertIn("GuardMutationSurvivor", str(ctx.exception))
            self.assertFalse((out / "export" / "manifest.md").exists())


if __name__ == "__main__":
    unittest.main()
