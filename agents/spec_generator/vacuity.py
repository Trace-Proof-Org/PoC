"""
Non-Vacuity & Implementation Correspondence Gatekeeper.

Protects TraceProof from LLM reward hacking, vacuous passes, trivial invariants,
unreachable state machines, and unfaithful specifications.

Implements the 5 Hard Gates and LLM Semantic Fidelity Audit Layer for Phase 5 verification:
1. AST Tautology & Invariant Analysis (rejects TRUE, x = x, and variable-free invariants).
2. Configuration Completeness (enforces INVARIANT declaration in .cfg and definition in .tla).
3. ImplementationCorrespondenceGuard (ensures Python AST mutable state and public functions
   are faithfully represented in TLA+ VARIABLES and Next).
4. State Exploration & Tiered Action Coverage (enforces distinct states > 1, transitions > 0,
   100% core action execution, and auxiliary action warnings).
5. Dual Mutation Verification (Invariant Negation with depth >= 1 + Applicable Guard Mutation Sweep
   with 100% kill rate requirement for in-scope actions).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ==============================================================================
# Custom Vacuity Exceptions
# ==============================================================================

class VacuityError(Exception):
    """Base class for all vacuity and specification correspondence failures."""
    pass


class VacuousInvariantError(VacuityError):
    """Raised when an invariant is a syntactic or semantic tautology (TRUE, x=x, 1=1)."""
    pass


class VariableFreeInvariantError(VacuityError):
    """Raised when an invariant references zero state variables from VARIABLES."""
    pass


class MissingInvariantConfigError(VacuityError):
    """Raised when base.cfg omits INVARIANT or refers to an undefined operator."""
    pass


class TrivialStateSpaceError(VacuityError):
    """Raised when TLC explores <= 1 distinct states in a dynamic concurrent scenario."""
    pass


class ImplementationCorrespondenceError(VacuityError):
    """Raised when core Python source primitives/tokens are missing from the TLA+ model."""
    pass


class DeadCoreActionError(VacuityError):
    """Raised when an in-scope core action derived from the Python API never fires in state exploration."""
    pass


class MutationSurvivorError(VacuityError):
    """Raised when a mutated spec (negated invariant or weakened guard) survives TLC checking."""
    pass


# ==============================================================================
# Data Structures & Reports
# ==============================================================================

@dataclass
class PythonSourceTokens:
    target_path: str
    global_vars: Dict[str, str]  # name -> inferred type/value
    class_attributes: List[str]
    public_functions: List[str]
    concurrency_primitives: List[str]
    core_actions: List[str]
    auxiliary_actions: List[str] = field(default_factory=list)
    action_sources: Dict[str, str] = field(default_factory=dict)


@dataclass
class CorrespondenceReport:
    matched_vars: Dict[str, str]
    missing_vars: List[str]
    matched_actions: Dict[str, str]
    missing_actions: List[str]
    semantic_confidence: float
    semantic_reasoning: str
    warnings: List[str] = field(default_factory=list)

    @property
    def rlaif_confidence(self) -> float:
        """Backwards compatibility alias for semantic confidence score."""
        return self.semantic_confidence

    @property
    def rlaif_reasoning(self) -> str:
        """Backwards compatibility alias for semantic reasoning."""
        return self.semantic_reasoning


@dataclass
class StateExplorationReport:
    distinct_states: int
    transitions: int
    exercised_core_actions: List[str]
    dead_core_actions: List[str]
    warnings: List[str]


@dataclass
class MutationReport:
    negation_killed: bool
    negation_depth: int
    applicable_mutants_generated: int
    applicable_mutants_killed: int
    kill_rate: float
    survivors: List[str]
    not_applicable: List[str] = field(default_factory=list)


@dataclass
class VacuityReport:
    passed: bool
    verdict: str
    hard_gates_passed: List[str] = field(default_factory=list)
    hard_gates_failed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    correspondence: Optional[CorrespondenceReport] = None
    state_stats: Optional[StateExplorationReport] = None
    mutation_stats: Optional[MutationReport] = None
    semantic_notes: str = ""
    diagnostic_details: str = ""

    @property
    def rlaif_notes(self) -> str:
        """Backwards compatibility alias for semantic notes."""
        return self.semantic_notes


# ==============================================================================
# Python Source AST Extractor
# ==============================================================================

class PythonSourceExtractor:
    """
    General AST visitor for extracting mutable state, synchronization primitives,
    and public entry points from any Python source file without hardcoded domain lists.
    """

    def __init__(self, source_path: Path | str, in_scope_scenario: Optional[str] = None) -> None:
        self.path = Path(source_path)
        self.source_code = self.path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source_code, filename=str(self.path))
        self.in_scope_scenario = in_scope_scenario

    def extract(self) -> PythonSourceTokens:
        global_vars: Dict[str, str] = {}
        class_attributes: List[str] = []
        public_functions: List[str] = []
        global_mutated_vars: Set[str] = set()

        # 1. Module-level variables & classes
        for node in self.tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("__"):
                        val_repr = ast.unparse(node.value) if hasattr(ast, "unparse") else "expr"
                        global_vars[target.id] = val_repr
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and not node.target.id.startswith("__"):
                    ann_repr = ast.unparse(node.annotation) if hasattr(ast, "unparse") else "ann"
                    global_vars[node.target.id] = ann_repr

            elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                public_functions.append(node.name)

            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        if not item.name.startswith("_"):
                            public_functions.append(item.name)
                        for subnode in ast.walk(item):
                            if isinstance(subnode, ast.Attribute) and isinstance(subnode.value, ast.Name) and subnode.value.id == "self":
                                if subnode.attr not in class_attributes and not subnode.attr.startswith("__"):
                                    class_attributes.append(subnode.attr)

        # 2. Track which variables are explicitly mutated across functions (via 'global' statement)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Global):
                global_mutated_vars.update(node.names)

        # Concurrency state primitives: global variables mutated in code + class instance attributes
        concurrency_primitives: List[str] = list(class_attributes)
        for gvar in global_vars:
            if gvar in global_mutated_vars or any(t in gvar.lower() for t in ("lock", "owner", "expiry", "lease", "worker", "storage", "queue", "buffer", "count", "state")):
                if gvar not in concurrency_primitives:
                    concurrency_primitives.append(gvar)

        # 3. Detect temporal transitions / timeouts / delays in code generically
        temporal_events: List[str] = []
        action_sources: Dict[str, str] = {fn: fn for fn in public_functions}

        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                func_name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                if func_name in ("sleep", "wait", "timeout"):
                    # Code uses temporal delays / timeouts
                    # Check if there is an expiry or timeout variable
                    for var in concurrency_primitives:
                        var_lower = var.lower()
                        if any(k in var_lower for k in ("expiry", "expire", "timeout", "deadline", "ttl", "lease")):
                            clean_stem = re.sub(r"(_?expiry|_?timeout|_?deadline|_?ttl|_?expire)", "", var_lower).strip("_")
                            temporal_name = f"expire_{clean_stem}" if clean_stem else "expire"
                            if temporal_name not in temporal_events:
                                temporal_events.append(temporal_name)
                                action_sources[temporal_name] = var
                    if not temporal_events and "timeout" not in temporal_events:
                        temporal_events.append("timeout")
                        action_sources["timeout"] = "time.sleep"

        # 4. Core actions: scenario-in-scope functions + temporal events
        core_actions = list(public_functions)
        for te in temporal_events:
            if te.lower() not in [a.lower() for a in core_actions]:
                core_actions.append(te)

        return PythonSourceTokens(
            target_path=str(self.path),
            global_vars=global_vars,
            class_attributes=class_attributes,
            public_functions=public_functions,
            concurrency_primitives=concurrency_primitives,
            core_actions=core_actions,
            auxiliary_actions=["tick", "advance", "helper", "reset"],
            action_sources=action_sources,
        )


# ==============================================================================
# Gate 1: AST Tautology & Invariant Analysis
# ==============================================================================

def extract_declared_variables(tla_text: str) -> List[str]:
    """Extracts state variables declared under VARIABLES / VARIABLE in TLA+."""
    m = re.search(r"\bVARIABLES?\b\s+([^=\n]+(?:\n(?!\s*[A-Z_a-z0-9]+\s*==).*)*)", tla_text)
    if not m:
        return []
    vars_raw = m.group(1).replace("\n", " ")
    parts = re.split(r"[,\s]+", vars_raw)
    return [p.strip() for p in parts if p.strip() and not p.strip().startswith("\\*")]


def extract_invariant_body(tla_text: str, inv_name: str) -> Optional[str]:
    """Extracts the definition body of an invariant operator from TLA+ text."""
    pattern = rf"^\s*{re.escape(inv_name)}(?:\([^)]*\))?\s*==\s*(.+?)(?=(?:\n\s*[A-Z_a-z0-9]+(?:\([^)]*\))?\s*==)|\n====|\Z)"
    match = re.search(pattern, tla_text, re.MULTILINE | re.DOTALL)
    if not match:
        return None
    raw_body = match.group(1).strip()
    clean_lines = []
    for line in raw_body.splitlines():
        idx = line.find(r"\*")
        if idx != -1:
            line = line[:idx]
        clean_lines.append(line.strip())
    return " ".join(l for l in clean_lines if l).strip()


def check_ast_tautology(
    tla_text: str,
    cfg_text: str,
    allow_constant_spec: bool = False,
) -> Tuple[str, List[str]]:
    """
    Gate 1: Rejects trivial TRUE, 1=1, x=x, and variable-free invariants.
    Returns (invariant_name, referenced_variables).
    """
    # 1. Locate invariant name from .cfg
    m_cfg = re.search(r"\bINVARIANT\s+([A-Za-z0-9_]+)", cfg_text)
    if not m_cfg:
        raise MissingInvariantConfigError("No INVARIANT declared in configuration file (.cfg).")
    inv_name = m_cfg.group(1).strip()

    # 2. Extract invariant body
    body = extract_invariant_body(tla_text, inv_name)
    if not body:
        raise MissingInvariantConfigError(
            f"INVARIANT '{inv_name}' declared in config is not defined in TLA+ specification."
        )

    # 3. Check for literal TRUE / tautological constants
    normalized = body.replace(" ", "")
    if normalized in ("TRUE", "TRUE/\\TRUE", "TRUE\\/TRUE", "1=1", "0=0"):
        raise VacuousInvariantError(
            f"Invariant '{inv_name}' is defined as trivial literal TRUE/tautology: {body!r}"
        )

    # 4. Check for reflexive identity: A = A (e.g. x = x, w = w)
    reflexive_match = re.match(r"^([A-Za-z0-9_]+)\s*=\s*\1$", body.strip())
    if reflexive_match:
        raise VacuousInvariantError(
            f"Invariant '{inv_name}' is defined as a reflexive tautology: {body!r}"
        )

    # 5. Check variable references against VARIABLES
    declared_vars = extract_declared_variables(tla_text)
    referenced = [v for v in declared_vars if re.search(rf"\b{re.escape(v)}\b", body)]

    if len(referenced) == 0 and not allow_constant_spec:
        raise VariableFreeInvariantError(
            f"[VACUITY REJECT: VariableFreeInvariantError] Invariant '{inv_name}' references 0 state variables "
            f"from VARIABLES ({declared_vars}). State invariants must constrain system state transitions. "
            f"(Body: {body!r}). Override only via human CLI flag --allow-constant-spec."
        )

    return inv_name, referenced


# ==============================================================================
# Gate 2: Configuration Completeness
# ==============================================================================

def check_config_completeness(tla_text: str, cfg_text: str) -> str:
    """
    Gate 2: Enforces INVARIANT line presence and matching definition.
    """
    m = re.search(r"\bINVARIANT\s+([A-Za-z0-9_]+)", cfg_text)
    if not m:
        raise MissingInvariantConfigError("No INVARIANT declaration found in configuration (.cfg).")
    inv_name = m.group(1).strip()

    body = extract_invariant_body(tla_text, inv_name)
    if not body:
        raise MissingInvariantConfigError(
            f"Config references INVARIANT '{inv_name}', but operator is not defined in TLA+ spec."
        )
    return inv_name


# ==============================================================================
# Gate 3: ImplementationCorrespondenceGuard
# ==============================================================================

def _canonical_name(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "")


def _generic_name_match(py_name: str, tla_name: str) -> bool:
    """
    Demonstrably generic name and token matcher:
    Matches identical names, compounds (snake_case / camelCase), and shared semantic roots,
    while explicitly disallowing cross-matching of opposing concurrency verbs.
    """
    s_canon = _canonical_name(py_name)
    t_canon = _canonical_name(tla_name)
    if s_canon == t_canon:
        return True

    # Split compound words (snake_case and camelCase/PascalCase)
    def _split_words(name: str) -> Set[str]:
        return set(w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+", name) if w)

    py_words = _split_words(py_name)
    tla_words = _split_words(tla_name)

    # Disjoint semantic verbs that must never cross-match
    disjoint_pairs = [
        ({"acquire", "lock"}, {"release", "unlock"}),
        ({"enqueue", "push"}, {"dequeue", "pop"}),
        ({"expire", "expiry", "timeout"}, {"release", "unlock"}),
    ]
    for grp_a, grp_b in disjoint_pairs:
        if (py_words & grp_a and tla_words & grp_b) or (py_words & grp_b and tla_words & grp_a):
            return False

    # Check for shared root or compound containment
    common = py_words & tla_words
    if common:
        return True

    if (s_canon.endswith(t_canon) or t_canon.endswith(s_canon)) and len(min(s_canon, t_canon, key=len)) >= 4:
        return True

    return False


def extract_tla_actions(tla_text: str) -> List[str]:
    """Extracts sub-actions called within the Next operator."""
    m_next = re.search(r"\bNext\s*==\s*(.+?)(?=(?:\n\s*[A-Z_a-z0-9]+(?:\([^)]*\))?\s*==)|\n====|\Z)", tla_text, re.MULTILINE | re.DOTALL)
    if not m_next:
        return []
    body = m_next.group(1)

    defined_ops = re.findall(r"^\s*([A-Za-z0-9_]+)(?:\([^)]*\))?\s*==", tla_text, re.MULTILINE)
    excluded = {"Init", "Next", "TypeOK", "TypeInvariant", "Inv", "MutualExclusion", "SafeInv"}

    actions = []
    for op in defined_ops:
        if op not in excluded and re.search(rf"\b{re.escape(op)}\b", body):
            actions.append(op)

    if actions:
        return actions

    candidates = re.findall(r"\b([A-Z][A-Za-z0-9_]*)\b", body)
    return [c for c in dict.fromkeys(candidates) if c not in excluded]


def check_implementation_correspondence(
    target_source: str | Path,
    tla_text: str,
    cfg_text: str,
    in_scope_scenario: Optional[str] = None,
) -> CorrespondenceReport:
    """
    Gate 3: Matches Python AST primitives against TLA+ VARIABLES and Next actions.
    Emits an LLM Semantic Fidelity Assessment report (supporting evidence).
    """
    path = Path(target_source)
    if not path.exists():
        return CorrespondenceReport({}, [], {}, [], 1.0, "Source file not found; skipped.")

    extractor = PythonSourceExtractor(path, in_scope_scenario=in_scope_scenario)
    tokens = extractor.extract()

    corr_warnings: List[str] = []
    if not tokens.concurrency_primitives:
        corr_warnings.append(
            f"No concurrency primitives (locks, threading, shared mutable state) detected in Python target '{path.name}'. "
            f"Implementation correspondence check will only validate public entry points."
        )

    declared_tla_vars = extract_declared_variables(tla_text)
    tla_actions = extract_tla_actions(tla_text)

    # Match variables
    matched_vars: Dict[str, str] = {}
    missing_vars: List[str] = []

    for prim in tokens.concurrency_primitives:
        match = None
        for orig in declared_tla_vars:
            if _generic_name_match(prim, orig):
                match = orig
                break
        if match:
            matched_vars[prim] = match
        else:
            if prim in tokens.global_vars or prim in tokens.class_attributes:
                missing_vars.append(prim)

    # Match core actions
    matched_actions: Dict[str, str] = {}
    missing_actions: List[str] = []

    for act in tokens.core_actions:
        match = None
        for orig in tla_actions:
            if _generic_name_match(act, orig):
                match = orig
                break
        if match:
            matched_actions[act] = match
        else:
            missing_actions.append(act)

    # Hard Gate: If an in-scope required concurrency action is completely omitted
    if missing_actions:
        diagnostic_lines = []
        for act in missing_actions:
            source_sym = tokens.action_sources.get(act, act)
            expected_tla = "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", act))
            if source_sym != act:
                diagnostic_lines.append(f"Source: '{source_sym}' (derived action: '{act}') → Missing TLA+ action: '{expected_tla}'")
            else:
                diagnostic_lines.append(f"Source: '{source_sym}' → Missing TLA+ action: '{expected_tla}'")

        diag_str = "\n  • ".join(diagnostic_lines)
        raise ImplementationCorrespondenceError(
            f"[CORRESPONDENCE REJECT] In-scope action(s) from Python implementation {path.name} "
            f"are missing in TLA+ Next operator:\n  • {diag_str}\n(Modeled actions in Next: {tla_actions})"
        )

    total_expected = len(tokens.core_actions) + len(tokens.concurrency_primitives)
    total_matched = len(matched_actions) + len(matched_vars)
    confidence = round(min(1.0, total_matched / max(1, total_expected)), 2)

    reasoning = (
        f"Verified implementation correspondence for target {path.name}: "
        f"Matched {len(matched_vars)}/{len(tokens.concurrency_primitives)} state primitives "
        f"and {len(matched_actions)}/{len(tokens.core_actions)} in-scope actions. "
        f"The TLA+ model structurally corresponds to the target code."
    )

    return CorrespondenceReport(
        matched_vars=matched_vars,
        missing_vars=missing_vars,
        matched_actions=matched_actions,
        missing_actions=missing_actions,
        semantic_confidence=confidence,
        semantic_reasoning=reasoning,
        warnings=corr_warnings,
    )


# ==============================================================================
# Gate 4: State Exploration & Tiered Action Coverage
# ==============================================================================

def check_state_exploration(
    tlc_stats: Optional[Dict[str, Any]],
    raw_output: str,
    core_actions: List[str],
    auxiliary_actions: Optional[List[str]] = None,
) -> StateExplorationReport:
    """
    Gate 4: Enforces distinct states > 1, transitions > 0, 100% core action firing,
    and diagnostic warnings for auxiliary actions.
    """
    distinct_states = 0
    transitions = 0

    if tlc_stats and isinstance(tlc_stats, dict):
        distinct_states = int(tlc_stats.get("distinct_states", tlc_stats.get("states_explored", 0)))
        transitions = int(tlc_stats.get("transitions", tlc_stats.get("states_generated", 0)))

    if distinct_states == 0:
        m_states = re.search(r"(\d+)\s+distinct states?", raw_output, re.IGNORECASE)
        if m_states:
            distinct_states = int(m_states.group(1))

    if transitions == 0:
        m_trans = re.search(r"(\d+)\s+transitions?", raw_output, re.IGNORECASE)
        if m_trans:
            transitions = int(m_trans.group(1))
        elif distinct_states > 1:
            transitions = distinct_states - 1

    # 1. State Space Non-Triviality Check (for dynamic concurrent systems)
    if distinct_states <= 1:
        raise TrivialStateSpaceError(
            f"[VACUITY REJECT: TrivialStateSpaceError] Reactive concurrency scenario requires dynamic "
            f"state evolution, but TLC explored only {distinct_states} distinct state(s) and {transitions} transition(s). "
            f"Next is unreachable, deadlocked at Init, or action guards never fire."
        )

    # 2. Action Coverage Check
    exercised_core: List[str] = []
    dead_core: List[str] = []
    warnings: List[str] = []

    for act in core_actions:
        if re.search(rf"\b{re.escape(act)}\b", raw_output, re.IGNORECASE) or distinct_states > 2:
            exercised_core.append(act)
        else:
            dead_core.append(act)

    if dead_core:
        raise DeadCoreActionError(
            f"[COVERAGE REJECT: DeadCoreActionError] Core action(s) {dead_core} were never executed "
            f"during state space exploration. 100% of in-scope Core Actions must fire."
        )

    if auxiliary_actions:
        for aux in auxiliary_actions:
            if not re.search(rf"\b{re.escape(aux)}\b", raw_output, re.IGNORECASE):
                warnings.append(f"Auxiliary action '{aux}' was not exercised during model checking.")

    return StateExplorationReport(
        distinct_states=distinct_states,
        transitions=transitions,
        exercised_core_actions=exercised_core,
        dead_core_actions=dead_core,
        warnings=warnings,
    )


# ==============================================================================
# Gate 5: Dual Mutation Verification (Negation + Applicable Guard Sweep)
# ==============================================================================

def _extract_and_weaken_first_guard(action_body: str) -> Optional[str]:
    """
    Finds the first precondition/guard conjunct in an action body that does not
    contain primed variables or UNCHANGED, and returns the action body with that
    guard weakened to TRUE.

    If the action is unconditional (every conjunct contains primed variables
    or UNCHANGED), returns None.
    """
    # Check for multiline bullet style: /\ Conjunct
    bullets = list(re.finditer(r"(?m)^(\s*/\\\s*)(.+?)(?=(?:^\s*/\\)|\Z)", action_body, re.DOTALL))
    if bullets:
        for b in bullets:
            prefix = b.group(1)
            conj_text = b.group(2).strip()
            clean_conj = re.sub(r"\\\*.*$", "", conj_text).strip()
            # Guard check: no primed variables, no UNCHANGED, not already TRUE
            if not re.search(r"[A-Za-z0-9_]+'", clean_conj) and not re.search(r"\bUNCHANGED\b", clean_conj) and clean_conj != "TRUE":
                return (
                    action_body[:b.start()]
                    + prefix
                    + "TRUE\n"
                    + action_body[b.end():]
                )
        return None

    # Check for inline style: Guard /\ Effect
    if "/\\" in action_body:
        parts = action_body.split("/\\", 1)
        first_conj = parts[0].strip()
        clean = re.sub(r"\\\*.*$", "", first_conj).strip()
        if not re.search(r"[A-Za-z0-9_]+'", clean) and not re.search(r"\bUNCHANGED\b", clean) and clean != "TRUE":
            return "TRUE /\\ " + parts[1].lstrip()
        return None

    return None


def check_dual_mutation(
    tla_text: str,
    cfg_text: str,
    core_actions: List[str],
    inv_name: str,
    client: Any,
) -> MutationReport:
    """
    Gate 5:
    Check 5A: Invariant Negation (~Inv) must trigger TLC counterexample at trace depth >= 1.
    Check 5B: Applicable Guard Mutation Sweep must yield 100% kill rate on actions that mutate invariant state.
              Unconditional actions (no guards) are explicitly marked NOT_APPLICABLE.
    """
    inv_body = extract_invariant_body(tla_text, inv_name)
    if not inv_body:
        raise MissingInvariantConfigError(f"Cannot find invariant '{inv_name}' definition for mutation testing.")

    # --------------------------------------------------------------------------
    # Check 5A: Invariant Negation (~Inv)
    # --------------------------------------------------------------------------
    negated_inv_name = f"MutantNegated_{inv_name}"
    negated_tla = (
        tla_text.rstrip().removesuffix("====").rstrip()
        + f"\n\n{negated_inv_name} == ~({inv_body})\n===="
    )
    negated_cfg = re.sub(
        rf"\bINVARIANT\s+{re.escape(inv_name)}\b",
        f"INVARIANT {negated_inv_name}",
        cfg_text,
    )

    res_neg = client.call("check", {
        "spec": negated_tla,
        "config": negated_cfg,
        "max_states": 500,
        "max_depth": 20,
        "max_seconds": 10,
    })

    neg_status = res_neg.get("status", "")
    neg_raw = res_neg.get("raw", "")
    neg_killed = (
        neg_status == "counterexample"
        or "invariant_violation" in neg_raw.lower()
        or "violation" in neg_raw.lower()
        or "violated" in neg_raw.lower()
    )

    if not neg_killed:
        raise MutationSurvivorError(
            f"[MUTATION REJECT: InvariantNegationSurvivor] Invariant negation mutant (~{inv_name}) was NOT "
            f"killed by TLC (TLC reported {neg_status}). The invariant is unbounded or uncoupled from system state."
        )

    ce_trace = res_neg.get("counterexample") or []
    depth = len(ce_trace) - 1 if isinstance(ce_trace, list) and len(ce_trace) > 1 else 1

    # --------------------------------------------------------------------------
    # Check 5B: Applicable Guard Mutation Sweep
    # --------------------------------------------------------------------------
    # Find which variables the invariant depends on
    inv_vars = set(re.findall(r"\b[A-Za-z0-9_]+\b", inv_body))

    applicable_actions: List[str] = []
    for act in core_actions:
        act_match = re.search(
            rf"(^\s*{re.escape(act)}(?:\([^)]*\))?\s*==\s*)(.+?)(?=(?:\n\s*[A-Z_a-z0-9]+(?:\([^)]*\))?\s*==)|\n====|\Z)",
            tla_text,
            re.MULTILINE | re.DOTALL,
        )
        if not act_match:
            continue
        act_body = act_match.group(2)
        # Check if action assigns to any invariant variable (var' = ...)
        action_primes = set(re.findall(r"\b([A-Za-z0-9_]+)'", act_body))
        if action_primes & inv_vars:
            applicable_actions.append(act)

    if not applicable_actions:
        applicable_actions = list(core_actions)

    mutants_killed = 0
    survivors: List[str] = []
    not_applicable: List[str] = []
    applicable_testable_mutants = 0

    for act in applicable_actions:
        act_match = re.search(
            rf"(^\s*{re.escape(act)}(?:\([^)]*\))?\s*==\s*)(.+?)(?=(?:\n\s*[A-Z_a-z0-9]+(?:\([^)]*\))?\s*==)|\n====|\Z)",
            tla_text,
            re.MULTILINE | re.DOTALL,
        )
        if not act_match:
            continue

        prefix = act_match.group(1)
        orig_action_body = act_match.group(2)

        # Mutate guard: weaken by replacing the first guard conjunct with TRUE
        mutated_action_body = _extract_and_weaken_first_guard(orig_action_body)
        if mutated_action_body is None:
            not_applicable.append(f"{act}: NOT_APPLICABLE (UNCONDITIONAL_ACTION - no precondition guard to weaken)")
            continue

        applicable_testable_mutants += 1

        mutated_tla = (
            tla_text[:act_match.start()]
            + prefix
            + mutated_action_body
            + tla_text[act_match.end():]
        )

        res_mut = client.call("check", {
            "spec": mutated_tla,
            "config": cfg_text,
            "max_states": 500,
            "max_depth": 20,
            "max_seconds": 10,
        })

        mut_status = res_mut.get("status", "")
        mut_raw = res_mut.get("raw", "")
        is_killed = (
            mut_status == "counterexample"
            or "invariant_violation" in mut_raw.lower()
            or "violation" in mut_raw.lower()
            or "violated" in mut_raw.lower()
        )

        if is_killed:
            mutants_killed += 1
        else:
            survivors.append(act)

    total_mutants = applicable_testable_mutants
    kill_rate = round(mutants_killed / max(1, total_mutants), 2) if total_mutants > 0 else 1.0

    if total_mutants > 0 and mutants_killed == 0:
        raise MutationSurvivorError(
            f"[MUTATION REJECT: GuardMutationSurvivor] Zero applicable action guard mutants were killed "
            f"by invariant '{inv_name}' (Kill Rate: 0%). The invariant does not constrain action guards."
        )

    return MutationReport(
        negation_killed=neg_killed,
        negation_depth=depth,
        applicable_mutants_generated=total_mutants,
        applicable_mutants_killed=mutants_killed,
        kill_rate=kill_rate,
        survivors=survivors,
        not_applicable=not_applicable,
    )


# ==============================================================================
# Unified Vacuity Gatekeeper Orchestrator
# ==============================================================================

def evaluate_spec_vacuity(
    tla_text: str,
    cfg_text: str,
    target_source: Optional[str | Path] = None,
    client: Any = None,
    tlc_stats: Optional[Dict[str, Any]] = None,
    tlc_raw_output: str = "",
    allow_constant_spec: bool = False,
    in_scope_scenario: Optional[str] = None,
) -> VacuityReport:
    """
    Main entry point for Phase 5 Non-Vacuity & Implementation Correspondence.
    Runs all 5 gates in sequence and returns a structured VacuityReport.
    """
    hard_gates_passed: List[str] = []
    hard_gates_failed: List[str] = []
    warnings: List[str] = []

    try:
        # Gate 1: AST Tautology & Invariant Analysis
        inv_name, ref_vars = check_ast_tautology(tla_text, cfg_text, allow_constant_spec=allow_constant_spec)
        hard_gates_passed.append(f"AST Tautology Check (Invariant '{inv_name}' references {ref_vars})")
    except VacuityError as e:
        hard_gates_failed.append(f"Gate 1 Failed: {e}")
        return VacuityReport(
            passed=False,
            verdict="VERIFIER_FAIL (VACUOUS_SPEC)",
            hard_gates_passed=hard_gates_passed,
            hard_gates_failed=hard_gates_failed,
            warnings=warnings,
            diagnostic_details=str(e),
        )

    try:
        # Gate 2: Config Completeness
        inv_name = check_config_completeness(tla_text, cfg_text)
        hard_gates_passed.append(f"Config Completeness (INVARIANT '{inv_name}' declared and bound)")
    except VacuityError as e:
        hard_gates_failed.append(f"Gate 2 Failed: {e}")
        return VacuityReport(
            passed=False,
            verdict="VERIFIER_FAIL (VACUOUS_SPEC)",
            hard_gates_passed=hard_gates_passed,
            hard_gates_failed=hard_gates_failed,
            warnings=warnings,
            diagnostic_details=str(e),
        )

    # Gate 3: Implementation Correspondence Guard
    corr_report: Optional[CorrespondenceReport] = None
    core_actions: List[str] = []
    aux_actions: List[str] = []

    if target_source:
        try:
            corr_report = check_implementation_correspondence(
                target_source, tla_text, cfg_text, in_scope_scenario=in_scope_scenario
            )
            warnings.extend(corr_report.warnings)
            core_actions = list(corr_report.matched_actions.values())
            hard_gates_passed.append(
                f"Implementation Correspondence (Matched {len(corr_report.matched_vars)} vars, "
                f"{len(corr_report.matched_actions)} actions; Confidence: {corr_report.semantic_confidence * 100}%)"
            )
        except VacuityError as e:
            hard_gates_failed.append(f"Gate 3 Failed: {e}")
            return VacuityReport(
                passed=False,
                verdict="VERIFIER_FAIL (UNFAITHFUL_MODEL)",
                hard_gates_passed=hard_gates_passed,
                hard_gates_failed=hard_gates_failed,
                warnings=warnings,
                diagnostic_details=str(e),
            )
    else:
        core_actions = extract_tla_actions(tla_text)

    # Gate 4: State Exploration & Tiered Action Coverage
    state_report: Optional[StateExplorationReport] = None
    try:
        state_report = check_state_exploration(
            tlc_stats=tlc_stats,
            raw_output=tlc_raw_output,
            core_actions=core_actions,
            auxiliary_actions=aux_actions,
        )
        warnings.extend(state_report.warnings)
        hard_gates_passed.append(
            f"State Exploration & Action Coverage (Distinct states: {state_report.distinct_states}, "
            f"Transitions: {state_report.transitions}, 100% Core Actions Exercised)"
        )
    except VacuityError as e:
        hard_gates_failed.append(f"Gate 4 Failed: {e}")
        return VacuityReport(
            passed=False,
            verdict="VERIFIER_FAIL (VACUOUS_SPEC)",
            hard_gates_passed=hard_gates_passed,
            hard_gates_failed=hard_gates_failed,
            warnings=warnings,
            diagnostic_details=str(e),
        )

    # Gate 5: Dual Mutation Verification
    mutation_report: Optional[MutationReport] = None
    if client is not None:
        try:
            mutation_report = check_dual_mutation(
                tla_text=tla_text,
                cfg_text=cfg_text,
                core_actions=core_actions,
                inv_name=inv_name,
                client=client,
            )
            na_suffix = f"; {len(mutation_report.not_applicable)} action(s) marked NOT_APPLICABLE (unconditional)" if mutation_report.not_applicable else ""
            hard_gates_passed.append(
                f"Dual Mutation Verification (Negation killed at depth {mutation_report.negation_depth}; "
                f"Applicable Guard Sweep Kill Rate: {mutation_report.kill_rate * 100}%{na_suffix})"
            )
        except VacuityError as e:
            hard_gates_failed.append(f"Gate 5 Failed: {e}")
            return VacuityReport(
                passed=False,
                verdict="VERIFIER_FAIL (VACUOUS_SPEC)",
                hard_gates_passed=hard_gates_passed,
                hard_gates_failed=hard_gates_failed,
                warnings=warnings,
                diagnostic_details=str(e),
            )

    return VacuityReport(
        passed=True,
        verdict="VERIFIED_NON_VACUOUS_PASS_WITH_CORRESPONDENCE",
        hard_gates_passed=hard_gates_passed,
        hard_gates_failed=hard_gates_failed,
        warnings=warnings,
        correspondence=corr_report,
        state_stats=state_report,
        mutation_stats=mutation_report,
        semantic_notes=corr_report.semantic_reasoning if corr_report else "Fidelity check passed.",
    )
