"""
Non-Vacuity & Implementation Correspondence Gatekeeper.

Protects TraceProof from LLM reward hacking, vacuous passes, trivial invariants,
unreachable state machines, and unfaithful specifications.

Implements the 5 Hard Gates and Structural Implementation Correspondence Layer for Phase 5 verification:
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


class InconclusiveMutationError(VacuityError):
    """Raised when a mutation check hits exploration limits or terminates unexpectedly."""
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
    coverage_available: bool = True


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
    and public entry points from any Python source file, directory of files, or
    list of source paths without hardcoded domain lists.
    """

    def __init__(self, source_path: Path | str | List[Path | str], in_scope_scenario: Optional[str] = None) -> None:
        self.raw_source = source_path
        self.in_scope_scenario = in_scope_scenario
        self.paths: List[Path] = []

        if isinstance(source_path, (list, tuple)):
            raw_list = list(source_path)
        else:
            raw_list = [source_path]

        for p_item in raw_list:
            if not p_item:
                continue
            p = Path(p_item)
            if p.is_dir():
                py_files = sorted([
                    f for f in p.glob("*.py")
                    if not f.name.startswith("test_") and not f.name.endswith("_test.py") and not f.name.startswith("__")
                ])
                self.paths.extend(py_files)
            elif p.is_file():
                self.paths.append(p)
            elif p.exists():
                self.paths.append(p)

    def extract(self) -> PythonSourceTokens:
        if not self.paths:
            raise ImplementationCorrespondenceError(
                f"[CORRESPONDENCE REJECT: ImplementationCorrespondenceError] Target source path '{self.raw_source}' "
                f"does not exist or contains no valid Python source files. Verification fails closed."
            )

        global_vars: Dict[str, str] = {}
        class_attributes: List[str] = []
        public_functions: List[str] = []
        global_mutated_vars: Set[str] = set()
        temporal_events: List[str] = []
        action_sources: Dict[str, str] = {}

        for path in self.paths:
            source_code = path.read_text(encoding="utf-8")
            tree = ast.parse(source_code, filename=str(path))

            # 1. Module-level variables & classes
            for node in tree.body:
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
                    if node.name not in public_functions:
                        public_functions.append(node.name)
                    action_sources[node.name] = node.name

                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, ast.FunctionDef):
                            if not item.name.startswith("_"):
                                if item.name not in public_functions:
                                    public_functions.append(item.name)
                                action_sources[item.name] = f"{node.name}.{item.name}"
                            for subnode in ast.walk(item):
                                if isinstance(subnode, ast.Attribute) and isinstance(subnode.value, ast.Name) and subnode.value.id == "self":
                                    if subnode.attr not in class_attributes and not subnode.attr.startswith("__"):
                                        class_attributes.append(subnode.attr)

            # 2. Track which variables are explicitly mutated across functions
            for node in ast.walk(tree):
                if isinstance(node, ast.Global):
                    global_mutated_vars.update(node.names)

            # 3. Detect temporal transitions / timeouts / delays in code generically
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    if func_name in ("sleep", "wait", "timeout"):
                        # Code uses temporal delays / timeouts
                        for var in list(class_attributes) + list(global_vars.keys()):
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

        # Concurrency state primitives: global variables mutated in code + class instance attributes
        concurrency_primitives: List[str] = list(class_attributes)
        for gvar in global_vars:
            if gvar in global_mutated_vars or any(t in gvar.lower() for t in ("lock", "owner", "expiry", "lease", "worker", "storage", "queue", "buffer", "count", "state")):
                if gvar not in concurrency_primitives:
                    concurrency_primitives.append(gvar)

        # 4. Core actions: scenario-in-scope functions + temporal events
        core_actions = list(public_functions)
        for te in temporal_events:
            if te.lower() not in [a.lower() for a in core_actions]:
                core_actions.append(te)

        target_display = ", ".join(str(p) for p in self.paths) if self.paths else str(self.raw_source)
        return PythonSourceTokens(
            target_path=target_display,
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


def extract_cfg_invariants(cfg_text: str) -> List[str]:
    """Extracts all invariant names declared in a TLC configuration file."""
    invariants: List[str] = []
    # Match lines like INVARIANT InvName
    for m in re.finditer(r"^\s*INVARIANT\s+([A-Za-z0-9_]+)", cfg_text, re.MULTILINE):
        inv = m.group(1).strip()
        if inv not in invariants:
            invariants.append(inv)
    # Match INVARIANTS block
    m_block = re.search(r"^\s*INVARIANTS?\b(.*)", cfg_text, re.MULTILINE | re.DOTALL)
    if m_block:
        lines = m_block.group(1).splitlines()
        for line in lines:
            line = re.sub(r"\\\*.*$", "", line).strip()
            line = re.sub(r"#.*$", "", line).strip()
            if not line:
                continue
            if re.match(r"^[A-Z]+\b", line) and not line.startswith("INVARIANT"):
                break  # Reached another keyword like SPECIFICATION, INIT, NEXT, PROPERTY, etc.
            tokens = [t.strip() for t in line.split() if t.strip() and t.strip() != "INVARIANT"]
            for token in tokens:
                if re.match(r"^[A-Za-z0-9_]+$", token) and token not in invariants:
                    invariants.append(token)
    return invariants


def check_ast_tautology(
    tla_text: str,
    cfg_text: str,
    allow_constant_spec: bool = False,
) -> Tuple[str, List[str]]:
    """
    Gate 1: Rejects trivial TRUE, 1=1, x=x, and variable-free invariants across ALL declared invariants.
    Returns (primary_invariant_name, referenced_variables).
    """
    invariants = extract_cfg_invariants(cfg_text)
    if not invariants:
        raise MissingInvariantConfigError("No INVARIANT declared in configuration file (.cfg).")

    declared_vars = extract_declared_variables(tla_text)
    first_result: Optional[Tuple[str, List[str]]] = None

    for inv_name in invariants:
        # Extract invariant body
        body = extract_invariant_body(tla_text, inv_name)
        if not body:
            raise MissingInvariantConfigError(
                f"INVARIANT '{inv_name}' declared in config is not defined in TLA+ specification."
            )

        # Check for literal TRUE / tautological constants
        normalized = body.replace(" ", "")
        if normalized in ("TRUE", "TRUE/\\TRUE", "TRUE\\/TRUE", "1=1", "0=0"):
            raise VacuousInvariantError(
                f"Invariant '{inv_name}' is defined as trivial literal TRUE/tautology: {body!r}"
            )

        # Check for reflexive identity: A = A (e.g. x = x, w = w)
        reflexive_match = re.match(r"^([A-Za-z0-9_]+)\s*=\s*\1$", body.strip())
        if reflexive_match:
            raise VacuousInvariantError(
                f"Invariant '{inv_name}' is defined as a reflexive tautology: {body!r}"
            )

        # Check variable references against VARIABLES
        referenced = [v for v in declared_vars if re.search(rf"\b{re.escape(v)}\b", body)]

        if len(referenced) == 0 and not allow_constant_spec:
            raise VariableFreeInvariantError(
                f"[VACUITY REJECT: VariableFreeInvariantError] Invariant '{inv_name}' references 0 state variables "
                f"from VARIABLES ({declared_vars}). State invariants must constrain system state transitions. "
                f"(Body: {body!r}). Override only via human CLI flag --allow-constant-spec or TRACEPROOF_ALLOW_CONSTANT_SPEC=1."
            )

        if first_result is None:
            first_result = (inv_name, referenced)

    return first_result or (invariants[0], [])


# ==============================================================================
# Gate 2: Configuration Completeness
# ==============================================================================

def check_config_completeness(tla_text: str, cfg_text: str) -> str:
    """
    Gate 2: Enforces INVARIANT line presence and matching definition for all declared invariants.
    """
    invariants = extract_cfg_invariants(cfg_text)
    if not invariants:
        raise MissingInvariantConfigError("No INVARIANT declaration found in configuration (.cfg).")

    for inv_name in invariants:
        body = extract_invariant_body(tla_text, inv_name)
        if not body:
            raise MissingInvariantConfigError(
                f"Config references INVARIANT '{inv_name}', but operator is not defined in TLA+ spec."
            )
    return invariants[0]


# ==============================================================================
# Gate 3: ImplementationCorrespondenceGuard
# ==============================================================================

def _canonical_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]", "", name).lower()


