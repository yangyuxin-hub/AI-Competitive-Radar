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

export interface CoverageDomain {
  id: string;
  name: string;
  weight: number;
  score: number | null;
  evidence_rate: number;
  known: number;
  total: number;
}

export interface CoverageInfo {
  coverage_known_only?: number | null;
  evidence_coverage_rate?: number | null;
  by_domain?: CoverageDomain[];
}

export interface WinnerRow {
  feature_id?: string;
  name: string;
  winner: string;
  reason?: string;
  confidence?: string;
}

export interface MoatCandidate {
  name: string;
  domain?: string;
  depth_score?: number;
  confidence?: string;
  factors?: string[];
}

export interface WhitespaceItem {
  name: string;
  domain?: string;
  reason?: string;
  barrier?: string;
}

export interface FeatureAnalysis {
  coverage?: Record<string, CoverageInfo>;
  winners?: WinnerRow[];
  differentiation_matrix?: { note?: string }[];
  archetypes?: Record<string, string>;
  moat_candidates?: MoatCandidate[];
  whitespace?: WhitespaceItem[];
}

export interface FeatureTree {
  category: string;
  features: Feature[];
  analysis?: FeatureAnalysis;
  source_skill?: string;
  feature_tree_skill_version?: string;
  generation_mode?: string;
  scoring_rubric?: Record<string, string>;
}

export interface PricingTier {
  tier_name: string;
  segment?: string; // 面向用户: 个人 | 团队 | 企业 | 通用
  billing_cycle?: string;
  price?: { amount?: number; currency?: string; normalized_usd_month?: number };
  limits?: { limit_name: string; limit_value: unknown; unit?: string }[];
  display_limits?: string[] | string;
  evidence_ids?: string[];
}

export interface PricingProduct {
  name: string;
  tiers: PricingTier[];
  pricing_engine?: { archetype?: string; comparison_axis?: string };
}

export interface PricingStrategyProduct {
  product?: string;
  business_logic?: string;
}

export interface PricingModel {
  products: PricingProduct[];
  pricing_gap?: {
    target_position?: string;
    summary?: string;
    evidence_ids?: string[];
    confidence?: number;
  };
  engine_comparison?: {
    insights?: string[];
    gaps?: { note?: string }[];
  };
  pricing_strategy_analysis?: {
    pricing_model_analysis?: { summary?: string; products?: PricingStrategyProduct[] };
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
  affected_segments?: string[];
  user_expectation?: string;
  confidence?: number;
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

export type ActionType = "Learn" | "Avoid" | "Attack";

export interface Recommendation {
  rec_id: string;
  action: string;
  action_type?: ActionType | string;
  target_competitor?: string | string[];
  rationale?: string;
  expected_impact?: string;
  success_metric?: string;
  risk?: string;
  time_horizon?: string;
  validation_method?: string;
  source_feature_ids?: string[];
  source_pain_ids?: string[];
  evidence_ids?: string[];
  evidence_refs?: string[];
  priority_score?: PriorityScore;
}

export interface DecisionAnswer {
  answer?: string;
  confidence?: "high" | "medium" | "low" | string;
  refs?: string[];
}

export interface DecisionSummary {
  why_success?: DecisionAnswer;
  how_monetize?: DecisionAnswer;
  moat?: DecisionAnswer;
  what_to_learn?: DecisionAnswer;
  what_to_avoid?: DecisionAnswer;
}

export interface LandscapeEntry {
  name: string;
  reason?: string;
  relation?: string;
  evidence_ids?: string[];
}

export interface CompetitorLandscape {
  direct?: LandscapeEntry[];
  indirect?: LandscapeEntry[];
  alternative?: LandscapeEntry[];
  selection_rationale?: string;
}

export interface PositioningProduct {
  name: string;
  target_user?: string;
  core_scenario?: string;
  value_proposition?: string;
  positioning_label?: string;
  evidence_ids?: string[];
}

export interface SchemaDraft {
  analysis_meta?: Record<string, unknown>;
  feature_tree?: FeatureTree;
  pricing_model?: PricingModel;
  user_persona?: UserPersona;
  swot?: Swot;
  recommendations?: Recommendation[];
  decision_summary?: DecisionSummary;
  competitor_landscape?: CompetitorLandscape;
  positioning_map?: { products?: PositioningProduct[] };
}

export const SUPPORT_META: Record<SupportStatus, { icon: string; tone: string; label: string }> = {
  supported: { icon: "✅", tone: "text-emerald-400", label: "支持" },
  partially_supported: { icon: "⚠️", tone: "text-amber-400", label: "部分支持" },
  not_supported: { icon: "❌", tone: "text-red-400", label: "不支持" },
  unknown: { icon: "❓", tone: "text-neutral-500", label: "未知" },
};
