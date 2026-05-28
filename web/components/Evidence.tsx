"use client";

import { createContext, useContext, useEffect, useState } from "react";
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

  return (
    <span className="relative inline-block align-baseline">
      <button
        onClick={() => open(id)}
        onMouseEnter={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setPos({
            left: Math.min(r.left, window.innerWidth - 340),
            top: r.bottom + 6,
          });
        }}
        onMouseLeave={() => setPos(null)}
        className="mx-0.5 inline-flex max-w-[150px] items-center gap-1 truncate rounded-md border border-white/10 bg-white/[0.04] px-1.5 py-0.5 align-baseline text-[11px] text-neutral-300 transition hover:border-sky-500/40 hover:text-sky-300"
      >
        <Favicon url={ev.source_url} />
        <span className="truncate">{domain}</span>
      </button>

      {pos && (
        <div
          className="fixed z-[60] w-80 rounded-xl border border-white/10 bg-neutral-900 p-3 text-left shadow-2xl"
          style={{ left: pos.left, top: pos.top }}
        >
          <div className="mb-1.5 flex items-center gap-1.5">
            <Favicon url={ev.source_url} size={16} />
            <span className="text-xs text-neutral-300">{domain}</span>
            <span className="ml-auto rounded bg-white/5 px-1.5 py-0.5 text-[10px] text-neutral-400">
              {biasLabel(ev.source_bias)} · 可信度 {ev.source_reliability}
            </span>
          </div>
          <p className="line-clamp-3 text-xs leading-relaxed text-neutral-400">
            {ev.extracted_snippet || ev.claim}
          </p>
          <div className="mt-1.5 text-[10px] text-sky-500">点击查看完整原文 →</div>
        </div>
      )}
    </span>
  );
}

export function Chips({ ids }: { ids?: string[] }) {
  if (!ids || ids.length === 0) return null;
  return (
    <span className="ml-1 inline-flex flex-wrap gap-0.5 align-baseline">
      {ids.map((id) => (
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
