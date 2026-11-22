<!-- 由 scripts/arch_map.py 从代码生成，禁止手改。
     更新：仓库根 services\api\.venv\Scripts\python.exe scripts\arch_map.py -->

# 架构事实（自动生成）


## 路由（208 条 = 路径 × 方法，19 组资源）

### assessments（1 条）

| 方法 | 路径 |
|---|---|
| PATCH | `/api/assessments/{assessment_id}` |

### chat（1 条）

| 方法 | 路径 |
|---|---|
| POST | `/api/chat` |

### criteria（1 条）

| 方法 | 路径 |
|---|---|
| DELETE/PATCH | `/api/criteria/{criterion_id}` |

### export（1 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/export` |

### format-specs（5 条）

| 方法 | 路径 |
|---|---|
| GET/POST | `/api/format-specs` |
| POST | `/api/format-specs/match` |
| DELETE/GET/PUT | `/api/format-specs/{spec_id}` |
| POST | `/api/format-specs/{spec_id}/default` |
| POST | `/api/format-specs/{spec_id}/duplicate` |

### generation-jobs（3 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/generation-jobs/{job_id}` |
| POST | `/api/generation-jobs/{job_id}/cancel` |
| GET | `/api/generation-jobs/{job_id}/events` |

### goals（20 条）

| 方法 | 路径 |
|---|---|
| GET/POST | `/api/goals` |
| POST | `/api/goals/quickstart` |
| DELETE/GET/PATCH | `/api/goals/{goal_id}` |
| GET/POST | `/api/goals/{goal_id}/assessments` |
| GET/POST | `/api/goals/{goal_id}/criteria` |
| POST | `/api/goals/{goal_id}/criteria/confirm-all` |
| POST | `/api/goals/{goal_id}/criteria/manual` |
| POST | `/api/goals/{goal_id}/decision` |
| GET/POST | `/api/goals/{goal_id}/diagnose` |
| POST | `/api/goals/{goal_id}/knowledge-proposals` |
| GET/POST | `/api/goals/{goal_id}/materials` |
| GET/POST | `/api/goals/{goal_id}/plan` |
| GET/POST | `/api/goals/{goal_id}/reflections` |
| GET | `/api/goals/{goal_id}/task-understanding` |
| GET/POST | `/api/goals/{goal_id}/tasks` |
| GET | `/api/goals/{goal_id}/tutorials` |
| POST | `/api/goals/{goal_id}/tutorials/generate` |
| POST | `/api/goals/{goal_id}/tutorials/quickstart` |
| POST | `/api/goals/{goal_id}/unarchive` |
| GET | `/api/goals/{goal_id}/usage` |

### health（1 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/health` |

### import（1 条）

| 方法 | 路径 |
|---|---|
| POST | `/api/import` |

### knowledge（10 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/knowledge` |
| POST | `/api/knowledge/concepts` |
| DELETE/GET | `/api/knowledge/concepts/{concept_id}` |
| POST | `/api/knowledge/dedupe` |
| POST | `/api/knowledge/extract/{tutorial_id}` |
| DELETE/GET | `/api/knowledge/graph` |
| GET | `/api/knowledge/nav` |
| POST | `/api/knowledge/proposals/{proposal_id}/review` |
| POST | `/api/knowledge/relations` |
| PATCH | `/api/knowledge/relations/{relation_id}` |

### materials（2 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/materials/limits` |
| DELETE | `/api/materials/{material_id}` |

### plans（1 条）

| 方法 | 路径 |
|---|---|
| PATCH | `/api/plans/{plan_id}` |