def _split_words(name: str) -> List[str]:
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\W|$)|\d+", name) if w]


def _generic_name_match(py_name: str, tla_name: str) -> bool:
    """
    Strict structural identifier matcher:
    1. Exact canonical equality (e.g. 'do_work' == 'DoWork', 'current_holder' == 'CurrentHolder').
    2. Normalized token sequence equality (e.g. ['do', 'work'] == ['do', 'work']).
    3. Prefix/suffix qualifying entity match when one contains the other (e.g. 'acquire_lock' == 'Acquire' if entity is 'lock'),
       while strictly rejecting loose single-token overlap (e.g. 'worker' does not match 'work' or 'do_work').
    4. Disallows cross-matching of opposing concurrency verbs.
    """
    s_canon = _canonical_name(py_name)
    t_canon = _canonical_name(tla_name)
    if s_canon == t_canon:
        return True

    py_words = _split_words(py_name)
    tla_words = _split_words(tla_name)

    # Disjoint semantic verbs that must never cross-match
    disjoint_pairs = [
        ({"acquire"}, {"release", "unlock"}),
        ({"enqueue", "push"}, {"dequeue", "pop"}),
        ({"expire", "expiry", "timeout"}, {"acquire", "release"}),
    ]
    s_py = set(py_words)
    s_tla = set(tla_words)
    for grp_a, grp_b in disjoint_pairs:
        if (s_py & grp_a and s_tla & grp_b) or (s_py & grp_b and s_tla & grp_a):
            return False

    if (s_canon == "lock" and t_canon == "unlock") or (s_canon == "unlock" and t_canon == "lock"):
        return False

    # Exact token sequence match
    if py_words == tla_words:
        return True

    # Compound containment: e.g. acquire_lock vs acquire, or do_work vs work
    # Only if the non-overlapping token is an auxiliary qualifier (do, op, action, run, handle)
    # or the entity name itself, AND the primary verb matches.
    aux_qualifiers = {"do", "op", "action", "run", "handle", "fn", "method", "helper"}
    py_meaningful = [w for w in py_words if w not in aux_qualifiers]
    tla_meaningful = [w for w in tla_words if w not in aux_qualifiers]

    if py_meaningful and tla_meaningful and py_meaningful == tla_meaningful:
        return True

    # Check if one is a qualified version of the other, e.g. ['acquire', 'lock'] and ['acquire']
    # Require at least one full non-trivial token match AND the remaining token must be an entity noun,
    # never a loose single-word overlap of unrelated concepts.
    generic_words = {"worker", "work", "state", "val", "data", "item", "var", "client", "node", "process"}
    if len(py_words) > 1 and len(tla_words) == 1:
        if tla_words[0] in py_words and tla_words[0] not in generic_words:
            if tla_words[0] == py_words[0] or tla_words[0] == py_words[-1]:
                return True
    elif len(tla_words) > 1 and len(py_words) == 1:
        if py_words[0] in tla_words and py_words[0] not in generic_words:
            if py_words[0] == tla_words[0] or py_words[0] == tla_words[-1]:
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


