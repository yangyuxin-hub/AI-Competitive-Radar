"use client";

import { useEffect, useRef, useState } from "react";
import {
  fetchQuestions,
  streamIntake,
  answersToRunArgs,
  runAnalysis,
} from "@/lib/api";
import type { Question, Answers, ProgressEvent, Report, NodeDetail, AnalysisPreview } from "@/lib/types";
import { domainOf } from "@/lib/source";
import ReportView from "./ReportView";

type Stage = "input" | "clarify" | "running" | "report";
type RuntimeProfile = "fast" | "balanced" | "deep";

const SCENARIOS = [
  {
    domainHint: "ai_coding",
    domain: "AI 编程工具",
    title: "代码补全体验",
    prompt: "分析 Cursor、Windsurf 和 GitHub Copilot 在代码补全体验上的差距",
  },
  {
    domainHint: "ai_assistant",
    domain: "AI 对话助手",
    title: "大模型助手对比",
    prompt: "分析 ChatGPT、Claude 和 Gemini 在通用对话与推理体验上的差距",
  },
  {
    domainHint: "ai_video",
    domain: "AI 视频生成",
    title: "文生视频效果",
    prompt: "分析 Sora、可灵 Kling 和 Runway 在文生视频效果与可控性上的差距",
  },
  {
    domainHint: "ai_image",
    domain: "AI 图像生成",
    title: "文生图质量",
    prompt: "分析 Midjourney、Stable Diffusion 和 DALL·E 在图像生成质量与创作体验上的差距",
  },
  {
    domainHint: "ai_music",
    domain: "AI 音乐生成",
    title: "AI 作曲体验",
    prompt: "分析 Suno、Udio 和 Stable Audio 在 AI 音乐生成质量与易用性上的差距",
  },
  {
    domainHint: "ai_search",
    domain: "AI 搜索",
    title: "AI 搜索体验",
    prompt: "分析 Perplexity、秘塔 AI 搜索 和 ChatGPT Search 在 AI 搜索准确性与体验上的差距",
  },
  {
    domainHint: "pm",
    domain: "项目协作工具",
    title: "团队任务管理",
    prompt: "分析 Notion、Asana 和 Linear 在团队任务管理体验上的差距",
  },
  {
    domainHint: "design",
    domain: "设计协作工具",
    title: "设计协作体验",
    prompt: "分析 Figma、Sketch 和 Canva 在界面设计协作体验上的差距",
  },
];

const RUN_PROFILES: { key: RuntimeProfile; label: string; sub: string }[] = [
  { key: "fast", label: "快速", sub: "缓存优先" },
  { key: "balanced", label: "均衡", sub: "实时检索" },
  { key: "deep", label: "深度", sub: "社区证据 · 默认" },
];