### proposition（33 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/proposition/batches` |
| POST | `/api/proposition/batches/import` |
| DELETE/GET | `/api/proposition/batches/{batch_id}` |
| POST | `/api/proposition/batches/{batch_id}/analyse` |
| GET/PATCH | `/api/proposition/config` |
| POST | `/api/proposition/distill` |
| POST | `/api/proposition/distill-annotations` |
| GET | `/api/proposition/embedding-health` |
| GET | `/api/proposition/frameworks` |
| POST | `/api/proposition/frameworks/import` |
| POST | `/api/proposition/frameworks/{framework_id}/activate` |
| POST | `/api/proposition/guidance/preview` |
| GET | `/api/proposition/guidance/traces` |
| GET | `/api/proposition/item-instances` |
| GET | `/api/proposition/item-models` |
| POST | `/api/proposition/item-models/extract` |
| GET/PATCH | `/api/proposition/item-models/{item_model_id}` |
| POST | `/api/proposition/item-models/{item_model_id}/variations` |
| GET | `/api/proposition/jobs/{job_id}` |
| POST | `/api/proposition/jobs/{job_id}/cancel` |
| GET | `/api/proposition/overview` |
| POST | `/api/proposition/principle-links/{link_id}/review` |
| GET | `/api/proposition/principles` |
| GET | `/api/proposition/principles/injection-preview` |
| PATCH | `/api/proposition/principles/{principle_id}` |
| GET | `/api/proposition/principles/{principle_id}/history` |
| POST | `/api/proposition/principles/{principle_id}/review` |
| GET | `/api/proposition/quality-records` |
| GET | `/api/proposition/questions` |
| DELETE/GET | `/api/proposition/questions/{question_id}` |
| GET | `/api/proposition/questions/{question_id}/assessment` |
| POST | `/api/proposition/questions/{question_id}/feedback` |
| POST | `/api/proposition/questions/{question_id}/retry` |

### reflections（1 条）

| 方法 | 路径 |
|---|---|
| PATCH | `/api/reflections/{reflection_id}` |

### settings（11 条）

| 方法 | 路径 |
|---|---|
| GET/POST | `/api/settings/models` |
| POST | `/api/settings/models/activate` |
| POST | `/api/settings/models/discover` |
| POST | `/api/settings/models/evaluate/{version_id}` |
| GET/PUT | `/api/settings/models/evaluation` |
| PUT | `/api/settings/models/roles` |
| POST | `/api/settings/models/test` |
| PUT | `/api/settings/models/user-profile` |
| GET | `/api/settings/models/user-profile-suggestion` |
| POST | `/api/settings/models/user-profile-suggestion/dismiss` |
| DELETE | `/api/settings/models/{profile_id}` |

### tasks（2 条）

| 方法 | 路径 |
|---|---|
| PATCH | `/api/tasks/{task_id}` |
| GET/POST | `/api/tasks/{task_id}/attempts` |

### training（9 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/training/export` |
| POST | `/api/training/feedback` |
| GET | `/api/training/feedback-evidence` |
| POST | `/api/training/feedback-evidence/recover` |
| POST | `/api/training/feedback-evidence/revaluations/{request_id}/cancel` |
| POST | `/api/training/feedback-evidence/{event_id}/retract` |
| POST | `/api/training/feedback-evidence/{event_id}/review-request` |
| GET | `/api/training/records` |
| GET | `/api/training/stats` |

### tutorials（11 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/tutorials/{tutorial_id}` |
| POST | `/api/tutorials/{tutorial_id}/delivery` |
| GET | `/api/tutorials/{tutorial_id}/export` |
| GET | `/api/tutorials/{tutorial_id}/export/cheatsheet` |
| GET | `/api/tutorials/{tutorial_id}/export/exercises` |
| GET | `/api/tutorials/{tutorial_id}/generation-jobs` |
| POST | `/api/tutorials/{tutorial_id}/quality/optimize` |
| POST | `/api/tutorials/{tutorial_id}/restore/{version}` |
| POST | `/api/tutorials/{tutorial_id}/sections/generate` |
| GET | `/api/tutorials/{tutorial_id}/sections/{section_id}` |
| POST | `/api/tutorials/{tutorial_id}/sections/{section_id}/regenerate` |

### v2（68 条）