DEFAULT_EXCLUDED_VAR_PATTERNS = {
    "violations_detected",
    "assertion_failures",
    "test_counter",
    "debug_log",
    "metrics",
    "telemetry",
}


def _extract_excluded_vars(
    tla_text: str,
    cfg_text: str,
    user_excluded: Optional[List[str]] = None,
) -> Set[str]:
    """
    Extracts explicitly excluded variable names from:
    1. TLA+ comments: \\* @exclude_vars: var1, var2 or \\* EXCLUDE_VARS: ...
    2. Config comments/directives: # @exclude_vars: ...
    3. User-supplied exclusion list
    4. Default documented test harness instrumentation counters
    """
    excluded = set(DEFAULT_EXCLUDED_VAR_PATTERNS)
    if user_excluded:
        excluded.update(v.lower().strip() for v in user_excluded)

    # Search TLA+ comments for \* @exclude_vars: ... or \* EXCLUDE_VARS: ...
    for m in re.finditer(r"\\\*\s*@?exclude_vars?\s*:\s*([^\n]+)", tla_text, re.IGNORECASE):
        for var in m.group(1).split(","):
            var_clean = var.strip().lower()
            if var_clean:
                excluded.add(var_clean)

    for m in re.finditer(r"#\s*@?exclude_vars?\s*:\s*([^\n]+)", cfg_text, re.IGNORECASE):
        for var in m.group(1).split(","):
            var_clean = var.strip().lower()
            if var_clean:
                excluded.add(var_clean)

    return excluded


