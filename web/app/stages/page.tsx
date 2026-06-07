import Link from "next/link";
import StageQuality from "@/components/StageQuality";

export default function StagesPage() {
  return (
    <div className="min-h-screen bg-neutral-950 font-sans text-neutral-100">
      <main className="mx-auto max-w-4xl px-6 py-12">
        <header className="mb-10">
          <h1 className="text-2xl font-semibold">各环节质量看板</h1>
          <p className="mt-1 text-sm text-neutral-500">
            intake / collect / analyze / write / review 逐段耗时与质量，定位瓶颈
          </p>
          <Link href="/" className="mt-3 inline-block text-sm text-sky-400 hover:text-sky-300">
            ← 返回工作台
          </Link>
        </header>

        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <StageQuality />
        </section>
      </main>
    </div>
  );
}
