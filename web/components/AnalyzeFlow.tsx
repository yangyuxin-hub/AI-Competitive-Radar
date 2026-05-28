"use client";

import { useEffect, useState } from "react";
import {
  fetchQuestions,
  answersToRunArgs,
  runAnalysis,
} from "@/lib/api";
import type { Question, Answers, ProgressEvent, Report } from "@/lib/types";
import ReportView from "./ReportView";

type Stage = "input" | "clarify" | "running" | "report";

const SCENARIOS = [
  "评估下季度要不要做某个功能，看三家友商做得怎么样",
  "Cursor 刚发了大更新，要不要跟、怎么跟",
  "用户为什么流失到竞品，看看大家在吐槽什么",
];

const PIPELINE = [
  { node: "collector", icon: "📥", label: "收集证据" },
  { node: "analyzer", icon: "🧠", label: "分析结论" },
  { node: "writer", icon: "✍️", label: "生成报告" },
  { node: "reviewer", icon: "🧪", label: "规则质检" },
];

export default function AnalyzeFlow() {
  const [stage, setStage] = useState<Stage>("input");
  const [userInput, setUserInput] = useState("");
  const [questions, setQuestions] = useState<Question[]>([]);
  const [answers, setAnswers] = useState<Answers>({});
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmitInput() {
    if (!userInput.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { questions } = await fetchQuestions(userInput);
      setQuestions(questions);
      const init: Answers = {};
      for (const q of questions) {
        init[q.key] = q.multi ? [...q.suggested] : (q.suggested[0] ?? q.options[0] ?? "");
      }
      setAnswers(init);
      setStage("clarify");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function toggle(q: Question, opt: string) {
    setAnswers((prev) => {
      if (q.multi) {
        const cur = (prev[q.key] as string[]) ?? [];
        return {
          ...prev,
          [q.key]: cur.includes(opt) ? cur.filter((x) => x !== opt) : [...cur, opt],
        };
      }
      return { ...prev, [q.key]: opt };
    });
  }

  function onRun() {
    const args = answersToRunArgs(answers, userInput);
    if (!args.target_product) {
      setError("请先选择目标产品");
      return;
    }
    setEvents([]);
    setReport(null);
    setError(null);
    setStage("running");
    runAnalysis(args, (e) => {
      if (e.type === "done") {
        setReport(e.report);
        setStage("report");
      } else if (e.type === "error") {
        setError(e.message);
      } else {
        setEvents((prev) => [...prev, e]);
      }
    });
  }

  function reset() {
    setStage("input");
    setUserInput("");
    setQuestions([]);
    setAnswers({});
    setEvents([]);
    setReport(null);
    setError(null);
  }

  return (
    <div>
      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {stage === "input" && (
        <div className="space-y-4">
          <textarea
            value={userInput}
            onChange={(e) => setUserInput(e.target.value)}
            placeholder="用一句话说想分析什么，例如：分析 Cursor 和 Windsurf 的代码补全体验"
            className="h-28 w-full resize-none rounded-xl border border-white/10 bg-white/5 p-4 text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-sky-500/50"
          />
          <div className="flex flex-wrap gap-2">
            {SCENARIOS.map((s) => (
              <button
                key={s}
                onClick={() => setUserInput(s)}
                className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-neutral-400 transition hover:border-sky-500/40 hover:text-sky-300"
              >
                {s}
              </button>
            ))}
          </div>
          <button
            onClick={onSubmitInput}
            disabled={busy || !userInput.trim()}
            className="rounded-xl bg-sky-500 px-6 py-2.5 font-medium text-white transition hover:bg-sky-400 disabled:opacity-40"
          >
            {busy ? "理解意图中…" : "下一步：确认分析配置"}
          </button>
        </div>
      )}

      {stage === "clarify" && (
        <div className="space-y-6">
          <p className="text-sm text-neutral-400">
            Agent 已根据你的描述预填，下面可点选调整：
          </p>
          {questions.map((q) => (
            <div key={q.key}>
              <div className="mb-2 text-sm font-medium text-neutral-200">{q.question}</div>
              <div className="flex flex-wrap gap-2">
                {q.options.map((opt) => {
                  const cur = answers[q.key];
                  const on = q.multi
                    ? ((cur as string[]) ?? []).includes(opt)
                    : cur === opt;
                  return (
                    <button
                      key={opt}
                      onClick={() => toggle(q, opt)}
                      className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                        on
                          ? "border-sky-500 bg-sky-500/15 text-sky-300"
                          : "border-white/10 bg-white/5 text-neutral-400 hover:border-white/30"
                      }`}
                    >
                      {opt}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          <div className="flex gap-3">
            <button
              onClick={onRun}
              className="rounded-xl bg-sky-500 px-6 py-2.5 font-medium text-white transition hover:bg-sky-400"
            >
              开始分析
            </button>
            <button
              onClick={() => setStage("input")}
              className="rounded-xl border border-white/10 px-6 py-2.5 text-neutral-400 transition hover:text-white"
            >
              返回
            </button>
          </div>
        </div>
      )}

      {stage === "running" && <AgentProgress events={events} />}

      {stage === "report" && report && (
        <div className="space-y-6">
          <ReportView report={report} />
          <div className="flex justify-center">
            <button
              onClick={reset}
              className="rounded-xl border border-white/10 px-6 py-2.5 text-neutral-400 transition hover:text-white"
            >
              开始新的分析
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function useElapsed(running: boolean) {
  const [sec, setSec] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setSec((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [running]);
  return sec;
}

function AgentProgress({ events }: { events: ProgressEvent[] }) {
  const progress = events.filter((e) => e.type === "progress") as Extract<
    ProgressEvent,
    { type: "progress" }
  >[];
  const elapsed = useElapsed(true);
  // 一个节点可能出现多次（retry）→ 用出现次数判断 done
  const counts = new Map<string, number>();
  for (const e of progress) counts.set(e.node, (counts.get(e.node) ?? 0) + 1);
  const last = progress.at(-1);
  const currentNode = last?.node;
  const evidenceCount = last?.evidence_count ?? 0;
  const retry = last?.retry_count ?? {};
  const totalRetry = Object.values(retry).reduce((a, b) => a + b, 0);
  const quality = progress.findLast((e) => e.quality)?.quality;
  const rejected = progress.some((e) => e.reject_target);

  return (
    <div className="space-y-6 py-6">
      <div className="flex items-center justify-center gap-4 text-sm text-neutral-400">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 animate-ping rounded-full bg-sky-400" />
          多 Agent 协作中
        </span>
        <span>
          已收集 <span className="font-mono text-sky-400">{evidenceCount}</span> 条证据
        </span>
        <span className="font-mono text-neutral-500">{elapsed}s</span>
        {totalRetry > 0 && (
          <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs text-amber-400">
            打回重试 ×{totalRetry}
          </span>
        )}
      </div>

      <div className="flex items-stretch justify-center gap-1">
        {PIPELINE.map((p, i) => {
          const seen = (counts.get(p.node) ?? 0) > 0;
          const active = p.node === currentNode;
          const done = seen && !active;
          const repeated = (counts.get(p.node) ?? 0) > 1;
          return (
            <div key={p.node} className="flex items-center">
              <div
                className={`relative flex w-32 flex-col items-center rounded-xl border p-4 transition-all duration-300 ${
                  active
                    ? "border-sky-500 bg-sky-500/10 shadow-lg shadow-sky-500/10"
                    : done
                      ? "border-emerald-500/40 bg-emerald-500/5"
                      : "border-white/10 bg-white/5"
                }`}
              >
                {repeated && (
                  <span className="absolute -right-1.5 -top-1.5 rounded-full bg-amber-500 px-1.5 text-[10px] font-bold text-black">
                    ×{counts.get(p.node)}
                  </span>
                )}
                <div className={`text-2xl ${active ? "animate-bounce" : ""}`}>{p.icon}</div>
                <div className="mt-1 text-xs text-neutral-300">{p.label}</div>
                <div
                  className={`mt-1 text-[10px] ${
                    active ? "text-sky-400" : done ? "text-emerald-400" : "text-neutral-600"
                  }`}
                >
                  {active ? "进行中…" : done ? "✓ 完成" : "等待"}
                </div>
              </div>
              {i < PIPELINE.length - 1 && (
                <div className={`h-0.5 w-5 transition-colors ${done ? "bg-emerald-500/50" : "bg-white/10"}`} />
              )}
            </div>
          );
        })}
      </div>

      {quality && (
        <div className="mx-auto max-w-lg rounded-xl border border-white/10 bg-white/5 p-4 text-sm">
          <div className="mb-2 flex items-center justify-between text-neutral-400">
            <span>Reviewer 规则质检</span>
            {quality.quality_score != null && (
              <span className="font-mono text-emerald-400">{quality.quality_score}/100</span>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(quality.passed_rules ?? []).map((r) => (
              <span key={r} className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">
                ✓ {r}
              </span>
            ))}
            {(quality.warning_rules ?? []).map((r) => (
              <span key={`w-${r}`} className="rounded bg-amber-500/15 px-2 py-0.5 text-xs text-amber-400">
                ⚠ {r}
              </span>
            ))}
            {(quality.failed_rules ?? []).map((r) => (
              <span key={`f-${r}`} className="rounded bg-red-500/15 px-2 py-0.5 text-xs text-red-400">
                ✕ {r}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 实时事件流 */}
      <div className="mx-auto max-w-lg space-y-1">
        {progress.slice(-6).map((e, i) => (
          <div
            key={i}
            className="flex items-center gap-2 rounded-lg bg-white/[0.02] px-3 py-1.5 text-xs text-neutral-400"
          >
            <span>{e.icon}</span>
            <span className="text-neutral-300">{e.label}</span>
            {e.reject_target && (
              <span className="text-amber-400">→ 打回 {e.reject_target}</span>
            )}
            <span className="ml-auto font-mono text-neutral-600">{e.evidence_count} 证据</span>
          </div>
        ))}
      </div>

      {rejected && (
        <p className="text-center text-xs text-neutral-500">
          Reviewer 发现问题并打回重跑 — 这正是质量自愈闭环在工作
        </p>
      )}
    </div>
  );
}
