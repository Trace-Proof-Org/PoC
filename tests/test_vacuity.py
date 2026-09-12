"""
Comprehensive Unit Test Suite for Non-Vacuity & Implementation Correspondence.
Covers all 11 test fixtures addressing Mentor Issue #4 (EricSpencer00).
"""

import os
import tempfile
import unittest
from pathlib import Path

from agents.spec_generator.vacuity import (
    VacuousInvariantError,
    VariableFreeInvariantError,
    MissingInvariantConfigError,
    TrivialStateSpaceError,
    ImplementationCorrespondenceError,
    DeadCoreActionError,
    MutationSurvivorError,
    evaluate_spec_vacuity,
    check_ast_tautology,
    check_config_completeness,
    check_implementation_correspondence,
    check_state_exploration,
    PythonSourceExtractor,
)


class MockTlaClient:
    """Mock TLC / tla-mcp client for deterministic unit testing without spawning external binaries."""

    def __init__(self, check_handler=None):
        self.check_handler = check_handler

    def call(self, canonical: str, args: dict) -> dict:
        if canonical == "check":
            if self.check_handler:
                return self.check_handler(args)
            return {"status": "ok", "stats": {"distinct_states": 10, "transitions": 15}, "raw": "10 distinct states"}
        elif canonical == "validate":
            return {"success": True, "raw": "ok"}
        return {}


class TestNonVacuityAndCorrespondence(unittest.TestCase):

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        self.lock_target = self.repo_root / "examples" / "dist_lock" / "lock.py"

    # --------------------------------------------------------------------------
    # Fixture 1: Inv == TRUE (Vacuous Invariant)
    # --------------------------------------------------------------------------
    def test_fixture_inv_true(self):
        tla = """---- MODULE Test ----
VARIABLES x
Init == x = 0
Next == x' = x + 1
Inv == TRUE
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        with self.assertRaises(VacuousInvariantError):
            check_ast_tautology(tla, cfg)

        report = evaluate_spec_vacuity(tla, cfg)
        self.assertFalse(report.passed)
        self.assertIn("VACUOUS_SPEC", report.verdict)
        self.assertTrue(any("trivial literal TRUE" in f for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 2: Inv == x = x (Reflexive Tautology)
    # --------------------------------------------------------------------------
    def test_fixture_tautology_x_equals_x(self):
        tla = """---- MODULE Test ----