| 方法 | 路径 |
|---|---|
| GET | `/api/v2/actions/receipt` |
| POST | `/api/v2/artifacts/{artifact_id}/export-sets` |
| GET | `/api/v2/artifacts/{artifact_id}/versions/{version_id}` |
| POST | `/api/v2/benchmarks/controlled` |
| GET | `/api/v2/benchmarks/reports/{report_hash}` |
| POST | `/api/v2/benchmarks/sealed/{dataset_hash}/consume` |
| POST | `/api/v2/benchmarks/semantic-build` |
| GET | `/api/v2/capabilities` |
| POST | `/api/v2/chat` |
| GET | `/api/v2/evaluation-frameworks` |
| POST | `/api/v2/evaluation-frameworks/math/install` |
| GET | `/api/v2/export-sets/{bundle_id}/files/{file_id}` |
| POST | `/api/v2/findings/{finding_id}/evidence` |
| POST | `/api/v2/findings/{finding_id}/repairs` |
| POST | `/api/v2/findings/{finding_id}/user-reviews` |
| POST | `/api/v2/format-spec-imports` |
| GET/POST | `/api/v2/format-specs` |
| PUT | `/api/v2/format-specs/{spec_id}` |
| GET | `/api/v2/import-understanding` |
| POST | `/api/v2/knowledge/asset-imports` |
| GET | `/api/v2/knowledge/assets/{kind}` |
| POST | `/api/v2/knowledge/batches/import` |
| GET | `/api/v2/knowledge/cards` |
| PUT | `/api/v2/knowledge/principles/{principle_id}` |
| POST | `/api/v2/knowledge/questions/{question_id}/feedback` |
| POST | `/api/v2/learning-feedback` |
| GET | `/api/v2/learning-profile` |
| GET | `/api/v2/legacy-assets/{kind}` |
| GET | `/api/v2/legacy-compatibility` |
| POST | `/api/v2/managed-resource-actions` |
| POST | `/api/v2/managed-resource-cleanup` |
| GET | `/api/v2/managed-resources` |
| GET | `/api/v2/managed-resources/{kind}` |
| GET | `/api/v2/managed-resources/{kind}/{object_id}/impact` |
| GET | `/api/v2/managed-understanding-context/{profile_id}` |
| GET/PUT | `/api/v2/model-settings` |
| POST | `/api/v2/optimization/policy` |
| POST | `/api/v2/optimization/runs` |
| GET | `/api/v2/quality-profiles` |
| GET | `/api/v2/research/{run_id}` |
| GET | `/api/v2/research/{run_id}/artifacts/{artifact_key}` |
| GET | `/api/v2/research/{run_id}/rubric` |
| POST | `/api/v2/runs/{run_id}/pause` |
| POST | `/api/v2/runs/{run_id}/resume` |
| POST | `/api/v2/runs/{run_id}/unknown-quarantines` |
| POST | `/api/v2/session` |
| POST | `/api/v2/source-verifications` |
| POST | `/api/v2/spec-packages/preview` |
| POST | `/api/v2/spec-previews` |
| GET | `/api/v2/storage/report` |
| GET | `/api/v2/subject-understanding` |
| POST | `/api/v2/task-starts` |
| GET/POST | `/api/v2/tasks` |
| POST | `/api/v2/tasks/{task_id}/card-extractions` |
| POST | `/api/v2/tasks/{task_id}/confirm` |
| PATCH | `/api/v2/tasks/{task_id}/input` |
| GET | `/api/v2/tasks/{task_id}/knowledge-graph` |
| POST | `/api/v2/tasks/{task_id}/materials` |
| POST | `/api/v2/tasks/{task_id}/runs` |
| GET | `/api/v2/tasks/{task_id}/runs/{run_id}/manifest` |
| GET | `/api/v2/tasks/{task_id}/snapshot` |
| POST | `/api/v2/tasks/{task_id}/syntheses` |
| GET | `/api/v2/tasks/{task_id}/synthesis-source` |
| GET | `/api/v2/tasks/{task_id}/versions` |
| GET | `/api/v2/tasks/{task_id}/versions/{version_id}/cards` |
| PUT | `/api/v2/tasks/{task_id}/versions/{version_id}/cards/{card_id}` |
| GET | `/api/v2/tasks/{task_id}/versions/{version_id}/synthesis` |
| GET | `/api/v2/verifier-capabilities` |

## 数据表（38 张）

