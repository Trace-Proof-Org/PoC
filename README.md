# TraceProof PoC

Code + docs -> TLA+ spec generation -> model checking.

## Structure
- agents/spec_generator  - drafts PlusCal from code/docs (Person A)
- agents/repair_loop     - lint + capped self-repair loop (Person A)
- agents/model_checker   - TLC integration + result parsing (Person B)
- harness                - end-to-end pipeline runner (Person B)
- validation             - ground-truth comparison, seeded-bug tests (Person B + joint)
- shared                 - schemas/config/LLM client used by both sides (joint, define first)

## Setup
1. Agree on shared/schemas.py and docs/interfaces.md first.
2. Each branch builds against stubs/mocks of the other side's stage.
3. harness/pipeline.py is the integration point - wire it last.
