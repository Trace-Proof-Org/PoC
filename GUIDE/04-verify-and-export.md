# Phase 4 — Verify & Export

## Goal

Verify `model/base.tla` using the tla-rs MCP server, resolve any syntax
errors through a capped self-repair loop, and only then export the model to
`export/`. Nothing reaches `export/` without a passing verification in the
same run.

## Steps

1. **Read the tool contract.** See `references/05-mcp-tla-rs.md` for the
   exact tla-rs MCP tools available and how to call them — this guide
   assumes you've read that.

2. **Validate first (cheap, fast check).** Call tla-rs's syntax/parse check
   on `model/base.tla`. This is the "does it even parse" gate before
   spending time on model checking.

3. **Self-repair loop (capped).** If validation fails:
   - Feed the tla-rs error output back to the model-generation step (strong
     model) along with the specific `knowledge/*.md` facts it was derived
     from, and ask for a targeted fix — not a full regeneration.
   - Re-validate.
   - Repeat up to a fixed cap (default 3 attempts; make this configurable in
     `run-config.md`, not hardcoded per-call).
   - If the cap is hit without a passing validation, **stop** — write the
     failure and the last error to `model/generation-log.md`, and report to
     the user that this needs a human. Do not export a failing model, and do
     not keep looping past the cap.

4. **Model-check (if validation passes).** Call tla-rs's model-checking tool
   against `model/base.cfg` bounds. Record the result (pass, or
   counterexample) in `export/manifest.md` regardless of outcome — a
   counterexample here is itself useful signal for the user, not just a
   pipeline failure. If a mundane bug in the *model itself* (not the target
   system) is found, treat it like a validation failure and route through
   the same capped self-repair loop; if the counterexample looks like a
   real system property being violated, stop and report it — that's a
   result for the human, not something to auto-"fix" away.

5. **Export.** Copy the passing `model/base.tla` and `model/base.cfg` into
   `export/`, and write `export/manifest.md` from
   `references/templates/export-manifest.template.md`: include the
   scenario, the knowledge files used, verification result, self-repair
   attempts taken (if any), and a timestamp.

## Handoff note (out of scope from here)

`export/base.tla` + `export/manifest.md` is the contract this POC hands off
to the next phase (Trace Mapper / Conformance Checker / Ticket Agent), which
a teammate owns. Don't build anything past writing these export files.

## Done when

- `export/` contains a tla-rs-verified model and a manifest describing how
  it got there, OR
- The run stopped cleanly at the self-repair cap with a clear message and
  nothing was written to `export/`.
