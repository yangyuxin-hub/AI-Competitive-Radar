export interface Question {
  key: "target" | "competitors" | "focus" | "purpose" | "persist";
  question: string;
  options: string[];
  multi: boolean;
  suggested: string[];
  allow_custom: boolean;
}

export interface Evidence {
  evidence_id: string;
  product: string;
  claim_type: string;
  source_type: string;
  source_bias: string;
  source_url: string;
  observed_at: string;
  source_freshness: string;
  claim: string;
  extracted_snippet: string;
  source_reliability: number;
  claim_relevance?: number;
  evidence_confidence?: number;
}

export interface QualityReport {
  mode?: string;
  quality_score?: number;
  quality_dimensions?: Record<
    string,
    { label: string; score: number; note?: string }
  >;
  passed_rules?: string[];
  failed_rules?: string[];
  warning_rules?: string[];
  errors?: { rule?: string; reject_target?: string; detail?: string }[];
  warnings?: { rule?: string; detail?: string }[];
}

export interface ReportMeta {
  report_id: string;
  target_product: string;
  competitors: string[];
  analysis_focus: string[];
  analysis_purpose?: string;
  runtime_profile?: string;
  data_cutoff?: string;
  generated_at?: string;
}

export interface CompletenessAspect {
  key: string;
  label: string;
  value: number; // 0-1
  detail?: string;
}

export interface Completeness {
  overall: number; // 0-100
  aspects: CompletenessAspect[];
  counts: {
    features: number;
    recommendations: number;
    swot_items: number;
    products: number;
  };
  missing_action_fields?: string[];
}

export interface BusinessValue {
  rows: { metric: string; manual: string; system: string; delta: string }[];
  headline?: string;
  assumptions?: string;
}

export interface ResearchMethod {
  method: string;
  synthetic?: boolean;
  questions: { id?: string; text: string }[];
  n_findings: number;
  personas: string[];
}

export interface Report {
  report_id: string;
  meta: ReportMeta;
  summary?: string;
  schema_draft: import("./schema").SchemaDraft | null;
  report_draft: string | null;
  quality_report: QualityReport | null;
  completeness?: Completeness | null;
  business_value?: BusinessValue | null;
  research_method?: ResearchMethod | null;
  raw_evidence: Evidence[];
  status: string;
  stage_timings?: {
    node: string;
    icon?: string;
    label: string;
    duration_sec: number;
    result?: string | null;
  }[];
  created_at: string;
}

export interface ReportIndexItem {
  report_id: string;
  target_product: string;
  competitors: string[];
  analysis_focus: string[];
  summary?: string;
  runtime_profile?: string;
  evidence_count?: number;
  status: string;
  quality_score?: number;
  created_at: string;
}

export interface AnalysisPreview {
  kind: "overview" | "facts" | "derivations";
  fallback?: boolean;
  note?: string;
  evidence?: {
    products?: { product: string; count: number }[];
    claim_types?: { label: string; count: number }[];
    sources?: { label: string; count: number }[];
  };
  features?: string[];
  signals?: string[];
  pain_points?: string[];
  pricing?: string[];
  recommendations?: { action: string; priority?: string | null }[];
  swot?: {
    strengths?: number;
    weaknesses?: number;
    opportunities?: number;
    threats?: number;
  };
}

export type ProgressEvent =
  | {
      type: "status";
      node: string;
      icon: string;
      label: string;
      message: string;
      elapsed_sec: number;
      evidence_count: number;
      retry_count: Record<string, number>;
      collector_phase?: string;
      analysis_step?: string;
      analysis_phase?: string;
      analysis_summary?: string;
      analysis_preview?: AnalysisPreview;
      product?: string;
      source_counts?: Record<string, number>;
      coverage?: Record<string, number>;
      eta_sec?: number;
      samples?: { product: string; source: string; text: string }[];
    }
  | {
      type: "progress";
      node: string;
      icon: string;
      label: string;
      status: string;
      evidence_count: number;
      retry_count: Record<string, number>;
      reject_target: string | null;
      result?: string | null;
      detail?: NodeDetail | null;
      collection_health?: {
        product: string;
        health: string;
        missing_claim_types: string[];
      }[];
      collector_phase?: string;
      message?: string;
      product?: string;
      source_counts?: Record<string, number>;
      coverage?: Record<string, number>;
      official_count?: number;
      pricing_count?: number;
      product_count?: number;
      quality?: {
        quality_score?: number;
        passed_rules?: string[];
        failed_rules?: string[];
        warning_rules?: string[];
      };
    }
  | { type: "done"; report_id: string; report: Report }
  | { type: "judge"; report_id: string; scorecard: JudgeScorecard }
  | { type: "judge_error"; message: string }
  | { type: "error"; message: string };

export interface JudgeScorecard {
  weighted_score: number;
  weights_profile?: string;
  dimensions: Record<
    string,
    { name: string; score: number; scale: number; weight: number;
      justification?: string; fix_suggestion?: string }
  >;
  warnings?: string[];
}

export interface NodeDetail {
  kind: "collection" | "analysis" | "review";
  coverage?: { label: string; count: number; ok: boolean }[];
  missing?: string[];
  sources?: { label: string; count: number }[];
  products?: { product: string; health?: string; missing: string[] }[];
  searched?: {
    product: string;
    query: string;
    site?: string;
    claim_type?: string;
    count: number;
    urls: string[];
  }[];
  features?: string[];
  recommendations?: { action: string; priority?: string | null }[];
  passed?: string[];
  warnings?: string[];
  failed?: string[];
}

export type Answers = Record<string, string | string[]>;
