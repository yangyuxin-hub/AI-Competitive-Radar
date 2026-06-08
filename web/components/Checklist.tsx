"use client";

import { useEffect, useState } from "react";
import { fetchChecklist, type Checklist as ChecklistData, type ChecklistItem, type RunArgs } from "@/lib/api";
import type { StageReport } from "@/lib/types";

const OWNER_LABEL: Record<string, string> = {
  fetch_official: "采集·官网",
  fetch_community: "采集·社区",
  backfill: "采集·兜底",
  collector: "采集",
  analyzer: "分析",
  writer: "渲染",
};

const STATUS_ICON: Record<string, { mark: string; cls: string }> = {
  ok: { mark: "✓", cls: "text-emerald-400" },
  missing: { mark: "✗", cls: "text-rose-400" },
  unfixable: { mark: "✗", cls: "text-neutral-500" },
  warn: { mark: "⚠", cls: "text-amber-400" },
};

type ChecklistMode = "idle" | "running" | "done" | "error" | "cancelled";
type UiState = "pending" | "running";
type RenderItem = ChecklistItem & { uiState?: UiState; todoKey?: string };
type RenderGroup = Omit<ChecklistData["groups"][number], "items"> & { items: RenderItem[] };
type RenderChecklist = Omit<ChecklistData, "groups"> & { groups: RenderGroup[] };

interface ChecklistProps {
  mode?: ChecklistMode;
  reportId?: string | null;
  liveStageReports?: StageReport[];
  runArgs?: RunArgs | null;
  message?: string | null;
}

const STAGE_ORDER = ["evidence_planner", "collector", "analyzer", "writer", "reviewer", "degraded_writer"];

const STAGE_LABEL: Record<string, string> = {
  evidence_planner: "规划",
  collector: "采集",
  analyzer: "分析",
  writer: "撰写",
  reviewer: "质检",
  degraded_writer: "降级撰写",
};

const CLAIM_LABEL: Record<string, string> = {
  feature_existence: "功能证据",
  performance_quality: "体验/性能证据",
  pricing: "定价证据",
  user_pain: "用户痛点证据",
  market_signal: "市场/公司信号",
};

const DEFAULT_CLAIMS = ["feature_existence", "performance_quality", "pricing", "user_pain"];
const PAIN_CLAIMS = ["user_pain", "performance_quality"];

function pendingItem(label: string, owner: string, uiState: UiState = "pending", todoKey?: string): RenderItem {
  return {
    label,
    status: "warn",
    detail: uiState === "running" ? "进行中" : "等待执行",
    owner,
    fix: null,
    fixable: true,
    uiState,
    todoKey,
  };
}

function statusFromCheck(check: StageReport["checks"][number]): ChecklistItem["status"] {
  if (check.verdict === "pass") return "ok";
  return check.severity === "error" ? "missing" : "warn";
}

function statusFromStage(report: StageReport): ChecklistItem["status"] {
  if (report.status === "ok") return "ok";
  return report.status === "failed" ? "missing" : "warn";
}

function latestByStage(reports: StageReport[]) {
  const m = new Map<string, StageReport>();
  for (const r of reports) m.set(r.stage, r);
  return m;
}

function currentStageOf(reports: StageReport[]) {
  if (reports.length === 0) return "evidence_planner";
  const last = reports.at(-1)?.stage;
  if (!last) return "evidence_planner";
  if (last === "reviewer" && reports.at(-1)?.status === "ok") return null;
  const idx = STAGE_ORDER.indexOf(last);
  return STAGE_ORDER[Math.min(idx + 1, STAGE_ORDER.length - 1)] ?? null;
}

function plannerItems(report: StageReport | undefined, active = false): RenderItem[] {
  const base: RenderItem[] = [
    pendingItem("确定必需证据类型", "collector", active ? "running" : "pending", "plan_has_required_claims"),
    pendingItem("生成采集任务清单", "collector", active ? "running" : "pending", "plan_has_tasks"),
  ];
  if (!report) return base;
  return applyChecks(base, report).map((item) => {
    const required = Array.isArray(report.produced.required) ? report.produced.required as string[] : [];
    const tasks = Number(report.produced.evidence_tasks ?? 0);
    if (item.todoKey === "plan_has_required_claims") {
      return {
        ...item,
        status: required.length > 0 ? "ok" : statusFromStage(report),
        detail: required.length > 0 ? required.map((ct) => CLAIM_LABEL[ct] ?? ct).join(" / ") : "未生成",
        uiState: undefined,
      };
    }
    return {
      ...item,
      status: tasks > 0 ? "ok" : statusFromStage(report),
      detail: tasks > 0 ? `${tasks} 项任务` : "未生成",
      uiState: undefined,
    };
  });
}