def check_implementation_correspondence(
    target_source: str | Path | List[str | Path],
    tla_text: str,
    cfg_text: str,
    in_scope_scenario: Optional[str] = None,
    excluded_vars: Optional[List[str]] = None,
) -> CorrespondenceReport:
    """
    Gate 3: Matches Python AST primitives against TLA+ VARIABLES and Next actions.
    Treats missing required state variables and missing core actions as hard rejections.
    Supports documented variable exclusion via '\\* @exclude_vars: var1, var2' in TLA+
    or 'Excluded variables:' in run-config.md.
    """
    extractor = PythonSourceExtractor(target_source, in_scope_scenario=in_scope_scenario)
    tokens = extractor.extract()

    if not extractor.paths:
        raise ImplementationCorrespondenceError(
            f"[CORRESPONDENCE REJECT] Target source path '{target_source}' does not exist or contains no valid Python files. "
            f"Verification fails closed to guarantee model faithfulness."
        )

    path_display = tokens.target_path
    corr_warnings: List[str] = []
    if not tokens.concurrency_primitives:
        corr_warnings.append(
            f"No concurrency primitives (locks, threading, shared mutable state) detected in Python target '{path_display}'. "
            f"Implementation correspondence check will only validate public entry points."
        )

    declared_tla_vars = extract_declared_variables(tla_text)
    tla_actions = extract_tla_actions(tla_text)

    # Resolve excluded state variables (documented exclusion mechanism)
    effective_excluded_vars = _extract_excluded_vars(tla_text, cfg_text, user_excluded=excluded_vars)

    required_vars = [
        prim for prim in tokens.concurrency_primitives
        if prim.lower() not in effective_excluded_vars
    ]

    # Match variables
    matched_vars: Dict[str, str] = {}
    missing_vars: List[str] = []

    for prim in required_vars:
        match = None
        for orig in declared_tla_vars:
            if _generic_name_match(prim, orig):
                match = orig
                break
        if match:
            matched_vars[prim] = match
        else:
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

    # Hard Gate: If an in-scope required state variable is completely omitted
    if missing_vars:
        diag_lines = []
        for v in missing_vars:
            expected_tla = "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", v))
            diag_lines.append(f"Source: '{v}' → Missing TLA+ variable: '{expected_tla}'")
        diag_str = "\n  • ".join(diag_lines)
        raise ImplementationCorrespondenceError(
            f"[CORRESPONDENCE REJECT] In-scope state variable(s) from Python implementation {path_display} "
            f"are missing in TLA+ VARIABLES declaration:\n  • {diag_str}\n"
            f"(Declared TLA+ variables: {declared_tla_vars}; To exclude test/instrumentation variables, "
            f"use '\\* @exclude_vars: {', '.join(missing_vars)}' in the spec or 'Excluded variables:' in run-config.md)"
        )

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
            f"[CORRESPONDENCE REJECT] In-scope action(s) from Python implementation {path_display} "
            f"are missing in TLA+ Next operator:\n  • {diag_str}\n(Modeled actions in Next: {tla_actions})"
        )

    total_expected = len(tokens.core_actions) + len(required_vars)
    total_matched = len(matched_actions) + len(matched_vars)
    confidence = round(min(1.0, total_matched / max(1, total_expected)), 2)

    reasoning = (
        f"Verified implementation correspondence for target {path_display}: "
        f"Matched {len(matched_vars)}/{len(required_vars)} state primitives "
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

def _extract_per_action_evidence(
    tlc_stats: Optional[Dict[str, Any]],
    raw_output: str,
    all_expected_actions: Set[str],
) -> Optional[Dict[str, int]]:
    """
    Extracts per-action transition counts if the model checker emitted
    per-action exploration evidence. Returns None if per-action coverage is unavailable.
    """
    evidence: Dict[str, int] = {}

    # 1. Check structured tlc_stats["actions"]
    if tlc_stats and isinstance(tlc_stats, dict):
        actions_list = tlc_stats.get("actions", [])
        if isinstance(actions_list, list) and actions_list:
            # Only treat as per-action evidence if it breaks down actions beyond aggregate "Next" / "Init"
            sub_actions = [
                a for a in actions_list
                if isinstance(a, dict) and a.get("name") and a.get("name") not in ("Next", "Init", None)
            ]
            if sub_actions:
                for a in actions_list:
                    if isinstance(a, dict) and "name" in a:
                        name = str(a["name"])
                        trans = int(a.get("transitions", a.get("states", a.get("distinct_states", 0))))
                        evidence[name.lower()] = trans
                return evidence

    # 2. Check raw_output for explicit transition / coverage reports
    # Pattern 2a: Explicit summary line e.g. "Transitions: Acquire, Release" or "Coverage: Acquire(10), Release(5)"
    m_trans_line = re.search(r"(?:Transitions|Coverage|Exercised actions?):\s*([^\n]+)", raw_output, re.IGNORECASE)
    if m_trans_line:
        line_content = m_trans_line.group(1)
        found_any = False
        for act in all_expected_actions:
            if re.search(rf"\b{re.escape(act)}\b", line_content, re.IGNORECASE):
                evidence[act.lower()] = 1
                found_any = True
        if found_any:
            return evidence

    # Pattern 2b: Java TLC action profiling / coverage lines
    # e.g. "Action <line ...> Acquire has generated 12 states" or "Acquire: 12 states"
    found_tlc_profiling = False
    for line in raw_output.splitlines():
        for act in all_expected_actions:
            m_act = re.search(
                rf"(?:Action\s+.*?|\b){re.escape(act)}\b.*?(?:has generated|:)\s*(\d+)\s*(?:states|transitions)",
                line,
                re.IGNORECASE,
            )
            if m_act:
                count = int(m_act.group(1))
                evidence[act.lower()] = count
                found_tlc_profiling = True
    if found_tlc_profiling:
        return evidence

    return None


def check_state_exploration(
    tlc_stats: Optional[Dict[str, Any]],
    raw_output: str,
    core_actions: List[str],
    auxiliary_actions: Optional[List[str]] = None,
) -> StateExplorationReport:
    """
    Gate 4: Enforces distinct states > 1, transitions > 0.
    If per-action exploration evidence is available, enforces 100% core action firing
    and emits diagnostic warnings for auxiliary actions.
    If per-action evidence is unavailable from the model checker, reports coverage
    as unavailable rather than falsely claiming coverage.
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

    # 2. Per-Action Exploration Evidence Check
    all_expected = set(core_actions) | set(auxiliary_actions or [])
    evidence = _extract_per_action_evidence(tlc_stats, raw_output, all_expected)

    if evidence is None:
        # Per-action coverage evidence is unavailable from backend (e.g. tla-rs aggregate Next)
        warnings = [
            "Per-action coverage evidence is unavailable from model checker (only aggregate transitions reported)."
        ]
        return StateExplorationReport(
            distinct_states=distinct_states,
            transitions=transitions,
            exercised_core_actions=[],
            dead_core_actions=[],
            warnings=warnings,
            coverage_available=False,
        )

    exercised_core: List[str] = []
    dead_core: List[str] = []
    warnings: List[str] = []

    for act in core_actions:
        trans_count = evidence.get(act.lower(), 0)
        if trans_count > 0:
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
            if evidence.get(aux.lower(), 0) == 0:
                warnings.append(f"Auxiliary action '{aux}' was not exercised during model checking.")

    return StateExplorationReport(
        distinct_states=distinct_states,
        transitions=transitions,
        exercised_core_actions=exercised_core,
        dead_core_actions=[],
        warnings=warnings,
        coverage_available=True,
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
    inv_name: str | List[str],
    client: Any,
    max_states: int = 500,
    max_depth: int = 20,
    max_seconds: int = 30,
) -> MutationReport:
    """
    Gate 5:
    Check 5A: Invariant Negation (~Inv across transitions) must trigger TLC counterexample at trace depth >= 1.
    Check 5B: Applicable Guard Mutation Sweep must yield 100% kill rate on actions that mutate invariant state.
              Unconditional actions (no guards) are explicitly marked NOT_APPLICABLE.
    """
    inv_names = [inv_name] if isinstance(inv_name, str) else list(inv_name)

    all_survivors: List[str] = []
    all_not_applicable: List[str] = []
    total_mutants_killed = 0
    total_mutants_generated = 0
    min_negation_depth = 999
    negation_killed_all = True

    for current_inv in inv_names:
        inv_body = extract_invariant_body(tla_text, current_inv)
        if not inv_body:
            raise MissingInvariantConfigError(f"Cannot find invariant '{current_inv}' definition for mutation testing.")

        # --------------------------------------------------------------------------
        # Check 5A: Invariant Negation (~Inv across transitions)
        # --------------------------------------------------------------------------
        has_paramless_init = bool(re.search(r"^\s*Init\s*==", tla_text, re.MULTILINE))
        if has_paramless_init:
            negated_body = f"Init \\/ ~({inv_body})"
        else:
            negated_body = f"~({inv_body})"

        negated_inv_name = f"MutantNegated_{current_inv}"
        negated_tla = (
            tla_text.rstrip().removesuffix("====").rstrip()
            + f"\n\n{negated_inv_name} == {negated_body}\n===="
        )
        negated_cfg = re.sub(
            rf"\bINVARIANT\s+{re.escape(current_inv)}\b",
            f"INVARIANT {negated_inv_name}",
            cfg_text,
        )

        res_neg = client.call("check", {
            "spec": negated_tla,
            "config": negated_cfg,
            "max_states": max_states,
            "max_depth": max_depth,
            "max_seconds": max_seconds,
        })

        neg_status = res_neg.get("status", "")
        if neg_status in ("limit", "limit_reached"):
            raise InconclusiveMutationError(
                f"[MUTATION INCONCLUSIVE] Invariant negation mutant for '{current_inv}' reached exploration limit "
                f"({max_states} states). Cannot conclusively verify non-vacuity. Raise TLA_RS_MAX_STATES."
            )
        if neg_status == "error":
            raise InconclusiveMutationError(
                f"[MUTATION ERROR] Invariant negation mutant for '{current_inv}' encountered checker error: {res_neg.get('raw', '')}"
            )

        neg_raw = res_neg.get("raw", "")
        neg_killed = (
            neg_status == "counterexample"
            or "invariant_violation" in neg_raw.lower()
            or "violation" in neg_raw.lower()
            or "violated" in neg_raw.lower()
        )

        if not neg_killed:
            raise MutationSurvivorError(
                f"[MUTATION REJECT: InvariantNegationSurvivor] Invariant negation mutant for '{current_inv}' was NOT "
                f"killed by TLC (TLC reported {neg_status}). The invariant is unbounded or uncoupled from system state."
            )

        ce_trace = res_neg.get("counterexample")
        if ce_trace is None:
            ce_trace = res_neg.get("trace")

        if ce_trace is not None and isinstance(ce_trace, list):
            depth = max(0, len(ce_trace) - 1)
            if depth < 1:
                raise MutationSurvivorError(
                    f"[MUTATION REJECT: InvariantNegationSurvivor] Invariant negation mutant for '{current_inv}' counterexample "
                    f"did not reach depth >= 1 (transitions={depth}). A non-vacuous invariant must constrain transitions beyond the initial state."
                )
        else:
            depth = res_neg.get("stats", {}).get("transitions", 1)

        min_negation_depth = min(min_negation_depth, depth)

        # --------------------------------------------------------------------------
        # Check 5B: Applicable Guard Mutation Sweep
        # --------------------------------------------------------------------------
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
                all_not_applicable.append(f"{act}: NOT_APPLICABLE (UNCONDITIONAL_ACTION - no precondition guard to weaken)")
                continue

            applicable_testable_mutants += 1
            total_mutants_generated += 1

            mutated_tla = (
                tla_text[:act_match.start()]
                + prefix
                + mutated_action_body
                + tla_text[act_match.end():]
            )

            res_mut = client.call("check", {
                "spec": mutated_tla,
                "config": cfg_text,
                "max_states": max_states,
                "max_depth": max_depth,
                "max_seconds": max_seconds,
            })

            mut_status = res_mut.get("status", "")
            if mut_status in ("limit", "limit_reached"):
                raise InconclusiveMutationError(
                    f"[MUTATION INCONCLUSIVE] Guard mutation for action '{act}' reached exploration limit ({max_states} states). "
                    f"Cannot determine if mutant survives. Raise exploration limits."
                )
            if mut_status == "error":
                raise InconclusiveMutationError(
                    f"[MUTATION ERROR] Guard mutation for action '{act}' failed with checker error: {res_mut.get('raw', '')}"
                )

            mut_raw = res_mut.get("raw", "")
            is_killed = (
                mut_status == "counterexample"
                or "invariant_violation" in mut_raw.lower()
                or "violation" in mut_raw.lower()
                or "violated" in mut_raw.lower()
            )

            if is_killed:
                total_mutants_killed += 1
            else:
                all_survivors.append(f"{act} (against invariant '{current_inv}')")

    kill_rate = round(total_mutants_killed / max(1, total_mutants_generated), 2) if total_mutants_generated > 0 else 1.0

    if all_survivors:
        survivor_names = ", ".join(all_survivors)
        raise MutationSurvivorError(
            f"[MUTATION REJECT: GuardMutationSurvivor] {len(all_survivors)} of {total_mutants_generated} applicable action guard mutant(s) "
            f"survived TLC model checking (Kill Rate: {int(kill_rate * 100)}%, Survivors: [{survivor_names}]). "
            f"Every applicable action guard must be constrained by the invariant (100% kill rate required)."
        )

    return MutationReport(
        negation_killed=negation_killed_all,
        negation_depth=min_negation_depth if min_negation_depth != 999 else 1,
        applicable_mutants_generated=total_mutants_generated,
        applicable_mutants_killed=total_mutants_killed,
        kill_rate=kill_rate,
        survivors=all_survivors,
        not_applicable=all_not_applicable,
    )


# ==============================================================================
# Unified Vacuity Gatekeeper Orchestrator
# ==============================================================================

def evaluate_spec_vacuity(
    tla_text: str,
    cfg_text: str,
    target_source: Optional[str | Path | List[str | Path]] = None,
    client: Any = None,
    tlc_stats: Optional[Dict[str, Any]] = None,
    tlc_raw_output: str = "",
    allow_constant_spec: bool = False,
    in_scope_scenario: Optional[str] = None,
    excluded_vars: Optional[List[str]] = None,
    max_states: int = 500,
    max_depth: int = 20,
    max_seconds: int = 30,
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
                target_source, tla_text, cfg_text, in_scope_scenario=in_scope_scenario, excluded_vars=excluded_vars
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
        if state_report.coverage_available:
            action_summary = f"100% Core Actions Exercised: {state_report.exercised_core_actions}"
        else:
            action_summary = "Per-action coverage: unavailable from backend"
        hard_gates_passed.append(
            f"State Exploration & Action Coverage (Distinct states: {state_report.distinct_states}, "
            f"Transitions: {state_report.transitions}; {action_summary})"
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
    if client is None:
        return VacuityReport(
            passed=False,
            verdict="INCOMPLETE_VERIFICATION (UNCHECKED_MUTATIONS)",
            hard_gates_passed=hard_gates_passed,
            hard_gates_failed=["Gate 5 Skipped: Model checker client not provided"],
            warnings=warnings + ["Dual mutation check (Gate 5) could not be run because no checker client was provided."],
            correspondence=corr_report,
            state_stats=state_report,
            mutation_stats=None,
            semantic_notes=corr_report.semantic_reasoning if corr_report else "",
            diagnostic_details="Model checker client is required to execute Gate 5 dual mutation verification.",
        )

    mutation_report: Optional[MutationReport] = None
    try:
        all_cfg_invariants = extract_cfg_invariants(cfg_text)
        mutation_report = check_dual_mutation(
            tla_text=tla_text,
            cfg_text=cfg_text,
            core_actions=core_actions,
            inv_name=all_cfg_invariants,
            client=client,
            max_states=max_states,
            max_depth=max_depth,
            max_seconds=max_seconds,
        )
        na_suffix = f"; {len(mutation_report.not_applicable)} action(s) marked NOT_APPLICABLE (unconditional)" if mutation_report.not_applicable else ""
        hard_gates_passed.append(
            f"Dual Mutation Verification (Negation killed at depth {mutation_report.negation_depth}; "
            f"Applicable Guard Sweep Kill Rate: {mutation_report.kill_rate * 100}%{na_suffix})"
        )
    except InconclusiveMutationError as e:
        hard_gates_failed.append(f"Gate 5 Inconclusive: {e}")
        return VacuityReport(
            passed=False,
            verdict="VERIFIER_FAIL (INCONCLUSIVE_VERIFICATION)",
            hard_gates_passed=hard_gates_passed,
            hard_gates_failed=hard_gates_failed,
            warnings=warnings,
            correspondence=corr_report,
            state_stats=state_report,
            mutation_stats=None,
            semantic_notes=corr_report.semantic_reasoning if corr_report else "",
            diagnostic_details=str(e),
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
        semantic_notes=corr_report.semantic_reasoning if corr_report else "Structural correspondence check passed.",
    )
