import Link from "next/link";
import Workbench from "@/components/Workbench";

export default function Home() {
  return (
    <div className="min-h-screen bg-neutral-950 font-sans text-neutral-100">
      <main className="mx-auto max-w-6xl px-6 py-12">
        <header className="mb-10 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">竞品情报工作台</h1>
            <p className="mt-1 text-sm text-neutral-500">
              一句话发起 · 多 Agent 协作 · 每条结论可溯源
            </p>
          </div>
          <Link
            href="/stages"
            className="shrink-0 rounded-lg border border-white/10 px-3 py-1.5 text-sm text-neutral-300 transition hover:border-sky-500/40 hover:text-sky-300"
          >
            运行细节 →
          </Link>
        </header>

        <Workbench />
      </main>
    </div>
  );
}