function requiredClaims(runArgs?: RunArgs | null, plannerReport?: StageReport) {
  const required = plannerReport?.produced?.required;
  if (Array.isArray(required) && required.length > 0) return required as string[];
  return runArgs?.analysis_intent === "pain_attribution" ? PAIN_CLAIMS : DEFAULT_CLAIMS;
}

function productsOf(runArgs?: RunArgs | null) {
  return [runArgs?.target_product, ...(runArgs?.competitors ?? [])].filter(Boolean) as string[];
}

function applyChecks(items: RenderItem[], report?: StageReport) {
  if (!report) return items;
  const byId = new Map(report.checks.map((c) => [c.check_id, c]));
  return items.map((item) => {
    if (!item.todoKey) return item;
    const check = byId.get(item.todoKey);
    if (!check) return item;
    return {
      ...item,
      status: statusFromCheck(check),
      detail: check.detail || "已完成",
      fix: check.verdict === "pass" ? null : "按该检查项修复",
      uiState: undefined,
    };
  });
}

function collectorItems(
  report: StageReport | undefined,
  runArgs?: RunArgs | null,
  active = false,
  plannerReport?: StageReport
): RenderItem[] {
  const products = productsOf(runArgs);
  const claims = requiredClaims(runArgs, plannerReport);
  if (products.length === 0) return [pendingItem("确认采集对象", "collector", active ? "running" : "pending")];

  const gapByKey = new Map(
    (report?.gaps ?? [])
      .filter((g) => g.product && g.claim_type)
      .map((g) => [`${g.product}:${g.claim_type}`, g])
  );

  return products.flatMap((product) =>
    claims.map((claimType) => {
      const key = `${product}:${claimType}`;
      const gap = gapByKey.get(key);
      if (!report) {
        return pendingItem(
          `${product} · ${CLAIM_LABEL[claimType] ?? claimType}`,
          claimType === "pricing" || claimType === "feature_existence" ? "fetch_official" : "fetch_community",
          active ? "running" : "pending",
          key
        );
      }
      if (gap) {
        return {
          label: `${product} · ${CLAIM_LABEL[claimType] ?? claimType}`,
          status: gap.fixable === false ? "unfixable" : "missing",
          detail: gap.gap_type,
          owner: gap.owner_node,
          fix: "定向补齐缺口",
          fixable: gap.fixable,
          product,
          claim_type: claimType,
          todoKey: key,
        };
      }
      return {
        label: `${product} · ${CLAIM_LABEL[claimType] ?? claimType}`,
        status: "ok",
        detail: "已覆盖",
        owner: null,
        fix: null,
        fixable: true,
        product,
        claim_type: claimType,
        todoKey: key,
      };
    })
  );
}

function analyzerItems(report: StageReport | undefined, active = false): RenderItem[] {
  const base: RenderItem[] = [
    pendingItem("抽取事实层", "analyzer", active ? "running" : "pending", "facts_present"),
    pendingItem("校验证据引用", "analyzer", active ? "running" : "pending", "evidence_ref_integrity"),
    pendingItem("保持 unknown 诚实标注", "analyzer", active ? "running" : "pending", "unknown_policy"),
    pendingItem("生成推导与建议", "analyzer", active ? "running" : "pending", "derivations_present"),
  ];
  if (!report) return base;
  return applyChecks(base, report).map((item) => {
    if (item.todoKey === "unknown_policy") {
      const unknownHits = Number(report.produced.unknown_hits ?? 0);
      return {
        ...item,
        status: "ok",
        detail: unknownHits > 0 ? `unknown ${unknownHits} 处` : "无异常占位",
        uiState: undefined,
      };
    }
    if (item.todoKey === "derivations_present") {
      return {
        ...item,
        status: statusFromStage(report),
        detail: report.status === "ok" ? "已完成" : "需复核",
        uiState: undefined,
      };
    }
    return { ...item, uiState: undefined };
  });
}

