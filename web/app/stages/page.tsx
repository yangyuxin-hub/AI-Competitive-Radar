import Link from "next/link";
import StageQuality from "@/components/StageQuality";
import Timeline from "@/components/Timeline";

export default function StagesPage() {
  return (
    <div className="min-h-screen bg-neutral-950 font-sans text-neutral-100">
      <main className="mx-auto max-w-4xl px-6 py-12">
        <header className="mb-10">
          <h1 className="text-2xl font-semibold">运行细节</h1>
          <p className="mt-1 text-sm text-neutral-500">
            最近一次运行的逐节点时间线（含重试与精准打回），以及跨运行的瓶颈聚合。交付清单见工作台侧栏。
          </p>
          <Link href="/" className="mt-3 inline-block text-sm text-sky-400 hover:text-sky-300">
            ← 返回工作台
          </Link>
        </header>

        <section className="mb-8 rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <Timeline />
        </section>

        <details className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <summary className="cursor-pointer text-sm text-neutral-400">跨运行瓶颈聚合</summary>
          <div className="mt-4">
            <StageQuality />
          </div>
        </details>
      </main>
    </div>
  );
}
