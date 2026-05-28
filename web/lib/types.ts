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
  data_cutoff?: string;
  generated_at?: string;
}

export interface Report {
  report_id: string;
  meta: ReportMeta;
  schema_draft: import("./schema").SchemaDraft | null;
  report_draft: string | null;
  quality_report: QualityReport | null;
  raw_evidence: Evidence[];
  status: string;
  created_at: string;
}

export interface ReportIndexItem {
  report_id: string;
  target_product: string;
  competitors: string[];
  analysis_focus: string[];
  status: string;
  quality_score?: number;
  created_at: string;
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
  | { type: "error"; message: string };

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
