export type WorkspaceMode = "home" | "clarifying" | "running" | "ready" | "error";

export type ChatRole = "user" | "assistant" | "system";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
};

export type Question = {
  key: "target" | "competitors" | "focus" | "purpose" | string;
  question: string;
  options: string[];
  multi: boolean;
  suggested: string[];
  allow_custom: boolean;
};

export type RunMeta = {
  target_product: string;
  competitors: string[];
  analysis_focus: string[];
  analysis_purpose: string;
  user_input: string;
};

export type RunSettings = {
  domain_key: string;
  reviewer_mode: "minimal" | "full";
  use_mock: boolean;
  enable_live: boolean;
  demo_loop: boolean;
};

export type DomainConfig = {
  name?: string;
  target_product?: string;
  competitors?: string[];
  analysis_focus?: string[];
  analysis_purpose?: string;
};

export type DomainsResponse = {
  domains: Record<string, DomainConfig>;
  products: string[];
};

export type IntakeResponse = {
  mode: "direct" | "clarify";
  meta: RunMeta | null;
  questions: Question[];
};

export type RunEvent = {
  event: string;
  data: Record<string, unknown>;
  timestamp?: string;
};

export type Evidence = {
  evidence_id: string;
  product?: string;
  claim_type?: string;
  claim?: string;
  extracted_snippet?: string;
  source_url?: string;
  source_bias?: string;
  collection_source?: string;
  source_reliability?: number;
  claim_relevance?: number;
  evidence_confidence?: number;
  observed_at?: string;
};

export type QualityReport = {
  quality_score?: number;
  mode?: string;
  passed_rules?: string[];
  warning_rules?: string[];
  failed_rules?: string[];
  errors?: Array<Record<string, unknown>>;
  warnings?: Array<Record<string, unknown>>;
  module_status?: Record<string, string>;
};

export type FinalState = {
  status?: string;
  raw_evidence?: Evidence[];
  schema_draft?: Record<string, unknown>;
  report_draft?: string;
  quality_report?: QualityReport;
  collection_meta?: Record<string, unknown>;
  retry_count?: Record<string, number>;
};

export type ReportVersion = {
  version: number;
  title: string;
  report_md: string;
  created_at: string;
};

export type RunRecord = {
  run_id: string;
  status: string;
  meta: RunMeta;
  settings: RunSettings;
  final_state: FinalState | null;
  error?: string | null;
  events: RunEvent[];
  report_versions: ReportVersion[];
};

export type FollowupResponse =
  | { action: "answer"; message: string }
  | { action: "revision"; message: string; version: number; report_md: string }
  | { action: "new_intake"; message: string; intake_seed: string };
