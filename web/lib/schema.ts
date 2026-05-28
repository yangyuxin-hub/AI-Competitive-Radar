export type SupportStatus =
  | "supported"
  | "partially_supported"
  | "not_supported"
  | "unknown";

export interface QualityScore {
  score: number;
  scale: number;
  basis?: string;
  evidence_ids?: string[];
}

export interface FeatureCell {
  support_status: SupportStatus;
  support_evidence_ids?: string[];
  quality_score?: QualityScore;
}

export interface FeatureGap {
  winner?: string;
  gap_type?: string;
  reason?: string;
  evidence_ids?: string[];
  confidence?: number;
}

export interface Feature {
  feature_id: string;
  name: string;
  products: Record<string, FeatureCell>;
  gap?: FeatureGap;
}

export interface FeatureTree {
  category: string;
  features: Feature[];
}

export interface PricingTier {
  tier_name: string;
  billing_cycle?: string;
  price?: { amount?: number; currency?: string; normalized_usd_month?: number };
  limits?: { limit_name: string; limit_value: unknown; unit?: string }[];
  display_limits?: string[] | string;
  evidence_ids?: string[];
}

export interface PricingProduct {
  name: string;
  tiers: PricingTier[];
}

export interface PricingModel {
  products: PricingProduct[];
  pricing_gap?: {
    target_position?: string;
    summary?: string;
    evidence_ids?: string[];
    confidence?: number;
  };
}

export interface SwotItem {
  point: string;
  evidence_ids?: string[];
  confidence?: number;
}

export interface Swot {
  target?: string;
  note?: string;
  strengths: SwotItem[];
  weaknesses: SwotItem[];
  opportunities: SwotItem[];
  threats: SwotItem[];
}

export interface PainPoint {
  pain_id: string;
  description: string;
  frequency?: { level?: string; count?: string; sample_size?: number; evidence_ids?: string[] };
  affected_products?: string[];
  evidence_ids?: string[];
}

export interface UserSegment {
  segment_id: string;
  name: string;
  description?: string;
  evidence_ids?: string[];
  confidence?: number;
}

export interface UserPersona {
  user_segments: UserSegment[];
  pain_points: PainPoint[];
}

export interface PriorityScore {
  final_score?: number;
  priority?: string;
  pain_frequency?: number;
  business_impact?: number;
  implementation_feasibility?: number;
  evidence_confidence?: number;
}

export interface Recommendation {
  rec_id: string;
  action: string;
  rationale?: string;
  source_feature_ids?: string[];
  source_pain_ids?: string[];
  evidence_ids?: string[];
  priority_score?: PriorityScore;
}

export interface SchemaDraft {
  analysis_meta?: Record<string, unknown>;
  feature_tree?: FeatureTree;
  pricing_model?: PricingModel;
  user_persona?: UserPersona;
  swot?: Swot;
  recommendations?: Recommendation[];
}

export const SUPPORT_META: Record<SupportStatus, { icon: string; tone: string; label: string }> = {
  supported: { icon: "✅", tone: "text-emerald-400", label: "支持" },
  partially_supported: { icon: "⚠️", tone: "text-amber-400", label: "部分支持" },
  not_supported: { icon: "❌", tone: "text-red-400", label: "不支持" },
  unknown: { icon: "❓", tone: "text-neutral-500", label: "未知" },
};
