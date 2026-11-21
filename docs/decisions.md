# Architecture Decisions

## ADR-2026-11-21-01: Bind structured A targets to B and C

Status: accepted.

Context: versioned A and C skills were present, but A quality targets, knowledge
links, misconception boundaries and question blueprints could remain descriptive
JSON. B could generate without a complete executable plan, and C could return a
broad G1-G4 result without one observation per frozen A row.

Decision:

- `teaching-arrangement.v5` keeps readable `quality_targets` and adds
  `quality_target_refs` containing exact `profile_id`, `profile_version`,
  `profile_hash`, `rule_id`, `rule_version` and `criterion_hash`.
- The server derives the domain-rule criterion hash from the same code used by
  check-plan compilation, rejects missing/duplicate/unbound references, and
  requires complete coverage of the supplied quality profiles before B runs.
- Item-bearing units require a nonempty question blueprint whose positive
  quantities equal the unit quantity and whose forms agree with
  `output_design.question_form`.
- Knowledge links use a closed relation enum and must bind endpoints to frozen
  concepts/prerequisites and evidence to a supplied material/import identity or
  an explicit `unknown:` reason.
- Misconceptions driving item units bind to a blueprint group.
- C emits one independent request per quality target, knowledge link,
  misconception, blueprint and acceptance criterion. Missing rows remain
  undecidable.
- `assessment-review.v2` is injected in full and recorded as a C manifest entry
  across criterion review/correction, item review, import/research rubric,
  difficulty, synthesis, optimization and acceptance.
- The global generation prompt is not marked consumed merely because planning
  finished. A state-level receipt records actual successful B calls, request
  hashes and unit coverage.

Consequences:

- Existing frozen plans remain readable; enforcement applies when a new or
  explicitly re-planned arrangement is bound with context.
- More A/C output is required for complex tasks, but unsupported or malformed
  targets fail before contradictory B content is generated.
- Model observations remain engineering observations, not professional
  calibration or a final-quality certificate.

Verification:

- `tests/test_structured_a_contract.py` covers reference/hash binding, profile
  coverage, duplicate rejection, blueprint quantity/form, relation/evidence
  binding, misconception binding, one C row per A target and a synthetic C
  execution matrix across all 21 domain packs.
- Targeted regression suites cover every C consumer plus A/B receipt wiring.
