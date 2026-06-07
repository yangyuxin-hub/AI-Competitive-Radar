import type {
  Question,
  Report,
  ReportIndexItem,
  ProgressEvent,
  Answers,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export async function fetchQuestions(
  userInput: string,
  domainHint?: string,
  fast?: boolean
): Promise<{ questions: Question[]; draft: Record<string, unknown> }> {
  const res = await fetch(`${BASE}/api/intake/questions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_input: userInput, domain_hint: domainHint, fast: !!fast }),
  });
  if (!res.ok) throw new Error(`questions failed: ${res.status}`);
  return res.json();
}

export interface IntakeStreamHandlers {
  onStatus?: (text: string) => void;
  onReasoning?: (delta: string) => void;
  onDone: (draft: Record<string, unknown>, questions: Question[]) => void;
  onError?: (msg: string) => void;
}

/** POST /api/intake/stream，逐 SSE 事件回调（思考逐字流式 + 阶段进度）。返回中止函数。 */
export function streamIntake(
  userInput: string,
  domainHint: string | undefined,
  h: IntakeStreamHandlers
): () => void {
  const controller = new AbortController();
  (async () => {
    const res = await fetch(`${BASE}/api/intake/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_input: userInput, domain_hint: domainHint }),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) throw new Error(`intake stream failed: ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let evt: { type?: string; text?: string; delta?: string; draft?: Record<string, unknown>; questions?: Question[]; message?: string };
        try {
          evt = JSON.parse(line.slice(5).trim());
        } catch {
          continue;
        }
        if (evt.type === "status") h.onStatus?.(evt.text ?? "");
        else if (evt.type === "reasoning") h.onReasoning?.(evt.delta ?? "");
        else if (evt.type === "done") h.onDone(evt.draft ?? {}, evt.questions ?? []);
        else if (evt.type === "error") h.onError?.(evt.message ?? "intake error");
      }
    }
  })().catch((err) => {
    if (err?.name !== "AbortError") h.onError?.(String(err?.message ?? err));
  });
  return () => controller.abort();
}

export interface RunArgs {
  target_product: string;
  competitors: string[];
  analysis_focus: string[];
  analysis_purpose?: string;
  user_input?: string;
  runtime_profile?: "fast" | "balanced" | "deep";
  analysis_intent?: string; // 从 intake 草稿带回;留空则后端按 user_input 推断
}

/** 把选择题答案拼成运行参数（对齐后端 intake.assemble_meta 的口径）。 */
export function answersToRunArgs(answers: Answers, userInput: string): RunArgs {
  const asList = (v: string | string[] | undefined): string[] =>
    v == null ? [] : Array.isArray(v) ? v : v ? [v] : [];
  const target = asList(answers.target)[0] ?? "";
  return {
    target_product: target,
    competitors: asList(answers.competitors).filter((c) => c !== target),
    analysis_focus: asList(answers.focus),
    analysis_purpose: asList(answers.purpose).join(" / ") || undefined,
    user_input: userInput || undefined,
  };
}

/** POST /api/run，逐 SSE 事件回调。返回中止函数。 */
export function runAnalysis(
  args: RunArgs,
  onEvent: (e: ProgressEvent) => void
): () => void {
  const controller = new AbortController();
  (async () => {
    const res = await fetch(`${BASE}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`run failed: ${res.status}`);
    if (!res.body) throw new Error("no response body");
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        try {
          onEvent(JSON.parse(line.slice(5).trim()) as ProgressEvent);
        } catch {
          /* 跳过解析失败的块 */
        }
      }
    }
  })().catch((err) => {
    if (err?.name !== "AbortError") {
      onEvent({ type: "error", message: String(err?.message ?? err) });
    }
  });
  return () => controller.abort();
}

export async function fetchReports(): Promise<ReportIndexItem[]> {
  const res = await fetch(`${BASE}/api/reports`, { cache: "no-store" });
  if (!res.ok) throw new Error(`reports failed: ${res.status}`);
  return (await res.json()).reports;
}

export async function fetchReport(id: string): Promise<Report> {
  const res = await fetch(`${BASE}/api/reports/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`report failed: ${res.status}`);
  return res.json();
}

export interface StageStat {
  stage: string;
  runs: number;
  avg_elapsed: number | null;
  verdicts: Record<string, number>;
  last_metrics: Record<string, number | string | null> | null;
}

export async function fetchStageQuality(): Promise<{ stages: StageStat[]; recent: unknown[] }> {
  const res = await fetch(`${BASE}/api/stage_quality`, { cache: "no-store" });
  if (!res.ok) throw new Error(`stage_quality failed: ${res.status}`);
  return res.json();
}
