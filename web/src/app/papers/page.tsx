import Link from "next/link"
import { redirect } from "next/navigation"
import { Filter } from "lucide-react"

import { auth } from "@/auth"
import { backendBaseUrl } from "@/app/api/_utils/auth-headers"
import { fetchDashboardTracks } from "@/lib/dashboard-api"
import { PapersLibraryTools } from "@/components/paper/PapersLibraryTools"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

type SearchParams = Promise<{
  view?: string | string[]
  sort_by?: string | string[]
  track_id?: string | string[]
  page?: string | string[]
}>

type SavedPaperItem = {
  paper: {
    id: number
    title: string
    authors?: string[]
    primary_source?: string
    venue?: string
    publication_date?: string | null
  }
  saved_at?: string | null
  latest_judge?: { overall?: number | null; recommendation?: string | null; one_line_summary?: string | null } | null
  reading_status?: { status?: string | null } | null
  provenance?: { is_workflow?: boolean; is_manual?: boolean } | null
  track_id?: number | null
}

function first(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] || "" : value || ""
}

function fmtDate(value?: string | null): string {
  if (!value) return "-"
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleString()
}

function fmtShortDate(value?: string | null): string {
  if (!value) return "-"
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
}

function viewMatches(item: SavedPaperItem, view: string): boolean {
  if (view === "workflow") return Boolean(item.provenance?.is_workflow || item.latest_judge)
  if (view === "manual") return Boolean(item.provenance?.is_manual)
  return true
}

async function fetchSavedPapers(accessToken?: string, sortBy = "saved_at", trackId?: number | null) {
  const qs = new URLSearchParams({ limit: "500", sort_by: sortBy })
  if (trackId) qs.set("track_id", String(trackId))
  const res = await fetch(`${backendBaseUrl()}/api/research/papers/saved?${qs.toString()}`, {
    cache: "no-store",
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : undefined,
  })
  if (!res.ok) return []
  const payload = (await res.json().catch(() => null)) as { items?: SavedPaperItem[]; papers?: SavedPaperItem[] } | null
  return payload?.items || payload?.papers || []
}