| 表 | 列 |
|---|---|
| `artifact_export_sets` | bundle_id, artifact_id, version_id, kind, state, request_hash, action_key, renderer_version, options_hash, manifest_json, manifest_hash, cache_relpath |
| `concepts` | id, domain_key, title, title_norm, aliases, domain_keys, goal_ids, definition, status, source_refs, last_reviewed_at, created_at, updated_at |
| `design_principles` | id, domain_key, statement, principle_type, scope_knowledge_points, positive_refs, negative_refs, evidence_count, status, rationale, reviewed_at, version, title, mechanism, applicability, counterexamples, guidance, scope, confidence, approval_source, framework_id, evidence_family_count, created_at, updated_at |
| `evaluation_frameworks` | id, title, domain_key, version, source_text, source_hash, standards, source_map, status, extraction_method, warnings, created_at, updated_at |
| `format_specs` | id, name, description, scene, is_builtin, is_default, version, applicable_tasks, applicable_intents, excluded_scenarios, excluded_intents, trigger_keywords, excluded_keywords, allow_keyword_trigger, allow_semantic_recommendation, allow_explicit_selection, target_reader, output_goal, deliverables, content_structure, format_rules, required_rules, forbidden_rules, suggested_rules, flexible_rules, acceptance_criteria, reference_examples, custom_markdown, created_at, updated_at |
| `generation_call_dispatches` | call_id, root_job_id, run_id, unit_id, epoch, attempt, idempotency_key, request_hash, input_hash, manifest_json, request_json, provider, model_family, model, status, outcome, supersedes_call_id, receipt_json, usage_json, price_json, provider_request_id, result_blob_hash, started_at, finished_at |
| `item_models` | id, domain_key, title, chapter, knowledge_points, radicals, incidentals, solution_template, answer_expr, difficulty, provenance, status, source_ref, human_note, created_at, updated_at |
| `knowledge_proposals` | id, proposal_type, payload, rationale, evidence_refs, status, reviewed_at, created_at, updated_at |
| `learning_goals` | id, title, domain_key, branch, motivation, application_context, current_level, time_budget, hard_constraints, difficulty_mode, difficulty_note, capability_level, generate_extra_exercises, generation_tier, format_spec_id, format_spec_source, format_spec_match_note, format_spec_snapshot, status, user_notes, archived, completed, completed_at, input_revision, task_state_json, creation_key, request_hash, created_at, updated_at |
| `migration_delta_log` | txn_seq, event_id, entity_type, entity_id, operation, before_json, after_json, schema_version, applied_key |
| `model_call_logs` | id, feature, provider, model, goal_id, tutorial_id, job_id, call_id, run_id, unit_id, attempt, started_at, finished_at, duration_ms, outcome, latency_ms, status, error_category, prompt_tokens, completion_tokens, total_tokens, usage_source, created_at |
| `proposition_batches` | id, filename, domain_key, branch, learner, source_text, source_hash, status, created_at, updated_at |
| `proposition_config` | id, settings_json |
| `proposition_embeddings` | id, source_id, source_type, revision, model_key, vector, created_at |
| `proposition_guidance_traces` | id, goal_id, tutorial_id, section_id, task_kind, context, result, created_at |
| `quality_records` | id, domain_key, goal_id, tutorial_id, version, source, code, paper_title, question_number, motif, detail, severity, question_text, created_at, source_key, motif_key, motif_full |
| `sources` | id, title, url, source_type, publisher, reliability_note, accessed_at |
| `training_records` | id, source_type, feature, goal_id, tutorial_id, section_id, system_prompt, user_prompt, output_text, model_provider, model_name, prompt_tokens, completion_tokens, total_tokens, usage_source, rating, tags, comment, context_json, created_at |
| `chat_messages` | id, goal_id, tutorial_id, section_id, task_id, role, content, created_at |
| `concept_relations` | id, from_concept_id, to_concept_id, relation_type, status, rationale, source_refs, evidence_refs, confidence, origin, history, created_at, updated_at |
| `design_principle_links` | id, source_id, target_id, relation, rationale, status, created_at |
| `design_principle_revisions` | id, principle_id, version, snapshot, action, actor, rationale, created_at |
| `generation_jobs` | id, goal_id, job_type, status, progress, stage, error_code, error_message, retry_count, prompt_tokens, completion_tokens, total_tokens, usage_source, run_id, parent_job_id, root_job_id, state_revision, cancel_epoch, worker_instance_id, lease_until, action_key, request_hash, state_json, created_at, updated_at |
| `goal_assessments` | id, goal_id, benefits, prerequisites, alternatives, assumptions, uncertainties, recommendation, user_decision, decision_reason, created_at, updated_at |
| `goal_materials` | id, goal_id, kind, filename, stored_name, ext, content_type, size, status, error_message, extract_status, text_excerpt, char_count, source_char_count, note, raw_hash, extracted_hash, extractor_version, manifest_json, created_at, updated_at |
| `item_instances` | id, item_model_id, incidental_values, stem, answer, variation_type, verified_by, verified_at, source_tutorial_id, created_at, updated_at |
| `learning_plans` | id, goal_id, version, status, generated_at, user_edits, created_at, updated_at |
| `proposition_analysis_jobs` | id, batch_id, framework_id, status, progress, counts, question_ids, force, error_message, created_at, updated_at |
| `proposition_questions` | id, batch_id, item_model_id, domain_key, branch, learner, source_key, family_key, raw_text, raw_stem, raw_notes, raw_answer, raw_explanation, external_evaluation, external_source, paper_title, question_number, source_line_start, source_line_end, source_map, knowledge_points, question_type, parse_status, analysis_status, error_message, created_at, updated_at |
| `reflections` | id, goal_id, summary, learned_capabilities, unresolved_gaps, next_actions, evidence_refs, created_at |
| `plan_steps` | id, plan_id, order_index, title, objective, status, dependencies, estimated_effort, completion_evidence_refs, created_at, updated_at |
| `proposition_evaluations` | id, question_id, framework_id, framework_version, analysis_version, fingerprint, blind_solution, report, status, demo, model, error_message, created_at, updated_at |
| `acceptance_criteria` | id, goal_id, plan_step_id, statement, conditions, evidence_type, pass_rule, status, rationale, created_at, updated_at |
| `tutorials` | id, goal_id, plan_step_id, title, status, current_version_id, delivery_status, delivery_path, delivery_version, created_at, updated_at |
| `practice_tasks` | id, goal_id, plan_step_id, tutorial_id, title, instructions, task_type, expected_artifact, validation_rule, status, created_at, updated_at |
| `tutorial_versions` | id, tutorial_id, version, content_json, prompt_version, model_provider, model_name, quality_report, source_refs, prompt_tokens, completion_tokens, total_tokens, usage_source, created_at, parent_version_id, run_id, content_hash, quality_hash, schema_version, operation_id |
| `assessments` | id, goal_id, task_id, assessment_type, rubric, result, confidence, evidence_refs, weak_points, created_at |
| `attempts` | id, task_id, artifact, artifact_type, result, errors, help_level, user_explanation, feedback, created_at |