export default function AnalyzeFlow() {
  const [stage, setStage] = useState<Stage>("input");
  const [userInput, setUserInput] = useState("");
  const [domainHint, setDomainHint] = useState<string | undefined>();
  const [questions, setQuestions] = useState<Question[]>([]);
  const [intakeIntent, setIntakeIntent] = useState<string | undefined>(undefined);
  const [answers, setAnswers] = useState<Answers>({});
  const [customOpts, setCustomOpts] = useState<Record<string, string[]>>({});
  const [customInput, setCustomInput] = useState<Record<string, string>>({});
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [intakeReasoning, setIntakeReasoning] = useState<string>("");
  const [intakeStatus, setIntakeStatus] = useState<string>(""); // 流式阶段进度文案
  const [runProfile, setRunProfile] = useState<RuntimeProfile>("deep");
  const [busy, setBusy] = useState(false);
  const [refining, setRefining] = useState(false); // 渐进式:LLM 在后台精修候选(流式)
  const [error, setError] = useState<string | null>(null);
  const cancelRunRef = useRef<(() => void) | null>(null);
  const cancelIntakeRef = useRef<(() => void) | null>(null); // 中止上一次流式精修
  const touchedRef = useRef<Set<string>>(new Set()); // 用户手动改过的问题,LLM 刷新时不覆盖
  const intakeReqRef = useRef(0); // 防止过期的 LLM 响应覆盖新一次提交

  function defaultAnswers(qs: Question[]): Answers {
    const init: Answers = {};
    for (const q of qs) {
      init[q.key] = q.multi ? [...q.suggested] : (q.suggested[0] ?? q.options[0] ?? "");
    }
    return init;
  }

  async function onSubmitInput() {
    if (!userInput.trim()) return;
    setBusy(true);
    setError(null);
    const myReq = ++intakeReqRef.current;
    touchedRef.current = new Set();
    try {
      // 1) 秒显:先用启发式草稿把澄清页渲染出来,不让用户干等 LLM
      const fast = await fetchQuestions(userInput, domainHint, true);
      if (intakeReqRef.current !== myReq) return;
      setQuestions(fast.questions);
      if (typeof fast.draft?.analysis_intent === "string") setIntakeIntent(fast.draft.analysis_intent);
      setIntakeReasoning(typeof fast.draft?.reasoning === "string" ? fast.draft.reasoning : "");
      setAnswers(defaultAnswers(fast.questions));
      setCustomOpts({});
      setCustomInput({});
      setStage("clarify");
      setBusy(false);

      // 2) 后台:LLM 流式精修候选——逐字推送思考过程 + 阶段进度,回来后合并刷新,保留用户已改动的项
      setRefining(true);
      setIntakeReasoning("");          // 清空,准备逐字流式
      setIntakeStatus("正在检索竞品线索…");
      cancelIntakeRef.current?.();      // 中止可能仍在跑的上一次流
      await new Promise<void>((resolve) => {
        cancelIntakeRef.current = streamIntake(userInput, domainHint, {
          onStatus: (t) => {
            if (intakeReqRef.current === myReq) setIntakeStatus(t);
          },
          onReasoning: (d) => {
            if (intakeReqRef.current === myReq) setIntakeReasoning((p) => p + d);
          },
          onDone: (draft, qs) => {
            if (intakeReqRef.current !== myReq) {
              resolve();
              return;
            }
            if (qs.length) setQuestions(qs);
            if (typeof draft?.analysis_intent === "string") setIntakeIntent(draft.analysis_intent as string);
            if (typeof draft?.reasoning === "string" && draft.reasoning) {
              setIntakeReasoning(draft.reasoning as string);
            }
            setAnswers((prev) => {
              const next = { ...prev };
              for (const q of qs) {
                if (touchedRef.current.has(q.key)) continue; // 用户改过的不动
                next[q.key] = q.multi ? [...q.suggested] : (q.suggested[0] ?? q.options[0] ?? "");
              }
              return next;
            });
            resolve();
          },
          onError: () => resolve(), // 流式失败 → 保留启发式结果即可
        });
      });
      if (intakeReqRef.current === myReq) {
        setRefining(false);
        setIntakeStatus("");
      }
    } catch (e) {
      if (intakeReqRef.current === myReq) {
        setError(String(e));
        setBusy(false);
        setRefining(false);
      }
    }
  }

  function toggle(q: Question, opt: string) {
    touchedRef.current.add(q.key);
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

  function addCustom(q: Question) {
    const val = (customInput[q.key] ?? "").trim();
    if (!val) return;
    touchedRef.current.add(q.key);
    setCustomOpts((prev) => {
      const cur = prev[q.key] ?? [];
      return cur.includes(val) ? prev : { ...prev, [q.key]: [...cur, val] };
    });
    setAnswers((prev) => {
      if (q.multi) {
        const cur = (prev[q.key] as string[]) ?? [];
        return cur.includes(val) ? prev : { ...prev, [q.key]: [...cur, val] };
      }
      return { ...prev, [q.key]: val };
    });
    setCustomInput((prev) => ({ ...prev, [q.key]: "" }));
  }

  function onRun() {
    const args = { ...answersToRunArgs(answers, userInput), runtime_profile: runProfile, analysis_intent: intakeIntent };
    if (!args.target_product) {
      setError("请先选择目标产品");
      return;
    }
    cancelRunRef.current?.();
    setEvents([]);
    setReport(null);
    setError(null);
    setStage("running");
    cancelRunRef.current = runAnalysis(args, (e) => {
      if (e.type === "done") {
        cancelRunRef.current = null;
        setReport(e.report);
        setStage("report");
      } else if (e.type === "error") {
        cancelRunRef.current = null;
        setError(e.message);
      } else {
        setEvents((prev) => [...prev, e]);
      }
    });
  }

  function cancelRun() {
    cancelRunRef.current?.();
    cancelRunRef.current = null;
    setError("已取消本次分析");
    setStage("clarify");
  }

  function reset() {
    setStage("input");
    setUserInput("");
    setDomainHint(undefined);
    setQuestions([]);
    setAnswers({});
    setCustomOpts({});
    setCustomInput({});
    setIntakeReasoning("");
    setIntakeStatus("");
    setEvents([]);
    setReport(null);
    setError(null);
    setRunProfile("deep");
    setRefining(false);
    touchedRef.current = new Set();
    intakeReqRef.current++;
    cancelRunRef.current?.();
    cancelRunRef.current = null;
    cancelIntakeRef.current?.();
    cancelIntakeRef.current = null;
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
            onChange={(e) => {
              setUserInput(e.target.value);
              setDomainHint(undefined);
            }}
            placeholder="用一句话说想分析什么，例如：分析 Notion、Asana、Linear 的任务管理体验"
            className="h-28 w-full resize-none rounded-xl border border-white/10 bg-white/5 p-4 text-neutral-100 outline-none placeholder:text-neutral-600 focus:border-sky-500/50"
          />
          <div className="grid gap-2 sm:grid-cols-2">
            {SCENARIOS.map((s) => (
              <button
                key={s.prompt}
                onClick={() => {
                  setUserInput(s.prompt);
                  setDomainHint(s.domainHint);
                }}
                className={`rounded-lg border px-3 py-2 text-left transition hover:border-sky-500/40 hover:bg-white/[0.04] ${
                  userInput === s.prompt
                    ? "border-sky-500/50 bg-sky-500/10"
                    : "border-white/10 bg-white/[0.02]"
                }`}
              >
                <div className="text-xs text-neutral-500">{s.domain}</div>
                <div className="mt-0.5 text-sm font-medium text-neutral-200">{s.title}</div>
                <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-neutral-500">
                  {s.prompt}
                </div>
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
          {refining && (
            <div className="flex items-center gap-2 rounded-lg border border-sky-500/20 bg-sky-500/[0.06] px-3 py-2 text-xs text-sky-300">
              <span className="inline-block h-1.5 w-1.5 animate-ping rounded-full bg-sky-400" />
              {intakeStatus || "正在用 AI 生成更贴合的角度与竞品…"}候选会自动刷新，你已选的不会变
            </div>
          )}
          {(intakeReasoning || refining) && (
            <div className="rounded-xl border border-sky-500/20 bg-sky-500/[0.05] p-4">
              <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-sky-300">
                <span>🧭</span> Agent 的思考
              </div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-neutral-300">
                {intakeReasoning}
                {refining && (
                  <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-sky-400 align-middle" />
                )}
              </p>
            </div>
          )}
          <p className="text-sm text-neutral-400">
            下面是预填的分析配置，可点选调整：
          </p>
          {questions.map((q) => {
            const opts = [...q.options, ...(customOpts[q.key] ?? [])];
            const hasHints = !!q.hints && Object.keys(q.hints).length > 0;
            const isOn = (opt: string) => {
              const cur = answers[q.key];
              return q.multi ? ((cur as string[]) ?? []).includes(opt) : cur === opt;
            };
            const customBox = q.allow_custom && (
              <span className="inline-flex items-center gap-1">
                <input
                  value={customInput[q.key] ?? ""}
                  onChange={(e) => setCustomInput((p) => ({ ...p, [q.key]: e.target.value }))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addCustom(q);
                    }
                  }}
                  placeholder="其他…"
                  className="w-28 rounded-lg border border-dashed border-white/15 bg-transparent px-2.5 py-1.5 text-sm text-neutral-200 outline-none placeholder:text-neutral-600 focus:border-sky-500/50"
                />
                <button
                  onClick={() => addCustom(q)}
                  disabled={!(customInput[q.key] ?? "").trim()}
                  className="rounded-lg border border-white/10 px-2.5 py-1.5 text-sm text-neutral-400 transition hover:text-sky-300 disabled:opacity-40"
                >
                  ＋添加
                </button>
              </span>
            );

            return (
              <div key={q.key}>
                <div className="mb-2 text-sm font-medium text-neutral-200">
                  {q.question}
                  {q.multi && <span className="ml-2 text-xs text-neutral-500">可多选</span>}
                </div>

                {hasHints ? (
                  // 带逐项引导的问题(焦点维度 / 竞品)→ 卡片式,每项下面一句说明
                  <>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {opts.map((opt) => {
                        const on = isOn(opt);
                        const hint = q.hints?.[opt];
                        return (
                          <button
                            key={opt}
                            onClick={() => toggle(q, opt)}
                            className={`rounded-xl border px-3 py-2.5 text-left transition ${
                              on
                                ? "border-sky-500 bg-sky-500/10"
                                : "border-white/10 bg-white/[0.02] hover:border-white/30"
                            }`}
                          >
                            <div className="flex items-center gap-2">
                              <span
                                className={`flex h-4 w-4 shrink-0 items-center justify-center rounded ${
                                  q.multi ? "rounded" : "rounded-full"
                                } border text-[10px] ${
                                  on ? "border-sky-400 bg-sky-500 text-white" : "border-white/25 text-transparent"
                                }`}
                              >
                                ✓
                              </span>
                              <span className={`text-sm font-medium ${on ? "text-sky-200" : "text-neutral-200"}`}>
                                {opt}
                              </span>
                            </div>
                            {hint && (
                              <div className="mt-1 pl-6 text-xs leading-relaxed text-neutral-500">{hint}</div>
                            )}
                          </button>
                        );
                      })}
                    </div>
                    {customBox && <div className="mt-2">{customBox}</div>}
                  </>
                ) : (
                  // 普通问题(目标 / 目的 / 持久化)→ 紧凑 chip
                  <div className="flex flex-wrap items-center gap-2">
                    {opts.map((opt) => {
                      const on = isOn(opt);
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
                    {customBox}
                  </div>
                )}
              </div>
            );
          })}
          <div>
            <div className="mb-2 text-sm font-medium text-neutral-200">采集模式</div>
            <div className="inline-flex rounded-xl border border-white/10 bg-white/[0.03] p-1">
              {RUN_PROFILES.map((p) => (
                <button
                  key={p.key}
                  onClick={() => setRunProfile(p.key)}
                  className={`min-w-24 rounded-lg px-3 py-2 text-left transition ${
                    runProfile === p.key
                      ? "bg-sky-500/20 text-sky-200"
                      : "text-neutral-500 hover:text-neutral-200"
                  }`}
                >
                  <span className="block text-sm font-medium">{p.label}</span>
                  <span className="block text-[11px]">{p.sub}</span>
                </button>
              ))}
            </div>
          </div>
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

      {stage === "running" && <AgentProgress events={events} onCancel={cancelRun} />}

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

// LangGraph DAG 可视化:前向边 collector→analyzer→writer→reviewer→END,
// 加质检打回的回边(reviewer→采集/分析/撰写),实时按状态着色 + 标注重试次数。
const _DAG = [
  { id: "collector", icon: "📥", label: "采集", cx: 78 },
  { id: "analyzer", icon: "🧠", label: "分析", cx: 246 },
  { id: "writer", icon: "✍️", label: "撰写", cx: 414 },
  { id: "reviewer", icon: "🧪", label: "质检", cx: 582 },
];
const _END_CX = 672;
const _ROW_Y = 150;
const _NODE_W = 96;
const _NODE_H = 56;

function DagFlow({
  counts,
  currentNode,
  retry,
  degraded,
  passed,
}: {
  counts: Map<string, number>;
  currentNode?: string;
  retry: Record<string, number>;
  degraded: boolean;
  passed: boolean;
}) {
  const top = _ROW_Y - _NODE_H / 2;
  const cxOf = (id: string) => _DAG.find((n) => n.id === id)!.cx;
  const stateOf = (id: string) =>
    id === currentNode ? "active" : (counts.get(id) ?? 0) > 0 ? "done" : "pending";
  const stroke = (s: string) =>
    s === "active" ? "#38bdf8" : s === "done" ? "#34d399" : "rgba(255,255,255,0.15)";
  const fillBg = (s: string) =>
    s === "active" ? "rgba(56,189,248,0.12)" : s === "done" ? "rgba(52,211,153,0.08)" : "rgba(255,255,255,0.03)";

  // 回边:reviewer 顶部弧线回到目标顶部;retry>0 表示真实触发过(高亮 amber + ×N)
  const feedback = (target: string, lift: number) => {
    const sx = cxOf("reviewer");
    const tx = cxOf(target);
    const apexY = top - lift;
    const midX = (sx + tx) / 2;
    const fired = (retry[target] ?? 0) > 0;
    return (
      <g key={`fb-${target}`}>
        <path
          d={`M ${sx} ${top} Q ${midX} ${apexY} ${tx} ${top}`}
          fill="none"
          stroke={fired ? "#f59e0b" : "rgba(255,255,255,0.12)"}
          strokeWidth={fired ? 2 : 1}
          strokeDasharray="5 4"
          markerEnd={fired ? "url(#fb-arrow-on)" : "url(#fb-arrow-off)"}
        />
        {fired && (
          <text x={midX} y={apexY - 4} textAnchor="middle" fontSize="11" fill="#fbbf24">
            打回 ×{retry[target]}
          </text>
        )}
      </g>
    );
  };

  return (
    <svg viewBox="0 0 712 200" className="w-full" role="img" aria-label="Agent DAG 任务流转">
      <defs>
        <marker id="fwd-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="rgba(255,255,255,0.4)" />
        </marker>
        <marker id="fb-arrow-on" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="#f59e0b" />
        </marker>
        <marker id="fb-arrow-off" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="rgba(255,255,255,0.2)" />
        </marker>
      </defs>

      {/* 前向边 */}
      {_DAG.slice(0, -1).map((n, i) => {
        const next = _DAG[i + 1];
        const on = (counts.get(n.id) ?? 0) > 0;
        return (
          <line key={`fwd-${n.id}`}
            x1={n.cx + _NODE_W / 2} y1={_ROW_Y} x2={next.cx - _NODE_W / 2} y2={_ROW_Y}
            stroke={on ? "#34d399" : "rgba(255,255,255,0.15)"} strokeWidth="2" markerEnd="url(#fwd-arrow)" />
        );
      })}
      {/* reviewer → END */}
      <line x1={cxOf("reviewer") + _NODE_W / 2} y1={_ROW_Y} x2={_END_CX - 16} y2={_ROW_Y}
        stroke={passed ? "#34d399" : "rgba(255,255,255,0.15)"} strokeWidth="2" markerEnd="url(#fwd-arrow)" />

      {/* 回边(打回闭环)*/}
      {feedback("writer", 34)}
      {feedback("analyzer", 64)}
      {feedback("collector", 94)}

      {/* 节点 */}
      {_DAG.map((n) => {
        const s = stateOf(n.id);
        const rep = (counts.get(n.id) ?? 0) > 1;
        return (
          <g key={n.id}>
            <rect x={n.cx - _NODE_W / 2} y={top} width={_NODE_W} height={_NODE_H} rx="10"
              fill={fillBg(s)} stroke={stroke(s)} strokeWidth="1.5" />
            <text x={n.cx} y={_ROW_Y - 6} textAnchor="middle" fontSize="20">{n.icon}</text>
            <text x={n.cx} y={_ROW_Y + 16} textAnchor="middle" fontSize="12" fill="#d4d4d8">{n.label}</text>
            {rep && (
              <>
                <circle cx={n.cx + _NODE_W / 2 - 4} cy={top + 4} r="9" fill="#f59e0b" />
                <text x={n.cx + _NODE_W / 2 - 4} y={top + 7} textAnchor="middle" fontSize="10" fontWeight="bold" fill="#000">
                  {counts.get(n.id)}
                </text>
              </>
            )}
          </g>
        );
      })}

      {/* END 节点 */}
      <circle cx={_END_CX} cy={_ROW_Y} r="16"
        fill={degraded ? "rgba(245,158,11,0.15)" : passed ? "rgba(52,211,153,0.15)" : "rgba(255,255,255,0.03)"}
        stroke={degraded ? "#f59e0b" : passed ? "#34d399" : "rgba(255,255,255,0.15)"} strokeWidth="1.5" />
      <text x={_END_CX} y={_ROW_Y + 5} textAnchor="middle" fontSize="14">
        {degraded ? "⚠️" : passed ? "✅" : "🏁"}
      </text>
      <text x={_END_CX} y={_ROW_Y + 30} textAnchor="middle" fontSize="11" fill="#a1a1aa">
        {degraded ? "降级" : "完成"}
      </text>
    </svg>
  );
}

type TLStatus = Extract<ProgressEvent, { type: "status" }>;
type TLProgress = Extract<ProgressEvent, { type: "progress" }>;
type TLEvent = TLStatus | TLProgress;

interface AgentGroup {
  id: string;
  node: string;
  icon: string;
  label: string;
  steps: TLStatus[];
  done?: TLProgress;
  startSec: number;
  endSec: number;
  hasStart: boolean;
}

// 流式「已写 N 字」与等待心跳会高频刷新 → 视为同一条「活的」步骤，就地替换而非堆叠。
const _isLive = (m: string) => m.startsWith("✍️ 正在生成") || m.includes("（已用 ");
const _warnOf = (s: TLStatus) =>
  (s as unknown as { analysis_warning?: string }).analysis_warning;

// 把扁平事件流切成「按节点的一次运行」分组；retry 会让同名节点产生多个分组。
function buildAgentGroups(stream: TLEvent[]): AgentGroup[] {
  const groups: AgentGroup[] = [];
  let cur: AgentGroup | null = null;
  let idx = 0;
  for (const e of stream) {
    // 跳过「仍在处理…(已用 Ns)」心跳:只是保活填充,顶部进度条+ETA 已表达,不堆进时间线
    if (e.type === "status" && e.message.includes("（已用 ")) continue;
    if (!cur || cur.node !== e.node || cur.done) {
      cur = {
        id: `${e.node}-${idx++}`,
        node: e.node,
        icon: e.icon,
        label: e.label,
        steps: [],
        startSec: 0,
        endSec: 0,
        hasStart: false,
      };
      groups.push(cur);
    }
    if (e.type === "status") {
      const prev = cur.steps.at(-1);
      if (prev && prev.message === e.message) {
        // 完全重复的消息(start/done 同文案等)→ 跳过,只刷新计时
      } else if (prev && _isLive(prev.message) && _isLive(e.message)) {
        cur.steps[cur.steps.length - 1] = e;
      } else {
        cur.steps.push(e);
      }
      const el = e.elapsed_sec ?? 0;
      if (!cur.hasStart) {
        cur.startSec = el;
        cur.hasStart = true;
      }
      cur.endSec = Math.max(cur.endSec, el);
    } else {
      cur.done = e;
    }
  }
  return groups;
}

function AgentProgress({ events, onCancel }: { events: ProgressEvent[]; onCancel: () => void }) {
  const elapsed = useElapsed(true);

  const stream = events.filter(
    (e): e is TLEvent => e.type === "status" || e.type === "progress"
  );
  const progress = stream.filter((e): e is TLProgress => e.type === "progress");

  // 节点出现次数(含 retry)→ DAG 着色
  const counts = new Map<string, number>();
  for (const e of progress) counts.set(e.node, (counts.get(e.node) ?? 0) + 1);

  const last = stream.at(-1);
  const currentNode = last?.node;
  const evidenceCount = Math.max(0, ...stream.map((e) => e.evidence_count ?? 0));
  const retry = (last?.retry_count ?? {}) as Record<string, number>;
  const totalRetry = Object.values(retry).reduce((a, b) => a + b, 0);
  const degraded = progress.some((e) => e.status === "degraded");
  const passed = progress.some((e) => e.status === "passed");
  const rejected = progress.some((e) => e.reject_target);
  const collectionHealth =
    progress.findLast((e) => e.collection_health)?.collection_health ?? [];

  const order = ["collector", "analyzer", "writer", "reviewer", "degraded_writer"];

  // ── 进度条 + ETA(自校准:用已耗时与当前进度反推总时长,适配不同 runtime profile)──
  const STAGE_SPAN: Record<string, [number, number]> = {
    collector: [3, 25], analyzer: [25, 90], writer: [90, 94],
    reviewer: [94, 99], degraded_writer: [94, 99],
  };
  const STAGE_TYP: Record<string, number> = {
    collector: 55, analyzer: 180, writer: 3, reviewer: 6, degraded_writer: 6,
  };
  const nodeMaxElapsed: Record<string, number> = {};
  for (const e of stream) {
    const el = (e as { elapsed_sec?: number }).elapsed_sec;
    if (typeof el === "number") nodeMaxElapsed[e.node] = Math.max(nodeMaxElapsed[e.node] ?? 0, el);
  }
  let pct = 2;
  if (currentNode) {
    const [lo, hi] = STAGE_SPAN[currentNode] ?? [0, 100];
    const prior = order.filter((n) => n !== currentNode && (counts.get(n) ?? 0) > 0);
    const stageStart = prior.length ? Math.max(...prior.map((n) => nodeMaxElapsed[n] ?? 0)) : 0;
    const frac = Math.min(1, Math.max(0, (elapsed - stageStart) / (STAGE_TYP[currentNode] ?? 60)));
    pct = Math.round(lo + (hi - lo) * frac);
  }
  pct = Math.min(99, Math.max(2, pct));
  const remainSec = pct > 4 ? Math.max(1, Math.round((elapsed * (100 - pct)) / pct)) : null;

  const groups = buildAgentGroups(stream);
  // 默认展开「正在进行」的那一组(最后一个未完成的);其余完成的自动折叠。
  const activeId = groups.length
    ? (groups.find((g) => !g.done)?.id ?? groups[groups.length - 1].id)
    : undefined;

  return (
    <div className="space-y-5 py-6">
      {/* 顶部总览 */}
      <div className="flex flex-wrap items-center justify-center gap-4 text-sm text-neutral-400">
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
        <button
          onClick={onCancel}
          className="rounded-lg border border-white/10 px-2.5 py-1 text-xs text-neutral-500 transition hover:border-red-400/40 hover:text-red-300"
        >
          取消
        </button>
      </div>

      {/* 进度条 + ETA */}
      <div className="mx-auto max-w-2xl">
        <div className="mb-1 flex items-center justify-between text-xs text-neutral-500">
          <span>进度 {pct}%</span>
          {remainSec != null && (
            <span>{pct >= 99 ? "即将完成…" : `预计剩余 ~${remainSec}s`}</span>
          )}
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
          <div
            className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-400 transition-all duration-500"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      {/* LangGraph DAG 任务流转(含质检打回回边,实时着色) */}
      <div className="mx-auto max-w-2xl rounded-xl border border-white/10 bg-white/[0.02] p-3">
        <div className="mb-1 flex items-center gap-3 px-1 text-[11px] text-neutral-500">
          <span>LangGraph 任务流转</span>
          <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-sky-400" />进行中</span>
          <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />完成</span>
          <span className="flex items-center gap-1"><span className="inline-block h-2 w-3 bg-amber-500" style={{ clipPath: "polygon(0 40%,100% 40%,100% 60%,0 60%)" }} />质检打回</span>
        </div>
        <DagFlow
          counts={counts}
          currentNode={currentNode}
          retry={retry}
          degraded={degraded}
          passed={passed}
        />
      </div>

      {collectionHealth.length > 0 && (
        <div className="mx-auto max-w-2xl rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
          <div className="mb-2 font-medium text-amber-300">证据覆盖不足</div>
          <div className="space-y-1 text-xs text-amber-100/80">
            {collectionHealth.map((item) => (
              <div key={item.product}>
                {item.product}：{item.health}，缺少 {item.missing_claim_types.join(" / ")}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Claude 式可折叠活动时间线:每个动作实时流入,完成即折叠成一行摘要 */}
      <div className="mx-auto max-w-2xl">
        <div className="mb-2 px-1 text-xs font-medium text-neutral-400">Agent 活动时间线</div>
        <div className="space-y-2">
          {groups.length === 0 && (
            <div className="rounded-xl border border-white/5 bg-white/[0.02] px-4 py-3 text-xs text-neutral-500">
              正在连接分析服务…
            </div>
          )}
          {groups.map((g) => (
            <AgentGroupCard key={g.id} group={g} active={g.id === activeId} />
          ))}
        </div>
      </div>

      {rejected && (
        <p className="text-center text-xs text-neutral-500">
          Reviewer 发现问题并打回重跑 — 这正是质量自愈闭环在工作
        </p>
      )}
    </div>
  );
}

function AgentGroupCard({ group, active }: { group: AgentGroup; active: boolean }) {
  // override=null 时跟随 active(进行中自动展开/完成自动折叠);用户点击后锁定其选择。
  const [override, setOverride] = useState<boolean | null>(null);
  const expanded = override ?? active;
  const running = !group.done;
  const dur = Math.max(0, group.endSec - group.startSec);

  const rejectTarget = group.done?.reject_target ?? null;
  const summary =
    group.done?.result ||
    group.steps.at(-1)?.message ||
    (running ? "处理中…" : "完成");

  // analyzer 的实时分析结论(从该组各步骤携带的 preview 汇总)
  const previews = group.steps
    .map((s) => s.analysis_preview)
    .filter(Boolean) as AnalysisPreview[];
  const overviewPreview = previews.findLast((p) => p.kind === "overview");
  const factsPreview = previews.findLast((p) => p.kind === "facts");
  const derivationsPreview = previews.findLast((p) => p.kind === "derivations");
  const latestPreview = previews.at(-1) ?? null;

  const stateColor = running
    ? "border-sky-500/40 bg-sky-500/[0.06]"
    : rejectTarget
      ? "border-amber-500/40 bg-amber-500/[0.05]"
      : "border-emerald-500/30 bg-emerald-500/[0.04]";

  return (
    <div className={`rounded-xl border ${stateColor} transition-colors`}>
      <button
        onClick={() => setOverride(!expanded)}
        className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left"
      >
        <span className="text-lg">{group.icon}</span>
        <span className="text-sm font-medium text-neutral-200">{group.label}</span>
        {running ? (
          <span className="flex items-center gap-1 text-[11px] text-sky-400">
            <span className="inline-block h-1.5 w-1.5 animate-ping rounded-full bg-sky-400" />
            进行中
          </span>
        ) : rejectTarget ? (
          <span className="text-[11px] text-amber-400">↩ 打回 {rejectTarget}</span>
        ) : (
          <span className="text-[11px] text-emerald-400">✓ 完成</span>
        )}
        {dur > 0 && <span className="font-mono text-[11px] text-neutral-600">{dur}s</span>}
        <span className="ml-auto flex items-center gap-2">
          {!expanded && (
            <span className="hidden max-w-[260px] truncate text-[11px] text-neutral-500 sm:inline">
              {summary}
            </span>
          )}
          <span className="text-[11px] text-neutral-500">{group.steps.length} 步</span>
          <svg
            className={`h-3.5 w-3.5 text-neutral-500 transition-transform ${expanded ? "rotate-90" : ""}`}
            viewBox="0 0 12 12" fill="none"
          >
            <path d="M4 2.5L8 6L4 9.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>

      {expanded && (
        <div className="border-t border-white/5 px-3 py-3">
          {/* 子步骤时间线 */}
          <ol className="space-y-2">
            {group.steps.map((s, i) => {
              const isLast = i === group.steps.length - 1;
              const live = running && isLast;
              const warn = _warnOf(s);
              const samples = s.samples;
              return (
                <li key={i} className="flex gap-2.5">
                  <div className="flex flex-col items-center pt-1">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        live ? "animate-ping bg-sky-400" : warn ? "bg-amber-400" : "bg-neutral-600"
                      }`}
                    />
                    {!isLast && <span className="mt-1 w-px flex-1 bg-white/10" />}
                  </div>
                  <div className="min-w-0 flex-1 pb-0.5">
                    <div className="flex items-baseline gap-2">
                      <span className={`text-xs leading-relaxed ${live ? "text-sky-200" : "text-neutral-300"}`}>
                        {s.message}
                      </span>
                      {typeof s.elapsed_sec === "number" && (
                        <span className="shrink-0 font-mono text-[10px] text-neutral-600">+{s.elapsed_sec}s</span>
                      )}
                    </div>
                    {warn && <div className="mt-1 text-[11px] text-amber-300/80">{warn}</div>}
                    {samples && samples.length > 0 && (
                      <ul className="mt-1.5 space-y-1 border-l border-white/10 pl-3">
                        {samples.map((sm, j) => (
                          <li key={j} className="text-[11px] leading-relaxed text-neutral-500">
                            <span className="text-neutral-600">[{sm.source}]</span> {sm.text}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>

          {/* analyzer:实时分析结论 */}
          {group.node === "analyzer" && previews.length > 0 && (
            <div className="mt-3">
              <AnalysisLivePanel
                overview={overviewPreview}
                facts={factsPreview}
                derivations={derivationsPreview}
                latest={latestPreview}
              />
            </div>
          )}

          {/* 节点结构化产出明细(采集来源/搜索 URL/功能/建议等) */}
          {group.done?.detail && (
            <div className="mt-3">
              <NodeDetailCard icon={group.icon} label="本步产出" detail={group.done.detail} />
            </div>
          )}

          {/* reviewer:规则质检结果 */}
          {group.done?.quality && (
            <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.02] p-3 text-sm">
              <div className="mb-2 flex items-center justify-between text-neutral-400">
                <span>规则质检</span>
                {group.done.quality.quality_score != null && (
                  <span className="font-mono text-emerald-400">{group.done.quality.quality_score}/100</span>
                )}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(group.done.quality.passed_rules ?? []).map((r) => (
                  <span key={r} className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">✓ {r}</span>
                ))}
                {(group.done.quality.warning_rules ?? []).map((r) => (
                  <span key={`w-${r}`} className="rounded bg-amber-500/15 px-2 py-0.5 text-xs text-amber-400">⚠ {r}</span>
                ))}
                {(group.done.quality.failed_rules ?? []).map((r) => (
                  <span key={`f-${r}`} className="rounded bg-red-500/15 px-2 py-0.5 text-xs text-red-400">✕ {r}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AnalysisLivePanel({
  overview,
  facts,
  derivations,
  latest,
}: {
  overview?: AnalysisPreview;
  facts?: AnalysisPreview;
  derivations?: AnalysisPreview;
  latest: AnalysisPreview | null;
}) {
  const fallback = Boolean(latest?.fallback || facts?.fallback || derivations?.fallback);
  const productCounts = overview?.evidence?.products ?? [];
  const claimCounts = overview?.evidence?.claim_types ?? [];
  const features = facts?.features ?? [];
  const signals = overview?.signals ?? [];
  const pains = facts?.pain_points ?? [];
  const pricing = facts?.pricing ?? [];
  const recs = derivations?.recommendations ?? [];
  const swot = derivations?.swot;

  return (
    <div className="mx-auto max-w-xl rounded-xl border border-cyan-500/20 bg-cyan-500/[0.06] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-cyan-100">实时分析结论</div>
          <div className="mt-0.5 text-xs text-neutral-500">
            先展示已抽取内容，最终报告仍会经过 Writer 与 Reviewer
          </div>
        </div>
        {fallback && (
          <span className="rounded-md border border-amber-400/30 bg-amber-400/10 px-2 py-1 text-xs text-amber-200">
            已启用保守降级
          </span>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-[0.9fr_1.1fr]">
        <section className="min-w-0">
          <div className="mb-2 text-xs font-medium text-neutral-400">已读证据</div>
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {productCounts.length > 0 ? productCounts.map((p) => (
                <span key={p.product} className="rounded bg-white/[0.06] px-2 py-1 text-xs text-neutral-300">
                  {p.product} <span className="font-mono text-cyan-300">{p.count}</span>
                </span>
              )) : (
                <span className="text-xs text-neutral-500">等待证据概览</span>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {claimCounts.slice(0, 4).map((c) => (
                <span key={c.label} className="rounded bg-emerald-500/10 px-2 py-1 text-xs text-emerald-200/90">
                  {c.label} ×{c.count}
                </span>
              ))}
            </div>
          </div>
        </section>

        <section className="min-w-0">
          <div className="mb-2 text-xs font-medium text-neutral-400">事实层预览</div>
          <div className="space-y-2">
            <div className="flex flex-wrap gap-1.5">
              {features.length > 0 ? features.map((f) => (
                <span key={f} className="max-w-full truncate rounded bg-sky-500/10 px-2 py-1 text-xs text-sky-200">
                  {f}
                </span>
              )) : (
                <span className="text-xs text-neutral-500">功能/定价/痛点抽取中</span>
              )}
            </div>
            {pricing.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {pricing.map((p) => (
                  <span key={p} className="max-w-full truncate rounded bg-white/[0.06] px-2 py-1 text-xs text-neutral-300">
                    {p}
                  </span>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <section className="min-w-0">
          <div className="mb-2 text-xs font-medium text-neutral-400">
            {pains.length > 0 ? "用户痛点" : "证据线索"}
          </div>
          <div className="space-y-1.5">
            {pains.length > 0 ? pains.map((p, i) => (
              <div key={`${p}-${i}`} className="line-clamp-2 text-xs leading-relaxed text-neutral-300">
                {i + 1}. {p}
              </div>
            )) : signals.length > 0 ? signals.map((s, i) => (
              <div key={`${s}-${i}`} className="line-clamp-2 text-xs leading-relaxed text-neutral-300">
                {i + 1}. {s}
              </div>
            )) : (
              <div className="text-xs text-neutral-500">等待事实层返回</div>
            )}
          </div>
        </section>

        <section className="min-w-0">
          <div className="mb-2 text-xs font-medium text-neutral-400">初步建议</div>
          <div className="space-y-1.5">
            {recs.length > 0 ? recs.map((r, i) => (
              <div key={`${r.action}-${i}`} className="text-xs leading-relaxed text-neutral-300">
                {r.priority && <span className="mr-1 text-cyan-300">{r.priority}</span>}
                {r.action}
              </div>
            )) : (
              <div className="text-xs text-neutral-500">事实层完成后开始推导</div>
            )}
          </div>
        </section>
      </div>

      {swot && (
        <div className="mt-4 flex flex-wrap gap-1.5 border-t border-white/10 pt-3">
          <span className="rounded bg-white/[0.05] px-2 py-1 text-xs text-neutral-400">SWOT</span>
          <span className="rounded bg-emerald-500/10 px-2 py-1 text-xs text-emerald-300">优势 {swot.strengths ?? 0}</span>
          <span className="rounded bg-red-500/10 px-2 py-1 text-xs text-red-300">劣势 {swot.weaknesses ?? 0}</span>
          <span className="rounded bg-sky-500/10 px-2 py-1 text-xs text-sky-300">机会 {swot.opportunities ?? 0}</span>
          <span className="rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-300">威胁 {swot.threats ?? 0}</span>
        </div>
      )}
    </div>
  );
}

function NodeDetailCard({
  icon,
  label,
  detail,
}: {
  icon: string;
  label: string;
  detail: NodeDetail;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-3 text-xs">
      <div className="mb-2 flex items-center gap-1.5 text-neutral-300">
        <span>{icon}</span>
        <span className="font-medium">{label}</span>
      </div>

      {detail.kind === "collection" && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {detail.coverage?.map((c) => (
              <span
                key={c.label}
                className={`rounded px-2 py-0.5 ${
                  c.ok
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "bg-red-500/15 text-red-300"
                }`}
              >
                {c.ok ? "✓" : "✕"} {c.label} {c.ok ? `×${c.count}` : "未获取"}
              </span>
            ))}
          </div>
          {detail.sources && detail.sources.length > 0 && (
            <div className="text-neutral-500">
              来源：
              {detail.sources.map((s) => `${s.label} ×${s.count}`).join(" · ")}
            </div>
          )}
          {detail.products?.some((p) => p.missing.length > 0) && (
            <div className="text-amber-300/80">
              {detail.products
                .filter((p) => p.missing.length > 0)
                .map((p) => `${p.product} 缺 ${p.missing.join("/")}`)
                .join("；")}
            </div>
          )}
          {detail.searched && detail.searched.length > 0 && (
            <div className="space-y-1.5 border-t border-white/5 pt-2">
              <div className="text-neutral-500">🔍 网络检索了这些来源：</div>
              {detail.searched.map((s, i) => (
                <div key={i} className="rounded-lg bg-white/[0.02] px-2 py-1.5">
                  <div className="text-neutral-400">
                    <span className="text-sky-400">{s.product}</span>
                    {s.site && <span className="text-neutral-500"> · {s.site}</span>}
                    <span className="text-neutral-600"> · 命中 {s.count}</span>
                    <span className="ml-1 text-neutral-600">「{s.query}」</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {s.urls.map((u) => (
                      <a
                        key={u}
                        href={u}
                        target="_blank"
                        rel="noreferrer"
                        className="max-w-[180px] truncate rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-neutral-400 transition hover:text-sky-300"
                      >
                        {domainOf(u)}
                      </a>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {detail.kind === "analysis" && (
        <div className="space-y-2">
          {detail.features && detail.features.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {detail.features.map((f) => (
                <span key={f} className="rounded bg-white/5 px-2 py-0.5 text-neutral-300">
                  {f}
                </span>
              ))}
            </div>
          )}
          {detail.recommendations && detail.recommendations.length > 0 && (
            <div className="space-y-1 text-neutral-400">
              {detail.recommendations.map((r, i) => (
                <div key={i}>
                  {r.priority && <span className="text-sky-400">{r.priority}</span>} {r.action}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {detail.kind === "review" && (
        <div className="flex flex-wrap gap-1.5">
          {detail.passed?.map((r) => (
            <span key={r} className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-400">
              ✓ {r}
            </span>
          ))}
          {detail.warnings?.map((r) => (
            <span key={`w-${r}`} className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-400">
              ⚠ {r}
            </span>
          ))}
          {detail.failed?.map((r) => (
            <span key={`f-${r}`} className="rounded bg-red-500/15 px-1.5 py-0.5 text-red-400">
              ✕ {r}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
