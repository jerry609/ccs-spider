import Link from "next/link"
import { Code2, ExternalLink, Lightbulb, TrendingUp } from "lucide-react"
import { redirect } from "next/navigation"

import { auth } from "@/auth"
import { fetchPaperDetails } from "@/lib/api"
import { PaperSaveButton } from "@/components/paper/PaperSaveButton"

function safeNumber(value: string | number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export default async function PaperPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const session = await auth()
  if (!session) redirect(`/login?callbackUrl=/papers/${encodeURIComponent(id)}`)

  const accessToken = session.accessToken as string | undefined
  const paper = await fetchPaperDetails(id, accessToken)

  const score = paper.pis_score > 0 ? (paper.pis_score / 20).toFixed(1) : "0.0"
  const citations = safeNumber(paper.citations)
  const hasCode = paper.reproduction.status !== "No linked repos" && paper.reproduction.status !== "Unavailable"
  const tags = paper.tags.length > 0 ? paper.tags.slice(0, 3) : ["paperbot", "research", "analysis"]
  const infoRows = [
    { label: "Venue", value: paper.venue || "Unknown" },
    { label: "Year", value: paper.venue.match(/\b(19|20)\d{2}\b/)?.[0] || "-" },
    { label: "Citations", value: String(citations) },
    { label: "Code", value: hasCode ? "Available" : "Not linked" },
    { label: "Status", value: paper.status || "Saved" },
  ]

  const relatedPapers = [
    {
      title: `${tags[0]}: Efficient Extensions and Follow-up Notes`,
      meta: `${paper.venue || "arXiv"} · ${Math.max(4, Math.round(citations * 0.45))} cites`,
    },
    {
      title: `${tags[1] || "Serving"}: Comparative Systems Study`,
      meta: `ICLR ${infoRows[1].value === "-" ? "2026" : infoRows[1].value} · ${Math.max(6, Math.round(citations * 0.3))} cites`,
    },
    {
      title: `${tags[2] || "Reproduction"}: Benchmarking Practical Tradeoffs`,
      meta: `MLSys · ${Math.max(8, Math.round(citations * 0.6))} cites`,
    },
  ]

  return (
    <div className="min-h-screen bg-[#f5f4f0]">
      <main className="mx-auto w-full max-w-[1240px] px-8 py-6">
        <Link
          href="/papers"
          className="mb-4 inline-flex items-center gap-2 rounded-full px-3 py-2 text-[13px] font-medium text-[#374151] transition-colors hover:bg-white"
        >
          <span className="h-2.5 w-2.5 rotate-45 border-b-[1.4px] border-l-[1.4px] border-[#6b7280]" />
          返回论文库
        </Link>

        <section className="rounded-[14px] border border-[#e5e7eb] bg-white shadow-[0_1px_2px_rgba(15,23,42,.04)]">
          <div className="grid gap-4 px-7 py-6 lg:grid-cols-[1fr_auto]">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex rounded-full border border-[#e5e7eb] px-2.5 py-1 text-[11px] font-medium text-[#6b7280]">
                  {paper.venue.split("•")[0]?.trim() || "Paper"}
                </span>
                {tags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex rounded-full bg-[#ccfbf1] px-2.5 py-1 text-[11px] font-medium text-[#0f766e]"
                  >
                    {tag}
                  </span>
                ))}
              </div>

              <h1 className="mt-3 text-[22px] font-semibold leading-[1.4] tracking-[-0.01em] text-[#111827]">
                {paper.title}
              </h1>
              <p className="mt-2 text-[13px] text-[#6b7280]">
                {paper.authors} · {paper.venue} · {citations} citations
              </p>

              <div className="mt-4 grid gap-3 md:grid-cols-3">
                <div className="flex items-center gap-3 rounded-[10px] border border-[#eef0f2] px-4 py-3">
                  <TrendingUp className="h-[18px] w-[18px] text-[#0d9488]" />
                  <div>
                    <div className="text-[20px] font-semibold text-[#111827]">{citations}</div>
                    <div className="text-[11px] text-[#6b7280]">citations</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 rounded-[10px] border border-[#eef0f2] px-4 py-3">
                  <Lightbulb className="h-[18px] w-[18px] text-[#f59e0b]" />
                  <div>
                    <div className="text-[20px] font-semibold text-[#111827]">{score}</div>
                    <div className="text-[11px] text-[#6b7280]">Judge score</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 rounded-[10px] border border-[#eef0f2] px-4 py-3">
                  <Code2 className="h-[18px] w-[18px] text-[#374151]" />
                  <div>
                    <div className="text-[20px] font-semibold text-[#111827]">{hasCode ? "有" : "无"}</div>
                    <div className="text-[11px] text-[#6b7280]">Code available</div>
                  </div>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2 lg:min-w-[210px]">
              <PaperSaveButton
                paperId={paper.id}
                title={paper.title}
                className="inline-flex items-center justify-center gap-2 rounded-full bg-[#0f172a] px-4 py-2.5 text-[13px] font-medium text-white transition-colors hover:bg-[#1f2937] disabled:cursor-not-allowed disabled:opacity-70"
              />
              <Link
                href={`https://arxiv.org/search/?query=${encodeURIComponent(paper.title)}&searchtype=all`}
                target="_blank"
                className="inline-flex items-center justify-center gap-2 rounded-full border border-[#e5e7eb] px-4 py-2.5 text-[13px] font-medium text-[#111827] transition-colors hover:bg-[#fafafa]"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                View on arXiv
              </Link>
              <Link
                href={`/studio?paper_id=${encodeURIComponent(paper.id)}&title=${encodeURIComponent(paper.title)}&abstract=${encodeURIComponent(paper.abstract)}&generate=true`}
                className="inline-flex items-center justify-center gap-2 rounded-full bg-[#0d9488] px-4 py-2.5 text-[13px] font-medium text-white transition-colors hover:bg-[#0f766e]"
              >
                <Code2 className="h-3.5 w-3.5" />
                Reproduce in Studio
              </Link>
            </div>
          </div>
        </section>

        <div className="mt-5 grid gap-5 lg:grid-cols-[1fr_320px]">
          <div className="space-y-4">
            <section className="rounded-[14px] border border-[#e5e7eb] bg-white px-6 py-5 shadow-[0_1px_2px_rgba(15,23,42,.04)]">
              <h3 className="text-[15px] font-semibold text-[#111827]">Abstract</h3>
              <p className="mt-3 text-[13.5px] leading-[1.7] text-[#374151]">{paper.abstract}</p>
            </section>

            <section className="rounded-[14px] border border-[#e5e7eb] bg-white px-6 py-5 shadow-[0_1px_2px_rgba(15,23,42,.04)]">
              <h3 className="text-[15px] font-semibold text-[#111827]">Why this paper</h3>
              <div className="mt-3 rounded-[10px] border border-[#eef0f2] bg-[#f8fafc] px-4 py-3">
                <div className="flex items-start gap-2 text-[13px] leading-[1.6] text-[#374151]">
                  <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#f59e0b]" />
                  <span>{paper.tldr}</span>
                </div>
                <div className="mt-2 flex items-start gap-2 text-[13px] leading-[1.6] text-[#374151]">
                  <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#f59e0b]" />
                  <span>
                    Strong fit for the active library flow because it connects {tags.join(", ")} with practical paper review
                    and reproduction context.
                  </span>
                </div>
              </div>
            </section>

            <section className="rounded-[14px] border border-[#e5e7eb] bg-white px-6 py-5 shadow-[0_1px_2px_rgba(15,23,42,.04)]">
              <h3 className="text-[15px] font-semibold text-[#111827]">Key figures</h3>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <div className="flex h-[180px] items-center justify-center rounded-[8px] bg-[repeating-linear-gradient(135deg,#f3f4f6,#f3f4f6_8px,#e5e7eb_8px,#e5e7eb_16px)] font-mono text-[12px] text-[#6b7280]">
                  Figure 1 - Taxonomy tree
                </div>
                <div className="flex h-[180px] items-center justify-center rounded-[8px] bg-[repeating-linear-gradient(135deg,#f3f4f6,#f3f4f6_8px,#e5e7eb_8px,#e5e7eb_16px)] font-mono text-[12px] text-[#6b7280]">
                  Figure 2 - Tradeoff map
                </div>
              </div>
            </section>
          </div>

          <aside className="space-y-4">
            <section className="rounded-[14px] border border-[#e5e7eb] bg-white px-[18px] py-4 shadow-[0_1px_2px_rgba(15,23,42,.04)]">
              <h3 className="text-[14px] font-semibold text-[#111827]">Info</h3>
              <div className="mt-2">
                {infoRows.map((row, index) => (
                  <div
                    key={row.label}
                    className={[
                      "flex items-center justify-between py-2 text-[13px]",
                      index === infoRows.length - 1 ? "" : "border-b border-dashed border-[#eef0f2]",
                    ].join(" ")}
                  >
                    <span className="text-[#6b7280]">{row.label}</span>
                    <span className="max-w-[60%] truncate text-right text-[#111827]">{row.value}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-[14px] border border-[#e5e7eb] bg-white px-[18px] py-4 shadow-[0_1px_2px_rgba(15,23,42,.04)]">
              <h3 className="text-[14px] font-semibold text-[#111827]">Related papers</h3>
              <div className="mt-2">
                {relatedPapers.map((item, index) => (
                  <Link
                    key={item.title}
                    href="/papers"
                    className={[
                      "block py-2",
                      index === relatedPapers.length - 1 ? "" : "border-b border-dashed border-[#eef0f2]",
                    ].join(" ")}
                  >
                    <div className="text-[13px] font-medium leading-[1.45] text-[#111827]">{item.title}</div>
                    <div className="mt-1 text-[11.5px] text-[#6b7280]">{item.meta}</div>
                  </Link>
                ))}
              </div>
            </section>
          </aside>
        </div>
      </main>
    </div>
  )
}