### app/api/ — 路由层（23 个）

`chat`、`deps`、`format_specs`、`goals`、`health`、`io`、`knowledge`、`materials`、`model_settings`、`practice`、`proposition`、`proposition_knowledge`、`router`、`training`、`tutorials`、`v2`、`v2_assets`、`v2_import_profiles`、`v2_insights`、`v2_management`、`v2_optimization`、`v2_research`、`v2_synthesis`

### app/services/ — 业务层（37 个）

`chat`、`evaluation`、`exam_check`、`exam_verify`、`export`、`feedback_evidence`、`feedback_ledger`、`format_specs`、`generation_acceptance`、`generation_contract`、`generation_runtime`、`goals`、`knowledge`、`materials`、`pipeline`、`practice`、`practice_check`、`proposition`、`proposition_evidence`、`proposition_generated`、`proposition_knowledge`、`proposition_mechanisms`、`proposition_retrieval`、`proposition_rubric`、`proposition_symbolic_runner`、`quality_findings`、`quote_check`、`reference_documents`、`spec_check`、`synthesis`、`task_strategy`、`task_understanding`、`tool_verify`、`training`、`tutorials`、`usage`、`utils`

### app/learning/ — 学习引擎（6 个）

`demo`、`disciplines`、`engine`、`generation_tiers`、`levels`、`teaching_profile`

### app/llm/ — 模型调用（4 个）

`context_layers`、`prompts`、`provider`、`service`

## 前端页面（9 个）

- `/legacy/[kind]`
- `/legacy`
- `/library`
- `/new`
- `/`
- `/settings`
- `/tasks/[id]`
- `/tasks/[id]/quality`
- `/tasks/[id]/read`
