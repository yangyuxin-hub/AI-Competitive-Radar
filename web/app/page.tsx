import AnalyzeFlow from "@/components/AnalyzeFlow";
import RecentReports from "@/components/RecentReports";

export default function Home() {
  return (
    <div className="min-h-screen bg-neutral-950 font-sans text-neutral-100">
      <main className="mx-auto max-w-4xl px-6 py-12">
        <header className="mb-10">
          <h1 className="text-2xl font-semibold">竞品情报工作台</h1>
          <p className="mt-1 text-sm text-neutral-500">
            一句话发起 · 多 Agent 协作 · 每条结论可溯源
          </p>
        </header>

        <section className="mb-12 rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <AnalyzeFlow />
        </section>

        <RecentReports />
      </main>
    </div>
  );
}