function writerItems(report: StageReport | undefined, active = false): RenderItem[] {
  const base: RenderItem[] = [
    pendingItem("生成报告正文", "writer", active ? "running" : "pending", "report_nonempty"),
    pendingItem("插入证据 chip", "writer", active ? "running" : "pending", "chips_present"),
    pendingItem("不泄露质检分", "writer", active ? "running" : "pending", "no_score_leak"),
  ];
  if (!report) return base;
  return applyChecks(base, report).map((item) => {
    if (item.todoKey === "chips_present") {
      const chips = Number(report.produced.chips ?? 0);
      return {
        ...item,
        status: chips > 0 ? "ok" : "warn",
        detail: chips > 0 ? `${chips} 个 chip` : "暂无 chip 统计",
        fix: chips > 0 ? null : "补充 [SXXXXXXX] 溯源标记",
        uiState: undefined,
      };
    }
    return { ...item, uiState: undefined };
  });
}

function reviewerItems(report: StageReport | undefined, active = false): RenderItem[] {
  const base: RenderItem[] = [
    pendingItem("检查硬门规则", "reviewer", active ? "running" : "pending", "hard_gates"),
    pendingItem("记录 warning 规则", "reviewer", active ? "running" : "pending", "warning_rules"),
    pendingItem("给出最终状态", "reviewer", active ? "running" : "pending", "final_status"),
  ];
  if (!report) return base;
  const failed = Number(report.produced.failed_rules ?? 0);
  const warnings = Number(report.produced.warning_rules ?? 0);
  return base.map((item) => {
    if (item.todoKey === "hard_gates") {
      return {
        ...item,
        status: failed > 0 ? "missing" : "ok",
        detail: failed > 0 ? `${failed} 条失败规则` : "硬门通过",
        fix: failed > 0 ? "按失败规则修复后重跑" : null,
        uiState: undefined,
      };
    }
    if (item.todoKey === "warning_rules") {
      return {
        ...item,
        status: warnings > 0 ? "warn" : "ok",
        detail: warnings > 0 ? `${warnings} 条 warning` : "无 warning",
        uiState: undefined,
      };
    }
    return {
      ...item,
      status: statusFromStage(report),
      detail: report.produced.status ? String(report.produced.status) : report.status,
      uiState: undefined,
    };
  });
}

function buildLiveChecklist(reports: StageReport[], runArgs?: RunArgs | null): RenderChecklist {
  const byStage = latestByStage(reports);
  const currentStage = currentStageOf(reports);
  const visibleStages = STAGE_ORDER.filter((stage) => stage !== "degraded_writer" || byStage.has(stage));
  const plannerReport = byStage.get("evidence_planner");
  const groups: RenderGroup[] = visibleStages.map((stage) => {
    const report = byStage.get(stage);
    const active = currentStage === stage;
    const items =
      stage === "evidence_planner" ? plannerItems(report, active)
        : stage === "collector" ? collectorItems(report, runArgs, active, plannerReport)
        : stage === "analyzer" ? analyzerItems(report, active)
          : stage === "writer" || stage === "degraded_writer" ? writerItems(report, active)
            : reviewerItems(report, active);
    const done = items.filter((it) => it.status === "ok").length;
    return { layer: STAGE_LABEL[stage] ?? stage, done, total: items.length, items };
  });

  const allItems = groups.flatMap((g) => g.items);
  const openRepairs = allItems.filter((it) => it.status === "missing");
  const blocked = allItems.filter((it) => it.status === "unfixable");
  const repairsByOwner = openRepairs.reduce<Record<string, number>>((acc, it) => {
    if (it.owner) acc[it.owner] = (acc[it.owner] ?? 0) + 1;
    return acc;
  }, {});

  return {
    report_id: reports.at(-1)?.run_id ? `run: ${reports.at(-1)?.run_id}` : "本次运行",
    target: runArgs?.target_product ?? null,
    competitors: runArgs?.competitors ?? [],
    status: "running",
    groups,
    summary: {
      done: allItems.filter((it) => it.status === "ok").length,
      total: allItems.length,
      open_repairs: openRepairs.length,
      blocked: blocked.length,
      repairs_by_owner: repairsByOwner,
    },
  };
}

