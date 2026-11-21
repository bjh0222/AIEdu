// 仅保留 specs.ts（与 Obsidian 插件等价副本，需 verify_spec_preview_sync.mjs 校验）依赖的类型。
// 其余 1.0 类型已随旧页面移除（备份：工作区 归档/备份/2026-11-13_前端重建前/）。
export interface FormatSpecDeliverable {
  name: string;
  description?: string;
  status: "required" | "optional" | "on_request" | "forbidden";
  primary: boolean;
  order: number;
  allow_override: boolean;
}

export interface FormatSpec {
  id: string;
  name: string;
  description: string;
  scene: string;
  is_builtin: boolean;
  is_default: boolean;
  version: number;
  applicable_tasks: string;
  applicable_intents: string[];
  excluded_scenarios: string[];
  excluded_intents: string[];
  trigger_keywords: string[];
  excluded_keywords: string[];
  allow_keyword_trigger: boolean;
  allow_semantic_recommendation: boolean;
  allow_explicit_selection: boolean;
  target_reader: string;
  output_goal: string;
  deliverables: FormatSpecDeliverable[];
  content_structure: string;
  format_rules: string;
  required_rules: string;
  forbidden_rules: string;
  suggested_rules: string;
  flexible_rules: string;
  acceptance_criteria: string;
  reference_examples: string;
  custom_markdown: string;
  created_at?: string;
  updated_at?: string;
}
