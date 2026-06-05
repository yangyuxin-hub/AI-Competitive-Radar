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
