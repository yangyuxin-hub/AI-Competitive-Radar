"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { Evidence } from "@/lib/types";
import { domainOf, faviconOf, biasLabel } from "@/lib/source";

const EvidenceCtx = createContext<{
  open: (id: string) => void;
  get: (id: string) => Evidence | undefined;
}>({ open: () => {}, get: () => undefined });

export function EvidenceProvider({
  evidence,
  children,
}: {
  evidence: Evidence[];
  children: React.ReactNode;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const map = new Map(evidence.map((e) => [e.evidence_id, e]));
  const active = activeId ? map.get(activeId) ?? null : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setActiveId(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <EvidenceCtx.Provider value={{ open: setActiveId, get: (id) => map.get(id) }}>
      {children}
      {active && <EvidencePanel evidence={active} onClose={() => setActiveId(null)} />}
    </EvidenceCtx.Provider>
  );
}

function Favicon({ url, size = 14 }: { url?: string; size?: number }) {
  const src = faviconOf(url);
  const [failed, setFailed] = useState(false);
  const d = domainOf(url);
  if (!src || failed) {
    return (
      <span
        className="inline-flex items-center justify-center rounded-sm bg-sky-500/30 text-[8px] font-bold text-sky-200"
        style={{ width: size, height: size }}
      >
        {d.charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      className="rounded-sm"
      onError={() => setFailed(true)}
    />
  );
}

export function Chip({ id }: { id: string }) {
  const { open, get } = useContext(EvidenceCtx);
  const ev = get(id);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  if (!ev) {
    return (
      <sup className="mx-0.5 rounded bg-white/5 px-1 text-[10px] text-neutral-600" title="证据缺失">
        ?
      </sup>
    );
  }

  const domain = domainOf(ev.source_url);

  // 浮层定位:贴着 chip 下方,左右夹到视口内,靠近底部时翻到 chip 上方。
  // 用 Portal 渲染到 body,避免被带 transform/动画(ca-stagger)的祖先当成包含块而整体偏移。
  const PANEL_W = 320;
  const place = (el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const left = Math.max(8, Math.min(r.left, window.innerWidth - PANEL_W - 8));
    const estH = 132;
    const top = r.bottom + 6 + estH > window.innerHeight
      ? Math.max(8, r.top - estH - 6) // 翻到上方
      : r.bottom + 6;
    setPos({ left, top });
  };

  const panel = pos && (
    <div
      className="fixed z-[80] rounded-xl border border-white/10 bg-neutral-900 p-3 text-left shadow-2xl"
      style={{ left: pos.left, top: pos.top, width: PANEL_W }}
    >
      <div className="mb-1.5 flex items-center gap-1.5">
        <Favicon url={ev.source_url} size={16} />
        <span className="truncate text-xs text-neutral-300">{domain}</span>
        <span className="ml-auto shrink-0 rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-neutral-400">
          {biasLabel(ev.source_bias)} · 可信度 {ev.source_reliability}
        </span>
      </div>
      <p className="line-clamp-3 text-xs leading-relaxed text-neutral-400">
        {ev.extracted_snippet || ev.claim}
      </p>
      <div className="mt-1.5 text-[10px] text-sky-500">点击查看完整原文 →</div>
    </div>
  );

  return (
    <span className="relative inline-block align-baseline">
      <button
        onClick={() => open(id)}
        onMouseEnter={(e) => place(e.currentTarget)}
        onMouseLeave={() => setPos(null)}
        className="mx-0.5 inline-flex max-w-[150px] items-center gap-1 truncate rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 align-baseline text-[11px] text-neutral-300 transition hover:border-sky-500/40 hover:text-sky-300"
      >
        <Favicon url={ev.source_url} />
        <span className="truncate">{domain}</span>
      </button>

      {panel && typeof document !== "undefined" && createPortal(panel, document.body)}
    </span>
  );
}

function SourceGroup({ ids }: { ids: string[] }) {
  const { open, get } = useContext(EvidenceCtx);
  const evs = ids.map((id) => get(id)).filter((e): e is Evidence => Boolean(e));
  if (!evs.length) return null;
  const label = `${evs.length} source${evs.length > 1 ? "s" : ""}`;
  const title = evs
    .slice(0, 4)
    .map((e) => `${domainOf(e.source_url)} · ${biasLabel(e.source_bias)}`)
    .join("\n");
  return (
    <button
      type="button"
      onClick={() => open(evs[0].evidence_id)}
      title={title}
      className="mx-0.5 inline-flex items-center gap-1 rounded-md border border-sky-500/20 bg-sky-500/[0.07] px-1.5 py-0.5 align-baseline text-[11px] text-sky-300 transition hover:border-sky-500/40 hover:bg-sky-500/[0.12]"
    >
      <span>{label}</span>
    </button>
  );
}

export function Chips({ ids, compact = false }: { ids?: string[]; compact?: boolean }) {
  if (!ids || ids.length === 0) return null;
  // 去重:同一结论引用重复 evidence_id 时,既避免 React key 撞车(警告「重复/遗漏」),
  // 也避免同一来源 chip 视觉上重复渲染。保序。
  const unique = [...new Set(ids)];
  if (compact) {
    return (
      <span className="ml-1 inline-flex align-baseline">
        <SourceGroup ids={unique} />
      </span>
    );
  }
  return (
    <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
      {unique.map((id) => (
        <Chip key={id} id={id} />
      ))}
    </span>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return <span className="rounded bg-white/5 px-2 py-0.5 text-xs text-neutral-400">{children}</span>;
}

function EvidencePanel({ evidence, onClose }: { evidence: Evidence; onClose: () => void }) {
  const domain = domainOf(evidence.source_url);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto border-l border-white/10 bg-neutral-950 p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Favicon url={evidence.source_url} size={18} />
            <span className="text-sm text-neutral-200">{domain}</span>
          </div>
          <button onClick={onClose} className="text-neutral-500 hover:text-white">
            ✕ <span className="text-xs">Esc</span>
          </button>
        </div>
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap gap-2">
            <Tag>{evidence.product}</Tag>
            <Tag>{evidence.claim_type}</Tag>
            <Tag>{biasLabel(evidence.source_bias)}</Tag>
            <Tag>可信度 {evidence.source_reliability}</Tag>
          </div>
          <div>
            <div className="mb-1 text-xs text-neutral-500">结论</div>
            <p className="text-neutral-200">{evidence.claim}</p>
          </div>
          <div>
            <div className="mb-1 text-xs text-neutral-500">原文片段</div>
            <blockquote className="border-l-2 border-sky-500/40 pl-3 italic text-neutral-300">
              {evidence.extracted_snippet}
            </blockquote>
          </div>
          <a
            href={evidence.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-block break-all text-xs text-sky-500 hover:underline"
          >
            {evidence.source_url}
          </a>
          <div className="text-xs text-neutral-500">
            来源类型 {evidence.source_type} · 观测 {evidence.observed_at} · 新鲜度 {evidence.source_freshness}
          </div>
        </div>
      </div>
    </div>
  );
}
