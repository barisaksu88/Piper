### Blunt Advice Summary

*Original advice from GLM. Annotated against current state.*

---

**Architecture Strengths**

- **Graph World Model:** ✓ HELD — Node-edge structure in `memory/world_model.py` is intact. Not downgraded.
- **Transient State:** ✓ HELD — Separation of transient (expiring) vs durable memory is live and enforced.
- **Verification Doctrine:** ✓ HELD — "Tool results matter, narration is not authority" is codified in AGENTS.md and enforced by VerificationEngine. Do not weaken.

---

**Codebase Risks**

- **Hardcoded Paths:** ⚠ OPEN — `config.py` still contains machine-specific absolute paths. Not yet moved to `config.yaml` / `.env`. Still breaks on any machine other than the dev machine.
- **Regex Parsing:** ⚠ PARTIALLY ADDRESSED — Typed schema validation (§13.5) and `file_stage_kind` (§13.6) reduced regex surface area at LLM output boundaries. Regex accumulation in `file_stage_policy.py` is the remaining known debt (§13.6 open work). The hook registry (§13.7) will further reduce it. Not fully resolved.

---

**Roadmap Advice — All Resolved**

- **Phase 2 StateResolutionEngine latency trap:** ✓ DONE — Confidence-aware fast path live in `route_normalizer.py` (2026-03-20). High-confidence routes bypass LLM disambiguation.
- **Phase 3 StateMutationEngine schema validation:** ✓ DONE — Strict typed validation enforced. Missing fields rejected at boundary.
- **Phase 4 VerificationEngine contract before FileWorkEngine:** ✓ DONE — Built in correct order. VerificationEngine (Phase 4) completed before FileWorkEngine (Phase 5).
- **Planner Boundary as Python contract:** ✓ DONE — `PlannerBoundary.validate_input()` enforces allowed tools. LLM cannot use tools outside the stage card's `allowed_tools` list.

---

**Execution Style**

- **Ruthless Deletion:** ✓ STILL APPLIES — No `_v0` suffixes or commented-out backup blocks allowed. Trust git history.
