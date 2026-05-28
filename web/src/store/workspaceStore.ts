import { create } from "zustand";
import type {
  ChatMessage,
  DomainConfig,
  Evidence,
  FinalState,
  Question,
  ReportVersion,
  RunEvent,
  RunMeta,
  RunSettings,
  WorkspaceMode
} from "../types";

type NodeSummary = {
  node: string;
  status: "wait" | "running" | "done" | "warn" | "error";
  detail: string;
  count: number;
};

type WorkspaceState = {
  mode: WorkspaceMode;
  messages: ChatMessage[];
  domains: Record<string, DomainConfig>;
  products: string[];
  settings: RunSettings;
  pendingQuestions: Question[];
  pendingPrompt: string;
  activeRunId: string | null;
  finalState: FinalState | null;
  events: RunEvent[];
  nodeSummaries: Record<string, NodeSummary>;
  reportVersions: ReportVersion[];
  activeVersion: number;
  selectedEvidenceId: string | null;
  lastError: string;
  setDomains: (domains: Record<string, DomainConfig>, products: string[]) => void;
  updateSettings: (patch: Partial<RunSettings>) => void;
  addMessage: (role: ChatMessage["role"], content: string) => void;
  reset: () => void;
  setClarifying: (questions: Question[], prompt: string) => void;
  startRun: (runId: string) => void;
  addRunEvent: (event: RunEvent) => void;
  setFinal: (state: FinalState, versions: ReportVersion[]) => void;
  setError: (error: string) => void;
  addReportVersion: (version: ReportVersion) => void;
  setActiveVersion: (version: number) => void;
  setSelectedEvidenceId: (id: string | null) => void;
};

