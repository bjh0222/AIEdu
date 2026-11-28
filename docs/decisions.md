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

## ADR-2026-11-22-02: Fail-closed core quality and delivery contracts

Status: accepted.

Context: domain aliases and mixed-domain unit bindings could execute
mathematics-specific checks in the wrong place; historical unknown calls could
hold the global queue after their run stopped; generation used a per-unit
early-check probe followed by a second full C; calibration had no clustered
sample gate or certificate identity; research retrieval and model output
capacity used fixed fallback values that obscured evidence and provider limits.

Decision:

- Domain scope is canonicalized at the frozen check-plan, producer and consumer
  boundaries. A unit's own subject binding wins over unrelated task domains.
  Difficulty review runs only for `math_logic` units with items and produces a
  complete, bound `not_applicable` report otherwise.
- Only `authorized` calls on a still queued/running root occupy provider work.
  Failed/paused/interrupted runs retain call receipts for audit but do not block
  new runs. `quarantined` is terminal; late provider receipts append an
  independent audit and never rewrite the original outcome or billing fields.
  Resume requires a separate explicit isolation action.
- A configured run executes one complete C stage after generation. New
  mathematics item plans freeze a derivation contract: `Q1/Q2 -> C3`,
  `Q3 -> C4`, and `Q4 -> G3`. Source rows are reduced before the final quality
  reducer, and all three generic dispatch entries exclude derived targets.
  Old and non-mathematics plans keep independent checks.
- New C4 plans carry `requires_nonempty_hard_constraints`. A task with no
  frozen hard constraints records a bound `not_applicable` result without a
  model request; nonempty constraints still execute. Historical plans without
  the new scope are not retrofitted.
- Explicit artifact roles constrain automatic scene selection. `tutorial`
  proposals canonicalize to `knowledge_tutorial`, and an explicit tutorial-only
  task cannot be redirected to the exercise template by a negated phrase such
  as "do not create extra exercises." Output-design mode must agree with the
  requested artifact class.
- A quality-target identity is verified server-side from the frozen profile,
  rule and criterion hashes. The model receives the semantic target only; a
  metadata-only `binding` clause is not represented as learner-content evidence.
- A complete A response whose only execution fault is rejected JSON
  serialization may receive one separately billed correction call. The original
  failed receipt remains failed. Length failures, HTTP failures, unknown calls
  and missing semantic content are not repaired or replayed.
- Calibration uses a fixed-seed, 2,000-iteration `source_group` cluster
  bootstrap but separately requires at least 16 clusters, 35 expert positives
  and 9 expert negatives. `calibration-certificate.v1` binds scope, implementation
  and protocol hashes, plan and criterion identities, prompts, model/tool
  versions and a real anchor; any drift yields `stale`.
- Final export consumes a single `delivery-decision.v1`. Only C3 and G2 may be
  listed as soft items; structure, coverage, materials, evidence boundaries,
  binding and hard checks cannot be waived. A certificate must cover every
  unwaived claim class. A waiver is created only by an explicit user action and
  is stored separately from quality/calibration. Revocation is persistent and
  never auto-recreated. Export正文, file names and version-level quality fields
  remain unchanged; authorization is recorded only in manifest/audit data.
- Public research is requested separately from evidence obtained. Index or
  fulltext insufficiency freezes an explicit `local_materials_only` degradation
  with attempted indexes, failures and citation boundaries, then continues with
  selected local materials. Query/schema/privacy contract errors still fail.
- A owns the task-specific integration decision. `integration_plan` records
  whether cross-chapter synthesis, part summaries or a whole-tutorial summary
  are warranted, with each section bound to exactly one `book_level` unit whose
  `depends_on_titles` equal its `covers_titles` and whose position follows the
  covered units. B writes each section from the actual generated upstream
  content after the parallel chapter pass; the wrapper never invents a summary
  or applies a template heading. A whole-tutorial summary may consume earlier
  cross-chapter/part-summary units and must cover every non-summary unit. A
  missing or schema/contract-invalid arrangement gets at most one explicit A
  correction request, not a wrapper-generated substitute.
- Provider and check concurrency is a global, user-configurable setting bounded
  to 1-5 (default 5), persisted through the model-settings CAS and exposed in the
  settings UI. Independent unit checks, L4 findings and independent research
  scope reviews may run in parallel up to that bound. Dependency edges,
  perturbation pairing, reducers and evidence binding remain ordered; no
  parallel worker retries an existing unknown or ignores a failure.