function Item({ it }: { it: RenderItem }) {
  const ic =
    it.uiState === "running"
      ? { mark: "…", cls: "text-sky-400" }
      : it.uiState === "pending"
        ? { mark: "•", cls: "text-neutral-600" }
        : STATUS_ICON[it.status] ?? STATUS_ICON.ok;
  const done = it.status === "ok";
  return (
    <li className="flex items-start gap-2.5 py-1.5">
      <span className={`mt-0.5 w-4 shrink-0 text-center text-sm font-bold ${ic.cls}`}>{ic.mark}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-sm ${done ? "text-neutral-300" : it.uiState ? "text-neutral-500" : "text-neutral-100"}`}>{it.label}</span>
          {it.detail && <span className="text-xs text-neutral-500">{it.detail}</span>}
        </div>
        {!it.uiState && it.status !== "ok" && it.status !== "warn" && (
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
            {it.status === "unfixable" ? (
              <span className="rounded bg-white/5 px-2 py-0.5 text-neutral-400">客观补不到 · 已诚实标注 unknown</span>
            ) : (
              <>
                <span className="text-neutral-500">→ 打回</span>
                <span className="rounded bg-sky-500/10 px-2 py-0.5 text-sky-300">
                  {it.owner ? OWNER_LABEL[it.owner] ?? it.owner : "?"}
                </span>
                {it.fix && <span className="text-neutral-400">{it.fix}</span>}
              </>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

export default function Checklist({
  mode = "idle",
  reportId = null,
  liveStageReports = [],
  runArgs = null,
  message = null,
}: ChecklistProps) {
  const [finalState, setFinalState] = useState<{
    reportId: string | null;
    data: RenderChecklist | null;
    err: string | null;
  }>({ reportId: null, data: null, err: null });

  useEffect(() => {
    let alive = true;
    if (mode !== "done" || !reportId) {
      return () => {
        alive = false;
      };
    }

    const load = () => {
      fetchChecklist(reportId)
        .then((d) => {
          if (alive) setFinalState({ reportId, data: d, err: null });
        })
        .catch((e) => {
          if (alive) setFinalState({ reportId, data: null, err: String(e) });
        });
    };
    load();
    // 只刷新当前 report_id,不回退到"最近一次报告"。
    const onRefresh = () => load();
    window.addEventListener("focus", onRefresh);
    return () => {
      alive = false;
      window.removeEventListener("focus", onRefresh);
    };
  }, [mode, reportId]);

  const liveData =
    mode === "running" || mode === "error" || mode === "cancelled"
      ? buildLiveChecklist(liveStageReports, runArgs)
      : null;
  const doneStateMatches = mode === "done" && !!reportId && finalState.reportId === reportId;
  const data = liveData ?? (doneStateMatches ? finalState.data : null);
  const err = doneStateMatches ? finalState.err : null;

  if (mode === "done" && reportId && !doneStateMatches) {
    return <div className="text-sm text-neutral-500">加载中…</div>;
  }
  if (err) return <div className="text-sm text-rose-400">加载失败：{err}</div>;
  if (!data || !data.report_id || data.summary.total === 0)
    return (
      <div className="text-sm text-neutral-500">
        开始一次分析后，这里会按本次任务逐条显示采集、分析、撰写和质检进度。
      </div>
    );

  const { groups, summary, target, competitors, report_id } = data;
  const pct = summary.total ? Math.round((summary.done / summary.total) * 100) : 0;
  const repairs = Object.entries(summary.repairs_by_owner ?? {});

  return (
    <section className="space-y-5">
      <div className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-neutral-200">
              交付清单 · {target ?? "?"}
              {competitors.length > 0 && <span className="text-neutral-500"> vs {competitors.join(" / ")}</span>}
            </h2>
            <p className="mt-0.5 text-xs text-neutral-500">
              {mode === "done" ? "report" : "current"}: {report_id}
              {message && <span className="ml-1 text-amber-300">{message}</span>}
            </p>
          </div>
          <div className="text-right">
            <div className="text-lg font-semibold text-neutral-100">
              {summary.done}<span className="text-sm text-neutral-500">/{summary.total} 达标</span>
            </div>
          </div>
        </div>

        <div className="h-2 w-full overflow-hidden rounded-full bg-white/5">
          <div className="h-full rounded-full bg-emerald-400/70" style={{ width: `${pct}%` }} />
        </div>

        {(repairs.length > 0 || summary.blocked > 0) && (
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {repairs.map(([owner, n]) => (
              <span key={owner} className="rounded bg-rose-500/10 px-2 py-0.5 text-rose-300">
                还欠 {OWNER_LABEL[owner] ?? owner} 补 {n} 项
              </span>
            ))}
            {summary.blocked > 0 && (
              <span className="rounded bg-white/5 px-2 py-0.5 text-neutral-400">{summary.blocked} 项客观补不到</span>
            )}
          </div>
        )}
      </div>

      <div className="space-y-3">
        {groups.map((g) => (
          <div key={g.layer} className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-neutral-300">{g.layer}层</span>
              <span className="text-xs text-neutral-500">{g.done}/{g.total} 达标</span>
            </div>
            <ul className="mt-1.5 divide-y divide-white/[0.04]">
              {g.items.map((it, i) => (
                <Item key={`${it.label}-${i}`} it={it} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