VARIABLES x
Init == x = 0
Next == x' = x + 1
Inv == x = x
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        with self.assertRaises(VacuousInvariantError):
            check_ast_tautology(tla, cfg)

        report = evaluate_spec_vacuity(tla, cfg)
        self.assertFalse(report.passed)
        self.assertTrue(any("reflexive tautology" in f for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 3: Invariant referencing 0 variables (Variable-Free Invariant)
    # --------------------------------------------------------------------------
    def test_fixture_variable_free_invariant(self):
        tla = """---- MODULE Test ----
VARIABLES x, y
Init == x = 0 /\\ y = 0
Next == x' = x + 1 /\\ y' = y + 1
Inv == 1 + 1 = 2
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        with self.assertRaises(VariableFreeInvariantError):
            check_ast_tautology(tla, cfg, allow_constant_spec=False)

        # Confirm that automated gatekeeper rejects flatly
        report = evaluate_spec_vacuity(tla, cfg, allow_constant_spec=False)
        self.assertFalse(report.passed)
        self.assertTrue(any("references 0 state variables" in f for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 4: Missing Invariant in Config
    # --------------------------------------------------------------------------
    def test_fixture_missing_invariant_config(self):
        tla = """---- MODULE Test ----
VARIABLES x
Init == x = 0
Next == x' = x + 1
SafeInv == x < 10
====
"""
        # Config omits INVARIANT line entirely
        cfg_missing = """INIT Init
NEXT Next
"""
        with self.assertRaises(MissingInvariantConfigError):
            check_config_completeness(tla, cfg_missing)

        # Config references non-existent operator
        cfg_wrong_name = """INIT Init
NEXT Next
INVARIANT NonExistentInv
"""
        with self.assertRaises(MissingInvariantConfigError):
            check_config_completeness(tla, cfg_wrong_name)

        report = evaluate_spec_vacuity(tla, cfg_missing)
        self.assertFalse(report.passed)
        self.assertIn("Gate 1 Failed", report.hard_gates_failed[0])

    # --------------------------------------------------------------------------
    # Fixture 5: One-State Behavior / Unreachable Next
    # --------------------------------------------------------------------------
    def test_fixture_unreachable_next_one_state(self):
        # 1-state exploration output from TLC
        stats = {"distinct_states": 1, "transitions": 0}
        raw = "Model checking completed. 1 distinct state found. 0 transitions."

        with self.assertRaises(TrivialStateSpaceError):
            check_state_exploration(stats, raw, core_actions=["ActionA"])

        tla = """---- MODULE Test ----
VARIABLES x
Init == x = 0
Next == FALSE /\\ x' = x
Inv == x >= 0
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        report = evaluate_spec_vacuity(
            tla, cfg, tlc_stats=stats, tlc_raw_output=raw
        )
        self.assertFalse(report.passed)
        self.assertTrue(any("explored only 1 distinct state" in f for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 6: Deleted Action (Target Primitive Missing in Spec)
    # --------------------------------------------------------------------------
    def test_fixture_deleted_action(self):
        # Leased lock Python target defines acquire, release, do_work, and lease expiration
        # Here we model a cheated spec that DELETED ExpireLease from Next
        cheated_tla = """---- MODULE CheatedLock ----
VARIABLES current_owner, active_workers, lease_expiry, storage

Init == current_owner = "none" /\\ active_workers = {} /\\ lease_expiry = 0 /\\ storage = <<>>

Acquire(w) == current_owner = "none" /\\ current_owner' = w /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
Release(w) == current_owner = w /\\ current_owner' = "none" /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
DoWork(w) == current_owner = w /\\ UNCHANGED <<current_owner, active_workers, lease_expiry, storage>>

\\* NOTE: ExpireLease was deliberately deleted to prevent race condition!
Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w)

Inv == Cardinality(active_workers) <= 1
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        with self.assertRaises(ImplementationCorrespondenceError):
            check_implementation_correspondence(self.lock_target, cheated_tla, cfg)

        report = evaluate_spec_vacuity(
            cheated_tla, cfg, target_source=self.lock_target
        )
        self.assertFalse(report.passed)
        self.assertIn("UNFAITHFUL_MODEL", report.verdict)
        self.assertTrue(any("expire_lease" in f.lower() for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 7: Weakened Guard (Caught by Guard Mutation Sweep)
    # --------------------------------------------------------------------------
    def test_fixture_weakened_guard(self):
        # A mock TLC client that returns 'ok' (survives) when mutating Acquire guard
        def mock_checker(args):
            spec = args.get("spec", "")
            # If the mutant spec for Acquire survived
            if "TRUE /\\" in spec:
                return {"status": "ok", "stats": {"distinct_states": 5}, "raw": "ok"}
            return {"status": "counterexample", "raw": "violation"}

        client = MockTlaClient(check_handler=mock_checker)

        tla = """---- MODULE LockModel ----
VARIABLES owner
Init == owner = "none"
Acquire(w) == owner = "none" /\\ owner' = w
Next == \\E w \\in {"w1", "w2"}: Acquire(w)
Inv == owner # "both"
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        stats = {"distinct_states": 4, "transitions": 6}
        report = evaluate_spec_vacuity(
            tla, cfg, client=client, tlc_stats=stats, tlc_raw_output="4 distinct states"
        )
        self.assertFalse(report.passed)
        self.assertTrue(any("GuardMutationSurvivor" in f and "Acquire" in f for f in report.hard_gates_failed))

    def test_fixture_partial_mutation_survivor_rejected(self):
        """
        Tests that Gate 5 strictly requires a 100% kill rate and rejects
        a model where 1 mutant is killed and 1 mutant survives (50% kill rate).
        """
        def mock_checker(args):
            spec = args.get("spec", "")
            if "MutantNegated_" in spec:
                return {"status": "counterexample", "raw": "violation"}
            # Release mutant survives (status ok), Acquire mutant is killed (counterexample)
            if "Release(w) == TRUE /\\" in spec:
                return {"status": "ok", "stats": {"distinct_states": 5}, "raw": "ok"}
            return {"status": "counterexample", "raw": "violation"}

        client = MockTlaClient(check_handler=mock_checker)
        tla = """---- MODULE PartialSurvivor ----
VARIABLES owner, active
Init == owner = "none" /\\ active = {}
Acquire(w) == owner = "none" /\\ owner' = w /\\ active' = active \\cup {w}
Release(w) == owner = w /\\ owner' = "none" /\\ active' = active \\ {w}
Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w)
Inv == owner # "both"
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        stats = {"distinct_states": 6, "transitions": 10}
        report = evaluate_spec_vacuity(
            tla, cfg, client=client, tlc_stats=stats, tlc_raw_output="6 distinct states"
        )
        self.assertFalse(report.passed)
        self.assertIn("VERIFIER_FAIL (VACUOUS_SPEC)", report.verdict)
        self.assertTrue(any("GuardMutationSurvivor" in f and "Release" in f and "50%" in f for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 8: Unbounded Vacuous Invariant (Inv == x >= -10^9 caught by ~Inv)
    # --------------------------------------------------------------------------
    def test_fixture_unbounded_vacuous_invariant(self):
        # Negating x >= -1000 produces x < -1000.
        # If reachable states only have x in 0..5, x < -1000 is NEVER reached!
        # Thus TLC returns "ok" for the negated invariant mutant, exposing vacuity!
        def mock_checker(args):
            spec = args.get("spec", "")
            if "MutantNegated_" in spec:
                # Negated invariant is never violated in reachable states (mutant survived!)
                return {"status": "ok", "stats": {"distinct_states": 5}, "raw": "ok"}
            return {"status": "counterexample", "raw": "violation"}

        client = MockTlaClient(check_handler=mock_checker)

        tla = """---- MODULE Unbounded ----
VARIABLES x
Init == x = 0
Next == x < 5 /\\ x' = x + 1
Inv == x >= -1000
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        stats = {"distinct_states": 5, "transitions": 5}
        report = evaluate_spec_vacuity(
            tla, cfg, client=client, tlc_stats=stats, tlc_raw_output="5 distinct states"
        )
        self.assertFalse(report.passed)
        self.assertTrue(any("Invariant negation mutant" in f for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 9: Auxiliary Dead Action (Produces Warning, not Hard Failure)
    # --------------------------------------------------------------------------
    def test_fixture_auxiliary_dead_action(self):
        # All core actions executed, but auxiliary action 'Tick' was not exercised
        stats = {"distinct_states": 5, "transitions": 8}
        raw = "5 distinct states. Transitions: Acquire, Release."

        res = check_state_exploration(
            stats, raw, core_actions=["Acquire", "Release"], auxiliary_actions=["Tick"]
        )
        self.assertEqual(len(res.dead_core_actions), 0)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("Tick", res.warnings[0])
        self.assertTrue(res.coverage_available)

    def test_fixture_per_action_coverage_catches_dead_action_with_many_states(self):
        """
        Tests that when per-action evidence is present, a permanently disabled
        core action is rejected with DeadCoreActionError even if distinct_states is large (e.g. 50).
        """
        from agents.spec_generator.vacuity import DeadCoreActionError
        stats = {"distinct_states": 50, "transitions": 120}
        raw = "50 distinct states. Transitions: Acquire."

        with self.assertRaises(DeadCoreActionError) as ctx:
            check_state_exploration(
                stats, raw, core_actions=["Acquire", "Release"]
            )
        self.assertIn("Release", str(ctx.exception))

    def test_fixture_per_action_coverage_unavailable_reported(self):
        """
        Tests that when the model checker only reports aggregate Next transitions
        without per-action breakdown, coverage is explicitly reported as unavailable
        rather than falsely marking disabled actions as exercised.
        """
        stats = {
            "distinct_states": 42,
            "transitions": 89,
            "actions": [{"name": "Next", "transitions": 89}],
        }
        raw = '{"status": "ok", "stats": {"distinct_states": 42, "transitions": 89}}'

        res = check_state_exploration(
            stats, raw, core_actions=["Acquire", "Release", "DoWork"]
        )
        self.assertFalse(res.coverage_available)
        self.assertEqual(res.exercised_core_actions, [])
        self.assertEqual(res.dead_core_actions, [])
        self.assertTrue(any("unavailable" in w.lower() for w in res.warnings))

    # --------------------------------------------------------------------------
    # Fixture 10: Counterexample vs. Verifier Fail Distinction
    # --------------------------------------------------------------------------
    def test_fixture_counterexample_vs_fail(self):
        # When TLC finds a real system invariant violation (counterexample):
        # check_outcome is "counterexample", NOT a verifier failure!
        from agents.spec_generator.verify import _check

        mock_ce_client = MockTlaClient(check_handler=lambda args: {
            "status": "invariant_violation",
            "invariant": "MutualExclusion",
            "counterexample": [{"state": 1}, {"state": 2}],
            "stats": {"distinct_states": 6},
            "raw": "Error: Invariant MutualExclusion is violated.",
        })

        outcome, detail, stats, raw = _check(mock_ce_client, "spec", "config")
        self.assertEqual(outcome, "counterexample")
        self.assertIn("counterexample", outcome)

        # Contrast with a vacuous spec:
        vacuous_tla = """---- MODULE Vacuous ----
VARIABLES x
Init == x = 0
Next == x' = x + 1
Inv == TRUE
====
"""
        cfg = "INIT Init\nNEXT Next\nINVARIANT Inv\n"
        rep = evaluate_spec_vacuity(vacuous_tla, cfg)
        self.assertFalse(rep.passed)
        self.assertEqual(rep.verdict, "VERIFIER_FAIL (VACUOUS_SPEC)")

    # --------------------------------------------------------------------------
    # Fixture 11: Legitimate Model (Passes All Gates Cleanly with RLAIF Audit)
    # --------------------------------------------------------------------------
    def test_fixture_legitimate_model(self):
        # A fully faithful, non-vacuous leased lock model
        legit_tla = """---- MODULE LeasedLock ----
EXTENDS Naturals, FiniteSets
VARIABLES current_owner, active_workers, lease_expiry, storage

Init == current_owner = "none" /\\ active_workers = {} /\\ lease_expiry = 0 /\\ storage = <<>>

Acquire(w) == current_owner = "none" /\\ current_owner' = w /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
Release(w) == current_owner = w /\\ current_owner' = "none" /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
DoWork(w) == current_owner = w /\\ UNCHANGED <<current_owner, active_workers, lease_expiry, storage>>
ExpireLease == lease_expiry = 0 /\\ UNCHANGED <<current_owner, active_workers, storage>>

Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w) \\/ ExpireLease

MutualExclusion == Cardinality(active_workers) <= 1
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT MutualExclusion
"""
        # Client that kills negation mutant and kills guard mutants
        def mock_sound_checker(args):
            return {
                "status": "counterexample",
                "counterexample": [{"state": 1}, {"state": 2}],
                "stats": {"distinct_states": 8, "transitions": 14},
                "raw": "violation detected",
            }

        client = MockTlaClient(check_handler=mock_sound_checker)
        stats = {"distinct_states": 8, "transitions": 14}
        raw = "8 distinct states. Transitions: Acquire, Release, DoWork, ExpireLease."

        report = evaluate_spec_vacuity(
            tla_text=legit_tla,
            cfg_text=cfg,
            target_source=self.lock_target,
            client=client,
            tlc_stats=stats,
            tlc_raw_output=raw,
        )

        self.assertTrue(report.passed, f"Failed with: {report.hard_gates_failed}")
        self.assertEqual(report.verdict, "VERIFIED_NON_VACUOUS_PASS_WITH_CORRESPONDENCE")
        self.assertEqual(len(report.hard_gates_passed), 5)
        self.assertIsNotNone(report.correspondence)
        self.assertGreaterEqual(report.correspondence.semantic_confidence, 0.8)
        self.assertIn("Verified implementation correspondence", report.semantic_notes)

    # --------------------------------------------------------------------------
    # Fixture 12: Unconditional Action Handling (Marked NOT_APPLICABLE, Not Survivor)
    # --------------------------------------------------------------------------
    def test_fixture_unconditional_action_not_applicable(self):
        # Action 'Step' has no preconditions/guards (unconditional transition)
        tla = """---- MODULE UnconditionalModel ----
VARIABLES x, y
Init == x = 0 /\\ y = 0
Step == x' = x + 1 /\\ UNCHANGED <<y>>
Next == Step
Inv == x < 100 /\\ y = 0
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        # Client kills negation mutant (~Inv triggers counterexample)
        def mock_checker(args):
            spec = args.get("spec", "")
            if "MutantNegated_" in spec:
                return {"status": "counterexample", "raw": "violation"}
            return {"status": "ok", "stats": {"distinct_states": 5}, "raw": "ok"}

        client = MockTlaClient(check_handler=mock_checker)
        stats = {"distinct_states": 5, "transitions": 5}
        raw = "5 distinct states. Transitions: Step."

        report = evaluate_spec_vacuity(
            tla_text=tla,
            cfg_text=cfg,
            client=client,
            tlc_stats=stats,
            tlc_raw_output=raw,
            allow_constant_spec=False,
        )

        self.assertTrue(report.passed, f"Should pass when action is unconditional: {report.hard_gates_failed}")
        self.assertIsNotNone(report.mutation_stats)
        self.assertEqual(len(report.mutation_stats.survivors), 0)
        self.assertTrue(any("NOT_APPLICABLE (UNCONDITIONAL_ACTION" in na for na in report.mutation_stats.not_applicable))
        self.assertTrue(any("action(s) marked NOT_APPLICABLE (unconditional)" in g for g in report.hard_gates_passed))

    # --------------------------------------------------------------------------
    # Fixture 13: Warning When Target Code Lacks Concurrency Primitives
    # --------------------------------------------------------------------------
    def test_fixture_no_concurrency_primitives_warning(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            simple_py = Path(tmpdir) / "calc.py"
            simple_py.write_text("""
def calculate(a, b):
    return a + b

def display(result):
    print(result)
""")
            tla = """---- MODULE Calc ----
VARIABLES result
Init == result = 0
Calculate == result' = 10
Display == UNCHANGED <<result>>
Next == Calculate \\/ Display
Inv == result >= 0
====
"""
            cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
            corr = check_implementation_correspondence(simple_py, tla, cfg)
            self.assertTrue(any("No concurrency primitives" in w for w in corr.warnings))

    # --------------------------------------------------------------------------
    # Fixture 14: Missing State Variable Rejection (Hard Failure in Gate 3)
    # --------------------------------------------------------------------------
    def test_fixture_missing_state_variable_rejected(self):
        """
        Tests that Gate 3 strictly rejects a specification that omits required
        state variables (e.g. storage or lease_expiry), even if action names match.
        """
        # Spec preserves action names but omits 'storage' and 'lease_expiry'
        incomplete_vars_tla = """---- MODULE MissingVars ----
VARIABLES current_owner, active_workers
Init == current_owner = "none" /\\ active_workers = {}
Acquire(w) == current_owner = "none" /\\ current_owner' = w /\\ active_workers' = active_workers \\cup {w}
Release(w) == current_owner = w /\\ current_owner' = "none" /\\ active_workers' = active_workers \\ {w}
DoWork(w) == current_owner = w /\\ UNCHANGED <<current_owner, active_workers>>
ExpireLease == current_owner' = "none" /\\ UNCHANGED <<active_workers>>
Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w) \\/ ExpireLease
Inv == Cardinality(active_workers) <= 1
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        stats = {"distinct_states": 6, "transitions": 10}
        report = evaluate_spec_vacuity(
            tla_text=incomplete_vars_tla,
            cfg_text=cfg,
            target_source=self.lock_target,
            tlc_stats=stats,
            tlc_raw_output="6 distinct states. Transitions: Acquire, Release, DoWork, ExpireLease.",
        )
        self.assertFalse(report.passed)
        self.assertIn("UNFAITHFUL_MODEL", report.verdict)
        self.assertTrue(any("storage" in f.lower() or "lease_expiry" in f.lower() for f in report.hard_gates_failed))

    # --------------------------------------------------------------------------
    # Fixture 15: Documented Exclusion Mechanism Allows Intentional Variable Omission
    # --------------------------------------------------------------------------
    def test_fixture_missing_state_variable_explicitly_excluded(self):
        """
        Tests that when an unmodeled state variable is explicitly excluded via
        the documented exclusion comment (* @exclude_vars: ...), Gate 3 accepts it.
        """
        excluded_vars_tla = """---- MODULE ExcludedVars ----
\\* @exclude_vars: storage, lease_expiry
VARIABLES current_owner, active_workers
Init == current_owner = "none" /\\ active_workers = {}
Acquire(w) == current_owner = "none" /\\ current_owner' = w /\\ active_workers' = active_workers \\cup {w}
Release(w) == current_owner = w /\\ current_owner' = "none" /\\ active_workers' = active_workers \\ {w}
DoWork(w) == current_owner = w /\\ UNCHANGED <<current_owner, active_workers>>
ExpireLease == current_owner' = "none" /\\ UNCHANGED <<active_workers>>
Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w) \\/ ExpireLease
Inv == Cardinality(active_workers) <= 1
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        corr = check_implementation_correspondence(self.lock_target, excluded_vars_tla, cfg)
        self.assertNotIn("storage", corr.missing_vars)
        self.assertNotIn("lease_expiry", corr.missing_vars)
        self.assertIn("current_owner", corr.matched_vars)
        self.assertIn("active_workers", corr.matched_vars)

    # --------------------------------------------------------------------------
    # Fixture 16: Directory Source Path Resolution (No IsADirectoryError)
    # --------------------------------------------------------------------------
    def test_fixture_directory_source_resolution(self):
        """
        Tests that specifying a directory (e.g. examples/dist_lock) as target_source
        resolves to the Python file(s) inside without crashing with IsADirectoryError.
        """
        dist_lock_dir = Path("examples/dist_lock")
        self.assertTrue(dist_lock_dir.is_dir())

        tla = """---- MODULE DirModel ----
VARIABLES current_owner, active_workers, lease_expiry, storage
Init == current_owner = "none" /\\ active_workers = {} /\\ lease_expiry = 0 /\\ storage = <<>>
Acquire(w) == current_owner = "none" /\\ current_owner' = w /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
Release(w) == current_owner = w /\\ current_owner' = "none" /\\ UNCHANGED <<active_workers, lease_expiry, storage>>
DoWork(w) == current_owner = w /\\ UNCHANGED <<current_owner, active_workers, lease_expiry, storage>>
ExpireLease == lease_expiry = 0 /\\ UNCHANGED <<current_owner, active_workers, storage>>
Next == \\E w \\in {"w1", "w2"}: Acquire(w) \\/ Release(w) \\/ DoWork(w) \\/ ExpireLease
Inv == Cardinality(active_workers) <= 1
====
"""
        cfg = """INIT Init
NEXT Next
INVARIANT Inv
"""
        # Passing directory directly to check_implementation_correspondence
        corr = check_implementation_correspondence(dist_lock_dir, tla, cfg)
        self.assertEqual(corr.missing_actions, [])
        self.assertEqual(corr.missing_vars, [])
        self.assertIn("current_owner", corr.matched_vars)

    # --------------------------------------------------------------------------
    # Fixture 17: Multiple Source Paths Resolution & Merging
    # --------------------------------------------------------------------------
    def test_fixture_multiple_source_paths_resolution(self):
        """
        Tests that multiple source paths are parsed and merged into a unified
        token representation.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            f1 = Path(tmpdir) / "part1.py"
            f1.write_text("lock_owner = 'none'\ndef acquire(): pass\n")
            f2 = Path(tmpdir) / "part2.py"
            f2.write_text("queue_buffer = []\ndef release(): pass\n")

            from agents.spec_generator.vacuity import PythonSourceExtractor
            extractor = PythonSourceExtractor([f1, f2])
            tokens = extractor.extract()

            self.assertIn("acquire", tokens.public_functions)
            self.assertIn("release", tokens.public_functions)
            self.assertIn("lock_owner", tokens.concurrency_primitives)
            self.assertIn("queue_buffer", tokens.concurrency_primitives)


if __name__ == "__main__":
    unittest.main()
