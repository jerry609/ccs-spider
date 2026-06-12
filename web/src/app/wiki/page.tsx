import Link from "next/link"
import { Search, BookOpen, Layers, Target, BarChart2, Waves, Image as ImageIcon } from "lucide-react"

import { fetchWikiConcepts } from "@/lib/api"
import type { LucideIcon } from "lucide-react"

const iconMap: Record<string, LucideIcon> = {
  layers: Layers,
  target: Target,
  "bar-chart": BarChart2,
  waves: Waves,
  image: ImageIcon,
  "book-open": BookOpen,
}

type WikiSearchParams = Promise<{ q?: string | string[]; cat?: string | string[] }>

function firstValue(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] || "" : value || ""
}

export default async function WikiPage({ searchParams }: { searchParams: WikiSearchParams }) {
  const raw = await searchParams
  const keyword = firstValue(raw.q).trim()
  const activeCategory = firstValue(raw.cat).trim() || "All"
  const concepts = await fetchWikiConcepts(keyword.toLowerCase())
  const categories = ["All", ...Array.from(new Set(concepts.map((concept) => concept.category)))]
  const filteredConcepts = concepts.filter((concept) => activeCategory === "All" || concept.category === activeCategory)

  return (
    <div className="min-h-screen px-7 py-8">
      <div className="mx-auto max-w-[1280px]">
        <div className="mb-4 flex items-end justify-between gap-4">
          <div>
            <h1 className="flex items-center gap-2.5 text-[28px] font-semibold tracking-[-0.02em] text-slate-950">
              <BookOpen className="h-[26px] w-[26px] text-slate-600" />
              Knowledge Base
            </h1>
            <div className="mt-1 text-sm text-slate-500">
              Explore <b>{filteredConcepts.length}</b> core concepts in AI/ML research.
            </div>
          </div>
          <form method="get" className="flex items-center gap-2">
            <div className="flex items-center gap-2 rounded-[10px] border border-slate-200 bg-white px-3 py-2 shadow-sm">
              <Search className="h-3.5 w-3.5 text-slate-400" />
              <input
                name="q"
                defaultValue={keyword}
                placeholder="Search concepts, methods, metrics..."
                className="w-[240px] border-0 bg-transparent text-[13px] outline-none placeholder:text-slate-400"
              />
              <input type="hidden" name="cat" value={activeCategory} />
            </div>
            <button
              type="submit"
              className="rounded-full bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
            >
              Search
            </button>
          </form>
        </div>

        <div className="mb-4 flex flex-wrap gap-1.5">
          {categories.map((category) => {
            const params = new URLSearchParams()
            if (keyword) params.set("q", keyword)
            if (category !== "All") params.set("cat", category)
            const href = params.toString() ? `/wiki?${params.toString()}` : "/wiki"

            return (
              <Link
                key={category}
                href={href}
                className={[
                  "inline-flex rounded-full px-3 py-1 text-[12px] font-medium",
                  activeCategory === category
                    ? "bg-slate-900 text-white"
                    : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                ].join(" ")}
              >
                {category}
              </Link>
            )
          })}
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filteredConcepts.map((concept) => {
            const Icon = iconMap[concept.icon] || Layers
            return (
              <article
                key={concept.id}
                className="rounded-[14px] border border-slate-200 bg-white px-5 py-[18px] transition-shadow duration-150 hover:border-teal-600 hover:shadow-[0_10px_24px_rgba(15,23,42,0.08)]"
              >
                <div className="flex items-start gap-3">
                  <span className="inline-flex h-[38px] w-[38px] items-center justify-center rounded-[10px] bg-[#f3f4f6] text-slate-700">
                    <Icon className="h-5 w-5" />
                  </span>
                  <div>
                    <h3 className="text-[16px] font-semibold text-slate-950">{concept.name}</h3>
                    <div className="mt-0.5 text-[11px] uppercase tracking-[0.08em] text-slate-400">
                      {concept.category}
                    </div>
                  </div>
                </div>

                <p className="mt-3 text-[13px] leading-[1.55] text-slate-700">{concept.description}</p>

                <div className="mt-3 text-[11px] uppercase tracking-[0.08em] text-slate-400">Definition</div>
                <p className="mt-1 text-[13px] leading-[1.55] text-slate-700">
                  {concept.definition.slice(0, 120)}
                  {concept.definition.length > 120 ? "..." : ""}
                </p>

                <div className="mt-3 text-[11px] uppercase tracking-[0.08em] text-slate-400">Related Concepts</div>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {concept.related_concepts.slice(0, 4).map((item) => (
                    <span key={item} className="rounded-[6px] bg-[#f1f5f9] px-2 py-0.5 text-[12px] text-slate-700">
                      {item}
                    </span>
                  ))}
                </div>

                <div className="mt-4 border-t border-dashed border-slate-200 pt-3 text-right">
                  <span className="text-sm text-teal-700">Explore Concept</span>
                </div>
              </article>
            )
          })}
        </div>
      </div>
    </div>
  )
}
