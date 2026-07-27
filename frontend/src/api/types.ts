export type ProjectStatus =
  | "created"
  | "parsing"
  | "ready"
  | "translating"
  | "stopped"
  | "done"
  | "error"
  | string;

export type SegmentStatus =
  | "pending"
  | "processing"
  | "done"
  | "error"
  | "reviewed"
  | string;

export interface ProviderConfig {
  model?: string;
  temperature?: number;
  max_concurrency?: number;
  context_budget?: number;
  context_token_budget?: number;
  context_segments?: number;
  generate_chapter_summaries?: boolean;
  stream?: boolean;
  [key: string]: unknown;
}

export interface ProjectStats {
  total?: number;
  pending?: number;
  processing?: number;
  done?: number;
  reviewed?: number;
  error?: number;
  token_in?: number;
  token_out?: number;
}

export interface Project {
  id: number;
  title: string;
  source_lang: string;
  target_lang: string;
  source_type: "epub" | "md" | string;
  status: ProjectStatus;
  provider_cfg: ProviderConfig;
  created_at?: string;
  updated_at?: string;
  total?: number;
  done?: number;
  reviewed?: number;
  error?: number;
  progress?: number;
  stats?: ProjectStats;
}

export interface Chapter {
  id: number;
  project_id?: number;
  ord: number;
  title?: string | null;
  href?: string | null;
  summary?: string | null;
  total?: number;
  done?: number;
}

export interface ProjectDetail extends Project {
  chapters: Chapter[];
}

export interface Segment {
  id: number;
  project_id: number;
  chapter_id: number;
  chapter_title?: string | null;
  ord: number;
  stable_key: string;
  source_text: string;
  target_text?: string | null;
  status: SegmentStatus;
  error_msg?: string | null;
  token_in?: number | null;
  token_out?: number | null;
  provider?: string | null;
  updated_at?: string;
}

export interface SegmentPage {
  items: Segment[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface GlossaryTerm {
  id: number;
  project_id: number;
  source_term: string;
  target_term: string;
  note?: string | null;
  case_sensitive: boolean;
  enabled: boolean;
}

export type QaSeverity = "error" | "warn" | "warning" | "info" | string;

export interface QaIssue {
  id?: number | string;
  segment_id?: number | null;
  stable_key?: string | null;
  severity: QaSeverity;
  code?: string;
  rule?: string;
  type?: string;
  message: string;
  detail?: string;
}

export interface ExportOptions {
  mode: "bilingual" | "target_only";
  include_untranslated: boolean;
  format: "epub" | "md";
}

export interface ExportResult {
  artifact_id: number;
  download_url: string;
  filename: string;
}

export interface StreamEvent {
  type: "progress" | "segment_done" | "chapter_summary" | "error" | string;
  done?: number;
  completed?: number;
  reviewed?: number;
  total?: number;
  current?: number;
  current_segment?: number;
  segment_id?: number;
  id?: number;
  target?: string;
  target_text?: string;
  delta?: string;
  text?: string;
  status?: SegmentStatus;
  message?: string;
  running?: boolean;
  project_status?: string;
  [key: string]: unknown;
}

export interface TaskState {
  project_id: number;
  running: boolean;
  status: string;
  message: string;
}

export interface CostEstimate {
  segments?: number;
  pending_segments?: number;
  token_in?: number;
  token_out?: number;
  estimated_tokens?: number;
  estimated_cost?: number;
  currency?: string;
  model?: string;
  [key: string]: unknown;
}

export interface TranslationMemoryStats {
  entries?: number;
  entry_count?: number;
  total_entries?: number;
  hits?: number;
  hit_count?: number;
  total_hits?: number;
  lookups?: number;
  total_lookups?: number;
  hit_rate?: number;
  reused_segments?: number;
  saved_tokens?: number;
  global_entries?: number;
  language_pair_entries?: number;
  project_segments?: number;
  project_tm_matches?: number;
  reusable_remaining_segments?: number;
  completed_without_provider?: number;
  [key: string]: unknown;
}

export interface PublicSettings {
  app_name: string;
  app_version: string;
  upload_limit_mb: number;
  supported_source_types: string[];
  supported_export_modes: string[];
  provider_defaults: ProviderConfig;
  suggested_models: string[];
  segment_max_chars: number;
  providers: ProviderOption[];
  generation_param_keys: string[];
  prompt_placeholders: string[];
  connection_test_notice: string;
  credential_key_is_ephemeral: boolean;
}

export interface ProviderOption {
  name: string;
  label: string;
  hint: string;
  requires_base_url: boolean;
}

export interface ProviderCredential {
  id: number;
  provider: string;
  profile_label: string;
  configured: boolean;
  masked_key: string;
  enabled: boolean;
  test_status: "untested" | "valid" | "invalid" | string;
  last_tested_at: string | null;
  last_test_error_code: string | null;
  last_test_error_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProviderCredentialInput {
  provider: string;
  profile_label: string;
  api_key: string;
}

export interface ModelProfile {
  id: number;
  display_name: string;
  provider: string;
  litellm_model_id: string;
  credential_id: number | null;
  base_url: string | null;
  enabled: boolean;
  is_default: boolean;
  max_concurrency: number;
  context_window_tokens: number;
  max_output_tokens: number;
  generation_params: Record<string, unknown>;
  input_price_per_million: number | null;
  output_price_per_million: number | null;
  cache_read_price_per_million: number | null;
  cache_write_price_per_million: number | null;
  insecure_transport: boolean;
  created_at: string;
  updated_at: string;
}

export interface ModelProfileInput {
  display_name: string;
  provider: string;
  litellm_model_id: string;
  credential_id?: number | null;
  base_url?: string | null;
  enabled?: boolean;
  is_default?: boolean;
  max_concurrency?: number;
  context_window_tokens?: number;
  max_output_tokens?: number;
  generation_params?: Record<string, unknown>;
}

export interface ConnectionTestResult {
  ok: boolean;
  provider: string;
  model: string;
  tested_at: string;
  error_code: string | null;
  error_summary: string | null;
}

export interface PromptTemplate {
  id: number;
  name: string;
  description: string | null;
  system_prompt: string;
  user_prefix: string | null;
  enabled: boolean;
  is_default: boolean;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface PromptTemplateInput {
  name: string;
  description?: string | null;
  system_prompt: string;
  user_prefix?: string | null;
  enabled?: boolean;
  is_default?: boolean;
}

export interface PromptPreviewInput {
  system_prompt?: string;
  user_prefix?: string | null;
  template_id?: number;
  source_lang: string;
  target_lang: string;
}

export interface PromptPreview {
  rendered: string;
  placeholders: string[];
}

export interface AdminUser {
  id: number;
  username: string;
}

export interface AuthSession {
  authenticated: boolean;
  admin: AdminUser | null;
  idle_expires_at?: string;
  absolute_expires_at?: string;
}

export interface LoginInput {
  username: string;
  password: string;
}

export interface BatchSegmentInput {
  segment_ids: number[];
  action: "mark_reviewed" | "retranslate";
  start_translation?: boolean;
}

export interface TranslateScope {
  retry_errors?: boolean;
  // Limit the run to one chapter and/or an explicit segment selection. Scoped
  // runs are non-destructive: only pending (and, with retry_errors, error)
  // segments in scope are queued; existing translations are preserved.
  chapter_id?: number;
  segment_ids?: number[];
}

export interface UploadProjectInput {
  file: File;
  title?: string;
  sourceLang: string;
  targetLang: string;
  providerCfg?: ProviderConfig;
}