export default async function PapersPage({ searchParams }: { searchParams: SearchParams }) {
  const session = await auth()
  if (!session) redirect("/login?callbackUrl=/papers")

  const params = await searchParams
  const view = ["all", "workflow", "manual"].includes(first(params.view)) ? first(params.view) : "all"
  const sortBy = ["saved_at", "judge_score", "published_at"].includes(first(params.sort_by))
    ? first(params.sort_by)
    : "saved_at"
  const trackId = Number(first(params.track_id)) || null
  const pageNo = Math.max(1, Number(first(params.page)) || 1)

  const [tracks, rawItems] = await Promise.all([
    fetchDashboardTracks(session.accessToken),
    fetchSavedPapers(session.accessToken, sortBy, trackId),
  ])

  const items = rawItems.filter((item) => viewMatches(item, view))
  const total = items.length
  const pageSize = 20
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const safePage = Math.min(pageNo, totalPages)
  const start = (safePage - 1) * pageSize
  const paged = items.slice(start, start + pageSize)

  const counts = {
    all: rawItems.length,
    workflow: rawItems.filter((item) => viewMatches(item, "workflow")).length,
    manual: rawItems.filter((item) => viewMatches(item, "manual")).length,
  }

  const selectedTrack = tracks.find((track) => track.id === trackId) || null
  const trackMenuItems = [{ id: null as number | null, name: "All Tracks" }, ...tracks]
  const dropdownPanelClass =
    "z-[80] min-w-[220px] overflow-hidden rounded-[12px] border border-[#e5e7eb] bg-white p-1 shadow-[0_18px_36px_-12px_rgba(15,23,42,.22)]"
  const sortPanelClass =
    "z-[80] min-w-[180px] overflow-hidden rounded-[12px] border border-[#e5e7eb] bg-white p-1 shadow-[0_18px_36px_-12px_rgba(15,23,42,.22)]"

  function queryHref(next: Record<string, string | null>) {
    const qp = new URLSearchParams()
    const nextView = next.view ?? view
    const nextSort = next.sort_by ?? sortBy
    const nextTrack = next.track_id ?? (trackId ? String(trackId) : null)
    const nextPage = next.page ?? String(safePage)
    if (nextView !== "all") qp.set("view", nextView)
    if (nextSort !== "saved_at") qp.set("sort_by", nextSort)
    if (nextTrack) qp.set("track_id", nextTrack)
    if (nextPage !== "1") qp.set("page", nextPage)
    const qs = qp.toString()
    return qs ? `/papers?${qs}` : "/papers"
  }

  return (
    <div className="min-h-screen bg-transparent">
      <main className="mx-auto w-full max-w-[1200px] px-8 py-8">
        <header className="mb-5 flex items-end justify-between gap-4">
          <div>
            <h2 className="text-[28px] font-bold tracking-[-0.025em] text-[#0f172a]">Papers Library</h2>
            <p className="mt-1 text-[13.5px] leading-6 text-[#64748b]">
              Manual saves, workflow-reviewed papers, and import provenance in one library.
            </p>
          </div>
          <PapersLibraryTools
            trackId={trackId}
            trackName={selectedTrack?.name || null}
            visiblePapers={paged.map((item) => ({
              id: item.paper.id,
              title: item.paper.title,
              authors: item.paper.authors,
            }))}
            relatedPaperIds={items.map((item) => String(item.paper.id))}
          />
        </header>

        <section className="relative overflow-hidden rounded-[14px] border border-[rgba(229,231,235,.9)] bg-[rgba(252,249,244,.84)] shadow-[0_18px_40px_-24px_rgba(15,23,42,.18)] backdrop-blur-sm">
          <div className="absolute inset-0 bg-[linear-gradient(135deg,rgba(255,255,255,.44),rgba(255,255,255,.08))]" />
          <div className="relative z-10 border-b border-[rgba(229,231,235,.88)] bg-[rgba(252,250,246,.82)] px-5 py-4">
            <h3 className="text-[16px] font-semibold text-[#0f172a]">Saved Papers</h3>
            <p className="mt-1 text-[13px] leading-6 text-[#64748b]">
              One library for manual saves, workflow-reviewed papers, and import provenance.
            </p>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <div className="inline-flex rounded-[8px] border border-[rgba(226,232,240,.95)] bg-[rgba(247,244,237,.88)] p-1 shadow-[inset_0_1px_0_rgba(255,255,255,.7)]">
                {(["all", "workflow", "manual"] as const).map((key) => (
                  <Link
                    key={key}
                    href={queryHref({ view: key, page: "1" })}
                    className={[
                      "inline-flex h-[28px] items-center rounded-[6px] px-3 text-[13px] font-medium",
                      view === key
                        ? "bg-white text-[#0f172a] shadow-[0_1px_2px_rgba(15,23,42,.08)]"
                        : "text-[#64748b] hover:text-[#0f172a]",
                    ].join(" ")}
                  >
                    {key === "all" ? "All" : key === "workflow" ? "Workflow" : "Manual"}
                    <span className="ml-1 text-[#94a3b8]">{counts[key]}</span>
                  </Link>
                ))}
              </div>

              <div className="flex items-center gap-2">
                <div className="relative z-20">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button className="inline-flex h-[32px] items-center gap-2 rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[13px] font-medium text-[#0f172a] shadow-sm outline-none">
                        <Filter className="h-3.5 w-3.5 text-[#64748b]" />
                        {selectedTrack?.name || "All Tracks"}
                        <span className="h-3 w-3 -translate-y-0.5 rotate-45 border-b-[1.4px] border-r-[1.4px] border-[#94a3b8]" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" sideOffset={6} className={dropdownPanelClass}>
                      {trackMenuItems.map((track, index) => (
                        <div key={String(track.id) || "all"}>
                          {index === 1 ? <DropdownMenuSeparator /> : null}
                          <DropdownMenuItem asChild className="h-[38px] rounded-[8px] px-3 text-[13px] text-[#0f172a]">
                            <Link href={queryHref({ track_id: track.id ? String(track.id) : null, page: "1" })}>
                              {track.name}
                            </Link>
                          </DropdownMenuItem>
                        </div>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>

                <div className="relative z-20">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button className="inline-flex h-[32px] items-center gap-1.5 border-0 bg-transparent px-2 text-[13px] text-[#64748b] outline-none hover:bg-[#f1f5f9] hover:text-[#0f172a]">
                        Sort by{" "}
                        <span className="font-medium text-[#0f172a]">
                          {sortBy === "saved_at" ? "Saved time" : sortBy === "judge_score" ? "Judge score" : "Published time"}
                        </span>
                        <span className="h-3 w-3 -translate-y-0.5 rotate-45 border-b-[1.4px] border-r-[1.4px] border-[#94a3b8]" />
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" sideOffset={6} className={sortPanelClass}>
                      {[
                        ["saved_at", "Saved time"],
                        ["judge_score", "Judge score"],
                        ["published_at", "Published time"],
                      ].map(([value, label]) => (
                        <DropdownMenuItem asChild key={value} className="h-[38px] rounded-[8px] px-3 text-[13px] text-[#0f172a]">
                          <Link href={queryHref({ sort_by: value, page: "1" })}>{label}</Link>
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </div>
            </div>
          </div>

          <div className="relative z-10 bg-[rgba(255,255,255,.74)] px-5 pt-3">
            <div className="mb-3 flex items-center justify-between text-[12px] text-[#64748b]">
              <div className="flex items-center gap-2">
                <span className="inline-block h-4 w-4 rounded border border-[#94a3b8]" />
                <span>{total} papers</span>
              </div>
            </div>

            <div className="space-y-0">
              {paged.length === 0 ? (
                <div className="py-12 text-center text-[13px] text-[#64748b]">No saved papers yet.</div>
              ) : (
                paged.map((item) => {
                  const p = item.paper
                  const authors = (p.authors || []).slice(0, 4).join(", ") || "Unknown authors"
                  const status = item.reading_status?.status || "unread"
                  const judge = item.latest_judge?.overall
                  const judgeClass = typeof judge === "number" && judge >= 4 ? "high" : typeof judge === "number" && judge >= 3 ? "mid" : "low"

                  return (
                    <div
                      key={p.id}
                      className="group flex gap-3 border-t border-[#e5e7eb] px-1 py-[14px] transition-colors hover:bg-[rgba(241,245,249,.4)]"
                    >
                      <span className="mt-1 inline-flex h-4 w-4 shrink-0 rounded border border-[#94a3b8] bg-white" />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="line-clamp-2 text-[14.5px] font-semibold leading-[1.4] text-[#0f172a]">
                              {p.title}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                            <Link
                              href={`/papers/${p.id}`}
                              className="rounded-[6px] border border-[#e2e8f0] bg-white px-2 py-1 text-[11px] font-medium"
                            >
                              Detail
                            </Link>
                            <button className="rounded-[6px] border border-transparent px-2 py-1 text-[11px] font-medium text-[#0f172a]">
                              Unsave
                            </button>
                          </div>
                        </div>

                        <div className="mt-1 text-[12.5px] text-[#64748b]">{authors}</div>
                        {p.venue ? <div className="text-[12px] text-[#64748b]">{p.venue}</div> : null}
                        {item.latest_judge?.one_line_summary ? (
                          <p className="mt-2 line-clamp-2 text-[13px] leading-[1.55] text-[#0f172a]/85">
                            {item.latest_judge.one_line_summary}
                          </p>
                        ) : null}

                        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-[#64748b]">
                          <span className="inline-flex rounded-[5px] border border-[#e5e7eb] bg-white px-2 py-0.5">
                            {p.primary_source || "unknown"}
                          </span>
                          <span>Saved · {fmtDate(item.saved_at)}</span>
                          {p.publication_date ? <span>Published · {fmtShortDate(p.publication_date)}</span> : null}
                          {typeof judge === "number" ? (
                            <span
                              className={[
                                "inline-flex items-center gap-1 rounded-[5px] border border-[#e5e7eb] bg-white px-2 py-0.5",
                                judgeClass === "high" ? "text-[#0f172a]" : judgeClass === "mid" ? "text-[#0f172a]" : "text-[#64748b]",
                              ].join(" ")}
                            >
                              <span
                                className={[
                                  "h-1.5 w-1.5 rounded-full",
                                  judgeClass === "high" ? "bg-[#16a34a]" : judgeClass === "mid" ? "bg-[#f59e0b]" : "bg-[#94a3b8]",
                                ].join(" ")}
                              />
                              Judge {judge.toFixed(1)}
                            </span>
                          ) : null}
                          <button className="ml-auto inline-flex h-6 items-center gap-1 rounded-[5px] border border-[#e5e7eb] bg-white px-2 text-[11.5px] font-medium text-[#0f172a]">
                            {status === "reading" ? "Reading" : status === "read" ? "Read" : status === "archived" ? "Archived" : "To read"}
                            <span className="h-2.5 w-2.5 -translate-y-0.5 rotate-45 border-b-[1.2px] border-r-[1.2px] border-[#94a3b8]" />
                          </button>
                        </div>
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-[rgba(229,231,235,.88)] pt-4 text-[13px] text-[#64748b]">
              <span>
                Showing {total === 0 ? 0 : start + 1} - {Math.min(start + pageSize, total)} of {total}
              </span>
              <div className="flex items-center gap-2">
                <Link
                  href={queryHref({ page: String(Math.max(1, safePage - 1)) })}
                  className="rounded-[6px] border border-[#e5e7eb] bg-white px-3 py-1.5 text-[12.5px] font-medium text-[#0f172a]"
                >
                  Prev
                </Link>
                <span>
                  Page {safePage} / {totalPages}
                </span>
                <Link
                  href={queryHref({ page: String(Math.min(totalPages, safePage + 1)) })}
                  className="rounded-[6px] border border-[#e5e7eb] bg-white px-3 py-1.5 text-[12.5px] font-medium text-[#0f172a]"
                >
                  Next
                </Link>
              </div>
            </div>
          </div>
        </section>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-2 rounded-[8px] bg-[rgba(245,245,245,.5)] px-5 py-4 text-[13px] text-[#64748b]">
          <span>Manual saves, workflow-reviewed papers, and import provenance in one library.</span>
          <Link href="/settings?tab=models" className="text-[#006ddd] hover:underline">
            Manage imports
          </Link>
        </div>
      </main>
    </div>
  )
}
