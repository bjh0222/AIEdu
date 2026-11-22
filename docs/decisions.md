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

## ADR-2026-11-22-01: Deterministic review contracts before bounded concurrency

Status: accepted.

Context: Quality consumers repeatedly received valid semantic judgments in
avoidable wire shapes: string booleans/enums, empty numeric placeholders,
scalar evidence, and long hand-written quotes that were ambiguous or escaped.
Those failures consumed corrective calls even when the provider had completed
the intended review. Applicability was also distributed across several
ad-hoc checks, and the API did not expose whether a saved draft was waiting on
quality work or blocked by it.

Decision:

- The server builds a bounded catalog of short, unique evidence spans and gives
  each span a stable `evidence_id`. A model may select that ID; the server then
  binds the exact field, quote and span hash. Legacy field/quote observations
  remain readable when the catalog cannot represent the evidence.
- A pure normalizer handles only unambiguous wire shape: boolean/result aliases,
  applicability key aliases, empty null markers and evidence containers. It
  never changes a semantic verdict, invents a count, or turns malformed
  evidence into an abstention.
- A zero-model matrix decides execution per frozen check, unit, scene, role,
  domain, item presence and question form before quality workers start.
- New runs freeze versions and implementation-file hashes for the review skill,
  evidence catalog, normalizer, applicability matrix and correction protocols.
  Material protocol drift stops the run instead of mixing contracts.
- Bounded generation/review concurrency still flows through the existing
  provider-capacity, lease, cancellation and unknown-call gates. The former
  request-level guards and receipts remain authoritative.
- Run snapshots expose `draft_ready`, `quality_checking`, `quality_blocked` and
  `final_ready`, plus token, price-missing, stage latency and retry-source
  summaries. `final_ready` uses the same strict calibration and profile-binding
  requirements as final export.

Consequences:

- New evidence-ID requests have a new request identity and cannot reuse a
  historical quote-shaped receipt. Old receipts remain immutable and readable.
- More compact evidence reduces escape/ambiguity failures, but semantic quality
  still comes from the model observation and existing deterministic adapters.
- Existing paused runs adopt the complete current protocol manifest on their
  explicit resume child; later code drift blocks such a run instead of silently
  changing its quality contract.
- The four delivery states do not change QualityVerdict or make demo,
  uncalibrated, blocked or repaired content final.

Verification:

- `tests/test_review_contract.py` covers catalog uniqueness, evidence-ID
  hydration, structural normalization, applicability, freeze drift, four-state
  delivery and unknown token/price accounting.
- Criterion and research contract tests bind server evidence IDs to exact spans.
- Contract generation/check, architecture regeneration, targeted A/B/C,
  criterion, difficulty, research, optimization and snapshot regressions must
  pass before live task resumption.