- Model output limits use the registered model capacity. A profile
  `output_limit_tokens` may only reduce that capacity; null means no additional
  application cap. Per-call budget hints are planning metadata and never become
  an internal cap. A settled length failure at the active ceiling stops with
  `OUTPUT_CAPACITY_EXHAUSTED` instead of re-billing the identical request;
  raising a historical ceiling keeps successful ancestral results reusable.
  Unknown capacity refuses dispatch and the actual wire parameter plus effective
  reserve enter request identity.
- When A's own scene proposal resolves an equivalent domain profile, quality
  target refs may be deterministically rebound only if every source ref matched
  the pre-A profile and the target has exactly one equivalent rule. The rebind
  is persisted in the plan and A audit.
- Chapter entities requested as tutorials are projected under the requested
  `tutorial` role; chapter is an internal composition role, not a missing
  required artifact.

Consequences:

- New plans intentionally receive new check-plan and quality-protocol hashes.
  Historical plans, findings, unknown calls and exports keep their identities.
- Hard failures remain failures. Provider HTTP 5xx errors are not retried,
  resumed or converted into success.
- A valid technical validation may still end as an explicitly uncalibrated
  draft. Professional truth, measured difficulty and learning benefit remain
  unclaimed.

Verification:

- Backend full regression passed with 2,400 tests and no failures. Contracts
  (98 schemas / 69 routes), architecture regeneration, spec-preview sync,
  TypeScript, ESLint and the production web build also pass.
- Workbench unknown/quarantine, delivery-waiver, settings and responsive browser
  regressions pass on an isolated Next port.
- A real DeepSeek run completed A+B+C and exported a readable two-chapter draft
  with zero missing required artifacts. All 25 calls settled successfully,
  C4 was model-free `not_applicable`, and one bounded criterion-contract
  correction completed. The saved DeepSeek review executed with isolated input;
  it is recorded as `same_family`, while cross-family independence remains
  `not_run/MODEL_FAMILY_NOT_INDEPENDENT`.
- Final delivery remained blocked because model observations found real
  alignment, assessment and evidence-boundary defects and no calibration
  certificate exists. The final export correctly returned HTTP 422; this
  technical completion is not a professional or final-delivery claim.
- Earlier GLM HTTP 502 runs and later failed DeepSeek/C4 or JSON-contract runs
  retain their original failed receipts and were not resumed or replayed.

## ADR-2026-11-27-01: Bind A0 strategy, A1 blueprint, B deviations and repair directives

Status: accepted.

Context: A research, detailed planning, B execution deviations and C repair
guidance were present in separate fragments. A did not persist one strategy
that research queries and screening both consumed; B could generate without a
machine-readable blueprint execution status; C optimization packages did not
require one directive per confirmed finding; candidates were bound to a package
but not to concrete repair directives; and candidate rechecks still ran the full
semantic scope.

Decision:

- A configured new run accepts the user research switch as permission, then
  calls A0 once to decide research necessity. A0 records user requirements,
  autonomous additions, directions, public search strategy, source selection,
  evidence extraction and stop conditions. This strategy is the only input to
  research queries and screening.
- A1 receives the frozen A0 strategy and actual research receipts. It returns
  model-authored Evidence records and a versioned blueprint. The wrapper binds
  decision evidence, arrangement titles, source identities, strategy hash,
  evidence hash, arrangement hash and context hash before B can run.
- B consumes the blueprint and returns `blueprint_execution`. Local low-risk
  deviations are permitted only for non-`must_follow` decisions. Unknown
  decisions, core or medium/high-risk changes, or an unexecutable blueprint stop
  the run with an explicit human-decision failure.
- C checks frozen blueprint decisions through existing criterion rows. New
  blueprint optimization packages require one RepairDirective per confirmed
  finding. Directive modes are limited to executable `whole_item`, `whole_unit`
  and `related_units` modes; deterministic assertions are limited to
  `maximum_codepoints` and `required_literals`.
- Directive-bearing candidates must map every directive at the top level and
  each changed unit. Unknown, missing, duplicate or target-inconsistent mappings
  are rejected. Package `directives`, not a new candidate schema version,
  distinguishes new and legacy behavior.
- A RepairLedger binds finding, directive, candidate, recheck and lifecycle
  status. Unchanged checks are reused only with the same plan and input identity;
  deterministic tests run first; semantic checks rerun only for changed units
  and their dependency closure. Missing dependency edges fall back to full
  semantic recheck.
- Final delivery follows the 2026-11-26 ruling: completed tasks use the frozen
  policy's non-disabled required checks. A missing optional calibration
  certificate does not manufacture a hard blocker and never changes
  `not_calibrated` or professional wording into a pass.

Consequences:

- New runs carry A0, A1 blueprint, B execution and C directive identities in
  plan, version and quality audit data. Historical runs without these fields
  remain readable under their original contracts.
- New prompt and quality protocol hashes intentionally differ. A0, A1, directive
  planning and scope-aware recheck add bounded calls or reduce recheck calls
  according to the frozen execution policy.
- Scope-aware recheck is an engineering control, not proof of professional
  correctness. Unsupported or unavailable evidence remains undecidable.

Verification:

- `tests/test_s11_research_blueprint.py` covers A0/A1 separation, disabled
  strategy, Evidence binding, blueprint hashes, B deviation boundaries and C
  blueprint rows.
- `tests/test_s11_repair_directives.py` covers executable modes, deterministic
  assertion whitelists, directive coverage, candidate mappings, deterministic
  first, scope merge and ledger lifecycle.
- Existing A/B/C, optimization, delivery, contract and frontend regressions must
  remain green before live generation and export.

## ADR-2026-11-27-02: Layer-scoped repair and fail-closed actionable findings

Status: implemented and verified in the working tree.

Context: The three quality layers existed as check annotations, but several
downstream paths still consumed the legacy unlayered report shape. A disabled
layer could reappear in criterion requests, scope-aware rechecks, candidate
completion or optimization acceptance. A failed check without a located
confirmed finding could also fall through to an unrelated teaching optimization.

Decision:

- `quality_layer_for()` classifies every row exactly. New or unrecognized rows
  default to `quality_specific`; they never default to the user-disabled
  completeness layer.
- A quality finding is actionable only when it is confirmed, belongs to an
  enabled layer, binds to the frozen plan and carries a unit/version/hash/span
  location. A violated enabled check without such a finding stops before any
  C→B provider dispatch with `ACTIONABLE_FINDING_REQUIRED`.
- Enabled checks that are incomplete stop with `QUALITY_CHECK_INCOMPLETE`.
  When every enabled required check is satisfied or honestly not applicable,
  optimization is skipped with `optimization_not_needed`; the delivery gate then
  decides final eligibility from the frozen policy.
- Disabled findings remain audit evidence but are filtered by the reducer,
  scope-aware recheck and candidate completion. They cannot create a blocking
  reason, regression, directive or repair attempt.
- Derived checks (for example C3 from Q1/Q2) may be covered by a source-check
  finding, but an unbound derived violation cannot start a repair.
- A and imported-profile observations are bound to aggregate plan checks so a
  violated observation cannot remain a diagnostic-only result. Q5 difficulty
  observations become plan checks and findings, not a permanent `not_run` row.
- New configured tasks freeze a default repair policy of at most two attempts
  per finding and two rounds per run. This is a bounded ceiling, not a claim that
  two rounds are sufficient for every task.
- Material `source` mappings expand to all chunks of that source; explicit chunk
  mappings define a task subset. Manifest byte/hash/offset integrity remains
  mandatory and unmapped chunks are recorded as out of scope.

Consequences:

- A correctness-only task can final after its enabled required checks pass, even
  though completeness and quality-specific rows remain honest `not_applicable`
  policy skips. It cannot silently consume quality-specific criteria.
- Existing unlayered runs keep their legacy policy behavior. New runs carry the
  exact frozen `quality_layers` into both check compilation and recheck.
- A C failure is either actionable or explicitly blocked; it is no longer
  converted into a generic teaching rewrite.

Verification:

- Backend full regression: `2563 passed, 133 warnings` after a clean isolated
  test database. Focused layer/optimization/assessment/material tests were run
  separately during implementation.
- `scripts/generate_v2_contracts.py --check` reports `100 schemas / 72 routes`;
  frontend production build, TypeScript, targeted ESLint and spec preview sync
  pass.
- Real instance verification reloaded 8010/3001 and validated the new file
  removal and absent legacy quality-loop entry. The fresh quality restart did
  not reach final: one run stopped on incomplete required correctness checks,
  and the following run was explicitly stopped by provider HTTP 429. Those
  failures remain recorded; no failed run was retried or signed final.

## ADR-2026-11-28-01: Lossless repair of provider wire-shape and immutable package metadata

Status: implemented and verified; live comparison ended without final delivery.

Context:

DeepSeek Flash produced complete semantic observations that differed from the
wire contract in narrow, deterministic ways: an extra nested closer, flat
criterion clause rows, a directive layer copied incorrectly from the finding,
missing frozen optimization metadata, and planner notes mixed into the
executable directive package. Treating all of these as unparseable output
prevented valid findings and repairs from reaching the bounded next stage.

Decision:

- Recover only complete JSON values. An extra nested closer is removed one
  token at a time only outside strings and only when the resulting full JSON
  parses. Flat clause rows are grouped by request ID without changing result,
  reason or evidence. Missing fields, duplicate identities, truncation and
  semantic changes remain failures.
- A directive layer is metadata of its confirmed finding. If the model labels
  it differently, normalize it to the finding's frozen `quality_layer` and
  record the previous value. The target, required change and acceptance
  assertion are not rewritten.
- Q6 `support=undecidable` may cite the exact source catalog and literal-tool
  non-coverage as a complete unknown boundary. This never becomes satisfied
  and does not relax other Q6 clauses.
- Free-form `optimizations` and `unverified_hypotheses` are archived in
  `non_executable_planner_notes` when directives are present. B receives only
  executable directives.
- Only an omitted frozen skill identity or empty `scope.unit_ids` may be
  injected from request metadata or the frozen plan. Present-but-wrong values
  remain hard failures.

Consequences:

- The provider raw bytes, receipts and original failure outcomes remain
  immutable; normalization is separately audited.
- The live DeepSeek comparison reached a real B candidate and change-scope
  recheck, but the candidate was rejected and the second package exhausted the
  configured two-attempt budget before another B dispatch. No final was signed.
- The second real package passes offline validation after metadata injection,
  but that code path was not live-rerun after the attempt budget was exhausted.

Verification:

- Focused provider, criterion and repair-directive tests: 124 passed.
- Backend full suite: 2572 passed, 133 warnings.
- Frontend TypeScript, targeted ESLint, isolated production build and
  integration-entry 21/21 passed.
- Contract generation reports 100 schemas / 72 routes; spec preview sync passes.

## ADR-2026-11-28-02: Correctness-only execution with scoped A/B evidence

Status: implemented; one isolated real run reached final eligibility.

Context:

The prior layered suite still ran Criterion assessment and broad semantic
checks after a user selected only correctness. It also exposed the full task
arrangement to every B unit, omitted the actual blueprint from B's request,
and could not complete repair because finding/check method names, dependency
scope and protocol manifests disagreed. Long external sources also exceeded
the A1 provider capacity, and repeated wrapper validation rejected
`output_design` metadata that the wrapper itself had added.

Decision:

- New web tasks freeze `correctness_only.v1`, explicitly set
  `criterion_assessment=false`, and compile only `Q.item_correctness`.
  Requests without `quality_layers` retain `full_v1` compatibility.
- The correctness checker evaluates only `stem_markdown`, `answer.final` and
  `answer.reasoning`. A located error creates a RepairDirective; B repairs
  within the target unit; the changed questions are rechecked. A successful
  target recheck stops with `correctness_only_completed`, while exhaustion
  stops with `correctness_only_repair_failed`.
- C report directives and the B repair package share the same canonical
  directive identity. Candidate validation enforces directive coverage,
  allowed target ordinals, item count, unrelated-item preservation and target
  field changes.
- B receives only the current unit's blueprint structure, relevant decisions
  and evidence. Repair B receives the same scoped projection for all directive
  target units instead of the complete plan.
- A1 research input uses exact query-centered source excerpts with original
  source identity, byte/character location, full-text length and hashes.
  Research discovery is never embedded wholesale into A1.
- The full quality implementation is frozen under
  `services/api/quality_suite_archive/full_v1` with a recomputed SHA-256
  manifest and the source commit. Runtime selection is isolated in
  `app/v2/quality_suite`.

Consequences:

- Correctness-only final means every generated item was reviewed and no item
  was marked incorrect. It does not claim professional certification,
  measured difficulty, learning gain or completion of the archived full suite.
- Failed and unknown historical calls remain immutable. New deterministic
  normalizations are recorded separately and do not rewrite provider bytes.
- The reader distinguishes a new question start from continuation paragraphs
  and options, so the three-line inter-question rhythm no longer creates a
  large blank between a stem and its A/B/C/D options.

Verification:

- Isolated real run `46b8630d-78df-4b9c-9477-86e9ddb36b73` reached
  `quality_profile=correctness_only.v1`, one plan item `Q.item_correctness`,
  six reviewed items, no findings, `tqi_verdict=pass`,
  `calibration_status=not_calibrated` and `final_allowed=true`.
- The version binds one A1 blueprint with 3 evidence records and 7 decisions,
  6 coupled judgment/choice items, empty method overview after an audited
  blueprint-based suppression, and no Criterion call.
- Earlier failed live runs from the same validation series remain recorded for
  quantity scope, A0 query limits, A1 capacity, nested evidence aliases,
  derived-design idempotency and knowledge-chain endpoint normalization.
