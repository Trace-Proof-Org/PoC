# Export Manifest

- **Exported**: <ISO-8601 timestamp>
- **Scenario**: <scenario text, or "module-scoped: <module name>">
- **Model files**: export/base.tla, export/base.cfg
- **Knowledge files used**: <list of knowledge/*.md paths>

## Verification (tla-rs)

- **Syntax validation**: PASS | FAIL
- **Model checking**: PASS | FAIL | COUNTEREXAMPLE FOUND
- **Self-repair attempts used**: <n> / <cap>

### If a counterexample was found
<summarize the counterexample trace in plain language — this is a result
for the human, not necessarily a bug in the POC's pipeline>

## Handoff

This model is ready as input to the next phase (Trace Mapper / Conformance
Checker / Ticket Agent) — not implemented in this POC.