function makeId(): string {
  return `${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

const defaultNodes = (): Record<string, NodeSummary> => ({
  collector: { node: "collector", status: "wait", detail: "等待收集证据", count: 0 },
  analyzer: { node: "analyzer", status: "wait", detail: "等待分析 facts / derivations", count: 0 },
  writer: { node: "writer", status: "wait", detail: "等待生成 Markdown", count: 0 },
  reviewer: { node: "reviewer", status: "wait", detail: "等待规则质检", count: 0 }
});

const defaultSettings: RunSettings = {
  domain_key: "ai_coding",
  reviewer_mode: "minimal",
  use_mock: true,
  enable_live: false,
  demo_loop: false
};

export const useWorkspaceStore = create<WorkspaceState>((set, get) => ({
  mode: "home",
  messages: [],
  domains: {},
  products: [],
  settings: defaultSettings,
  pendingQuestions: [],
  pendingPrompt: "",
  activeRunId: null,
  finalState: null,
  events: [],
  nodeSummaries: defaultNodes(),
  reportVersions: [],
  activeVersion: 1,
  selectedEvidenceId: null,
  lastError: "",

  setDomains: (domains, products) => {
    const keys = Object.keys(domains);
    set((state) => ({
      domains,
      products,
      settings: {
        ...state.settings,
        domain_key: keys.includes(state.settings.domain_key) ? state.settings.domain_key : keys[0] ?? "ai_coding"
      }
    }));
  },

  updateSettings: (patch) => set((state) => ({ settings: { ...state.settings, ...patch } })),

  addMessage: (role, content) =>
    set((state) => ({
      messages: [...state.messages, { id: makeId(), role, content }]
    })),

  reset: () =>
    set({
      mode: "home",
      messages: [],
      pendingQuestions: [],
      pendingPrompt: "",
      activeRunId: null,
      finalState: null,
      events: [],
      nodeSummaries: defaultNodes(),
      reportVersions: [],
      activeVersion: 1,
      selectedEvidenceId: null,
      lastError: ""
    }),

  setClarifying: (questions, prompt) =>
    set({
      mode: "clarifying",
      pendingQuestions: questions,
      pendingPrompt: prompt
    }),

  startRun: (runId) =>
    set({
      mode: "running",
      activeRunId: runId,
      finalState: null,
      events: [],
      nodeSummaries: defaultNodes(),
      reportVersions: [],
      activeVersion: 1,
      selectedEvidenceId: null,
      lastError: ""
    }),

  addRunEvent: (event) =>
    set((state) => {
      const nextNodes = { ...state.nodeSummaries };
      if (event.event === "node_completed") {
        const node = String(event.data.node);
        const summary = (event.data.summary ?? {}) as Record<string, unknown>;
        const reject = summary.reject_target ? ` · 打回 ${summary.reject_target}` : "";
        const score = summary.quality_score ? ` · 质量 ${summary.quality_score}` : "";
        const duration = summary.duration ? ` · ${summary.duration}s` : "";
        const current = nextNodes[node] ?? { node, status: "wait", detail: "", count: 0 };
        nextNodes[node] = {
          node,
          status: summary.status === "running" && reject ? "warn" : "done",
          detail: `完成第 ${current.count + 1} 次${reject}${score}${duration}`,
          count: current.count + 1
        };
      }
      if (event.event === "analyzer_step") {
        const step = String(event.data.step ?? "analyzer");
        const phase = String(event.data.phase ?? "");
        nextNodes.analyzer = {
          ...nextNodes.analyzer,
          status: phase === "repair" ? "warn" : "running",
          detail: `${step} ${phase}`
        };
      }
      if (event.event === "error") {
        Object.keys(nextNodes).forEach((key) => {
          if (nextNodes[key].status === "running") nextNodes[key].status = "error";
        });
      }
      return {
        events: [...state.events, event],
        nodeSummaries: nextNodes
      };
    }),

  setFinal: (finalState, versions) =>
    set({
      mode: "ready",
      finalState,
      reportVersions: versions.length
        ? versions
        : [
            {
              version: 1,
              title: "原始报告",
              report_md: finalState.report_draft ?? "",
              created_at: new Date().toISOString()
            }
          ],
      activeVersion: versions[versions.length - 1]?.version ?? 1
    }),

  setError: (error) => set({ mode: "error", lastError: error }),

  addReportVersion: (version) =>
    set((state) => ({
      reportVersions: [...state.reportVersions, version],
      activeVersion: version.version
    })),

  setActiveVersion: (version) => set({ activeVersion: version }),

  setSelectedEvidenceId: (id) => set({ selectedEvidenceId: id })
}));

export function currentReportMarkdown(): string {
  const state = useWorkspaceStore.getState();
  const version = state.reportVersions.find((item) => item.version === state.activeVersion);
  return version?.report_md ?? state.finalState?.report_draft ?? "";
}

export function selectedEvidence(): Evidence | null {
  const state = useWorkspaceStore.getState();
  const id = state.selectedEvidenceId;
  if (!id) return null;
  return state.finalState?.raw_evidence?.find((item) => item.evidence_id === id) ?? null;
}

export function buildMetaFromAnswers(answers: Record<string, string | string[]>, userInput: string): RunMeta {
  const targetRaw = answers.target;
  const target = Array.isArray(targetRaw) ? targetRaw[0] ?? "" : targetRaw ?? "";
  const competitorsRaw = answers.competitors;
  const competitors = Array.isArray(competitorsRaw)
    ? competitorsRaw.filter((item) => item && item !== target)
    : competitorsRaw
      ? [competitorsRaw].filter((item) => item !== target)
      : [];
  const focusRaw = answers.focus;
  const focus = Array.isArray(focusRaw) ? focusRaw : focusRaw ? [focusRaw] : [];
  const purposeRaw = answers.purpose;
  const purpose = Array.isArray(purposeRaw) ? purposeRaw[0] ?? "" : purposeRaw ?? "";

  return {
    target_product: target,
    competitors,
    analysis_focus: focus,
    analysis_purpose: purpose || "学习竞品优点，优化自身产品",
    user_input: userInput || `分析 ${target} 和 ${competitors.join(", ")} 在 ${focus[0] ?? "核心能力"} 上的差距`
  };
}
