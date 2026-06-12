import Link from "next/link"
import { Search, Sparkles } from "lucide-react"
import { redirect } from "next/navigation"

import { auth } from "@/auth"
import { backendBaseUrl } from "@/app/api/_utils/auth-headers"
import { ResearchTrackMenu, ResearchYearMenu } from "@/components/research/ResearchToolbarMenus"
import { fetchDashboardTracks } from "@/lib/dashboard-api"
import type { ResearchTrackSummary } from "@/lib/types"

type SearchParams = Promise<{
  query?: string | string[]
  track_id?: string | string[]
  personalized?: string | string[]
  year_from?: string | string[]
  year_to?: string | string[]
  cap?: string | string[]
  sources?: string | string[]
}>

type RecommendationPaper = {
  paper_id: string
  title: string
  abstract?: string
  year?: number
  venue?: string
  citation_count?: number
  authors?: string[]
  url?: string
  latest_judge?: {
    overall?: number
    recommendation?: string
    one_line_summary?: string
  }
}

type ContextPack = {
  context_run_id?: number | null
  paper_recommendations?: RecommendationPaper[]
  paper_recommendation_reasons?: Record<string, string[]>
}

const DEFAULT_SOURCES = ["semantic_scholar", "arxiv", "openalex", "papers_cool", "hf_daily"]

function first(value: string | string[] | undefined): string {
  return Array.isArray(value) ? value[0] || "" : value || ""
}

function getGreeting(date = new Date()): string {
  const hour = date.getHours()
  if (hour < 12) return "Good morning"
  if (hour < 18) return "Good afternoon"
  return "Good evening"
}

function parsePositiveInt(value: string, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function parseSources(raw: string): string[] {
  const items = raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
  return items.length > 0 ? items : DEFAULT_SOURCES
}

function buildResearchHref(args: {
  query?: string
  trackId?: number | null
  personalized?: boolean
  yearFrom?: string
  yearTo?: string
  cap?: number
  sources?: string[]
}) {
  const qp = new URLSearchParams()
  if (args.query?.trim()) qp.set("query", args.query.trim())
  if (args.trackId) qp.set("track_id", String(args.trackId))
  if (args.personalized === false) qp.set("personalized", "0")
  if (args.yearFrom?.trim()) qp.set("year_from", args.yearFrom.trim())
  if (args.yearTo?.trim()) qp.set("year_to", args.yearTo.trim())
  if (args.cap && args.cap !== 25) qp.set("cap", String(args.cap))
  if (args.sources && args.sources.join(",") !== DEFAULT_SOURCES.join(",")) {
    qp.set("sources", args.sources.join(","))
  }
  const qs = qp.toString()
  return qs ? `/research?${qs}` : "/research"
}

function toggleSource(sources: string[], source: string): string[] {
  const exists = sources.includes(source)
  if (exists) {
    const next = sources.filter((item) => item !== source)
    return next.length > 0 ? next : sources
  }
  return [...sources, source]
}

async function fetchSearchContext(args: {
  accessToken?: string
  query: string
  trackId?: number | null
  personalized: boolean
  yearFrom?: string
  yearTo?: string
  cap: number
  sources: string[]
}): Promise<ContextPack | null> {
  const body = {
    query: args.query,
    track_id: args.trackId ?? undefined,
    paper_limit: args.cap,
    memory_limit: 8,
    sources: args.sources,
    offline: false,
    include_cross_track: false,
    stage: "auto",
    personalized: args.personalized,
    year_from: args.yearFrom ? parsePositiveInt(args.yearFrom, 0) || undefined : undefined,
    year_to: args.yearTo ? parsePositiveInt(args.yearTo, 0) || undefined : undefined,
  }

  try {
    const res = await fetch(`${backendBaseUrl()}/api/research/context`, {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(args.accessToken ? { Authorization: `Bearer ${args.accessToken}` } : {}),
      },
      body: JSON.stringify(body),
    })
    if (!res.ok) return null
    const payload = (await res.json().catch(() => null)) as { context_pack?: ContextPack } | null
    return payload?.context_pack || null
  } catch {
    return null
  }
}

export default async function ResearchPage({ searchParams }: { searchParams: SearchParams }) {
  const session = await auth()
  if (!session) redirect("/login?callbackUrl=/research")

  const params = await searchParams
  const query = first(params.query).trim()
  const personalized = first(params.personalized) !== "0"
  const yearFrom = first(params.year_from).trim()
  const yearTo = first(params.year_to).trim()
  const cap = parsePositiveInt(first(params.cap), 25)
  const trackId = Number(first(params.track_id)) || null
  const selectedSources = parseSources(first(params.sources))

  const tracks = await fetchDashboardTracks(session.accessToken)
  const fallbackTrack: ResearchTrackSummary = {
    id: 1,
    name: "RAG for code generation",
    keywords: ["retrieval", "code", "benchmark"],
  }
  const activeTrack = tracks.find((track) => track.id === trackId) || tracks[0] || fallbackTrack
  const contextPack = query
    ? await fetchSearchContext({
        accessToken: session.accessToken,
        query,
        trackId: activeTrack.id,
        personalized,
        yearFrom,
        yearTo,
        cap,
        sources: selectedSources,
      })
    : null

  const papers = contextPack?.paper_recommendations || []
  const reasons = contextPack?.paper_recommendation_reasons || {}
  const hasSearch = query.length > 0

  return (
    <div className="min-h-screen bg-transparent">
      <main className="mx-auto w-full max-w-[1400px] px-6 py-6">
        <div className={hasSearch ? "" : "flex min-h-[calc(100vh-110px)] items-center justify-center"}>
          <div className={hasSearch ? "mx-auto max-w-[1200px]" : "w-full max-w-[896px]"}>
            {!hasSearch ? (
              <>
                <div className="mb-10 text-center">
                  <h1 className="inline-flex items-center gap-3 text-[clamp(28px,4vw,44px)] font-semibold leading-[1.1] tracking-[-0.025em] text-[#111827]">
                    <Sparkles className="h-[34px] w-[34px]" />
                    {getGreeting()}
                  </h1>
                  <p className="mt-3 text-[18px] text-[#6b7280]">What papers are you looking for?</p>
                </div>

                <form method="get" className="relative rounded-[16px] border border-[#e5e7eb] bg-white shadow-[0_4px_6px_-1px_rgba(0,0,0,.07),0_2px_4px_-2px_rgba(0,0,0,.05)]">
                  <textarea
                    name="query"
                    rows={1}
                    defaultValue={query}
                    placeholder="Search for papers on RAG, transformers, security..."
                    className="min-h-[56px] w-full resize-none border-0 bg-transparent px-6 pb-14 pt-4 text-[16px] leading-[1.5] text-[#111827] outline-none placeholder:text-[#9ca3af]"
                  />
                  <input type="hidden" name="track_id" value={String(activeTrack.id)} />
                  {yearFrom ? <input type="hidden" name="year_from" value={yearFrom} /> : null}
                  {yearTo ? <input type="hidden" name="year_to" value={yearTo} /> : null}
                  {personalized ? null : <input type="hidden" name="personalized" value="0" />}
                  <input type="hidden" name="sources" value={selectedSources.join(",")} />
                  {cap !== 25 ? <input type="hidden" name="cap" value={String(cap)} /> : null}

                  <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 px-5 py-3">
                    <div className="flex items-center gap-2">
                      <Link
                        href={buildResearchHref({
                          query,
                          trackId: activeTrack.id,
                          personalized: !personalized,
                          yearFrom,
                          yearTo,
                          cap,
                          sources: selectedSources,
                        })}
                        className="inline-flex h-8 items-center rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[12px] font-medium text-[#111827] hover:bg-[#f8fafc]"
                      >
                        {personalized ? "Personalized" : "Global"}
                      </Link>

                      <ResearchYearMenu
                        query={query}
                        trackId={activeTrack.id}
                        personalized={personalized}
                        yearFrom={yearFrom}
                        yearTo={yearTo}
                        cap={cap}
                        sources={selectedSources}
                      />
                    </div>

                    <div className="flex items-center gap-2">
                      <ResearchTrackMenu
                        query={query}
                        tracks={(tracks.length > 0 ? tracks : [fallbackTrack]).map((track) => ({
                          id: track.id,
                          name: track.name,
                        }))}
                        activeTrackName={activeTrack.name}
                        personalized={personalized}
                        yearFrom={yearFrom}
                        yearTo={yearTo}
                        cap={cap}
                        sources={selectedSources}
                      />

                      <button
                        type="submit"
                        className="inline-flex h-8 w-8 items-center justify-center rounded-[8px] bg-[#111827] text-white transition-colors hover:bg-[#1f2937]"
                        aria-label="Search"
                      >
                        <Search className="h-4 w-4" />
                      </button>
                    </div>
                  </div>
                </form>

                <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                  {(tracks.length > 0 ? tracks : [fallbackTrack]).slice(0, 5).map((track) => {
                    const active = track.id === activeTrack.id
                    return (
                      <Link
                        key={track.id}
                        href={buildResearchHref({
                          query,
                          trackId: track.id,
                          personalized,
                          yearFrom,
                          yearTo,
                          cap,
                          sources: selectedSources,
                        })}
                        className={[
                          "inline-flex h-10 items-center gap-2 rounded-full border px-5 text-[15px] font-medium transition-colors",
                          active
                            ? "border-[#111827] bg-[#111827] text-white shadow-[0_1px_2px_rgba(0,0,0,.05)]"
                            : "border-[#e5e7eb] bg-white text-[#111827] hover:bg-[#f5f5f5]",
                        ].join(" ")}
                      >
                        {track.name}
                      </Link>
                    )
                  })}
                  <Link
                    href="/tracks"
                    className="inline-flex h-10 items-center rounded-full border border-dashed border-[#e5e7eb] bg-white px-5 text-[15px] font-medium text-[#111827] hover:bg-[#f5f5f5]"
                  >
                    + New Track
                  </Link>
                </div>
              </>
            ) : (
              <div className="pb-4">
                <form method="get" className="rounded-[16px] border border-[#e5e7eb] bg-white px-5 py-4 shadow-[0_4px_6px_-1px_rgba(0,0,0,.07),0_2px_4px_-2px_rgba(0,0,0,.05)]">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                    <div className="flex-1">
                      <label className="mb-2 block text-[12px] font-medium uppercase tracking-[0.08em] text-[#6b7280]">
                        Research Query
                      </label>
                      <textarea
                        name="query"
                        rows={2}
                        defaultValue={query}
                        className="min-h-[56px] w-full resize-none border-0 bg-transparent p-0 text-[16px] leading-[1.5] text-[#111827] outline-none placeholder:text-[#9ca3af]"
                      />
                      <input type="hidden" name="track_id" value={String(activeTrack.id)} />
                      {yearFrom ? <input type="hidden" name="year_from" value={yearFrom} /> : null}
                      {yearTo ? <input type="hidden" name="year_to" value={yearTo} /> : null}
                      {personalized ? null : <input type="hidden" name="personalized" value="0" />}
                      <input type="hidden" name="sources" value={selectedSources.join(",")} />
                      {cap !== 25 ? <input type="hidden" name="cap" value={String(cap)} /> : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="submit"
                        className="inline-flex h-8 items-center rounded-[8px] bg-[#111827] px-3 text-[13px] font-medium text-white hover:bg-[#1f2937]"
                      >
                        Search
                      </button>
                    </div>
                  </div>
                </form>

                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-[rgba(229,231,235,.7)] bg-white/80 px-4 py-3 backdrop-blur">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="inline-flex h-[22px] items-center rounded-[6px] border border-[#e5e7eb] bg-white px-2.5 text-[11.5px] font-medium text-[#111827]">
                      Track: <span className="ml-1">{activeTrack.name}</span>
                    </span>
                    <span className="inline-flex h-[22px] items-center rounded-[6px] border border-[#e5e7eb] bg-white px-2.5 text-[11.5px] font-medium text-[#111827]">
                      Pending memory: 8
                    </span>
                    <span className="inline-flex h-[22px] items-center rounded-[6px] border border-[#e5e7eb] bg-white px-2.5 text-[11.5px] font-medium text-[#111827]">
                      Mode: <span className="ml-1">{personalized ? "Personalized" : "Global"}</span>
                    </span>
                    <span className="inline-flex h-[22px] items-center rounded-[6px] border border-[#e5e7eb] bg-white px-2.5 text-[11.5px] font-medium text-[#111827]">
                      Sources: <span className="ml-1">{selectedSources.length}</span>
                    </span>
                    <span className="inline-flex h-[22px] items-center rounded-[6px] bg-[#f5f5f5] px-2.5 text-[11.5px] font-medium text-[#111827]">
                      Results: <span className="ml-1">{papers.length}</span>
                    </span>
                    <div className="ml-1 flex items-center gap-1 border-l border-[#e5e7eb] pl-2">
                      <span className="text-[11.5px] text-[#6b7280]">Cap</span>
                      {[10, 25, 50].map((value) => (
                        <Link
                          key={value}
                          href={buildResearchHref({
                            query,
                            trackId: activeTrack.id,
                            personalized,
                            yearFrom,
                            yearTo,
                            cap: value,
                            sources: selectedSources,
                          })}
                          className={[
                            "inline-flex h-7 items-center rounded-[6px] border px-2 text-[11.5px]",
                            cap === value
                              ? "border-[#111827] bg-[#111827] text-white"
                              : "border-[#e5e7eb] bg-white text-[#111827]",
                          ].join(" ")}
                        >
                          {value}
                        </Link>
                      ))}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href="/papers"
                      className="inline-flex h-8 items-center gap-2 rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[13px] text-[#111827] hover:bg-[#f5f5f5]"
                    >
                      Open Papers Library
                    </Link>
                    <Link
                      href="/dashboard"
                      className="inline-flex h-8 items-center gap-2 rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[13px] text-[#111827] hover:bg-[#f5f5f5]"
                    >
                      Open Community Radar
                    </Link>
                    <Link
                      href={buildResearchHref({
                        query,
                        trackId: activeTrack.id,
                        personalized,
                        yearFrom,
                        yearTo,
                        cap,
                        sources: selectedSources,
                      })}
                      className="inline-flex h-8 items-center gap-2 rounded-[6px] bg-[#111827] px-3 text-[13px] text-white hover:bg-[#1f2937]"
                    >
                      Open Discovery Workspace
                    </Link>
                  </div>
                </div>

                <div className="mt-3 rounded-[12px] border border-[rgba(229,231,235,.6)] bg-white/80 px-4 py-3 text-[13px]">
                  <p className="font-medium text-[#111827]">
                    Research is the primary workspace for fast context search, track memory, and ranked paper review.
                  </p>
                  <p className="mt-1 text-[#6b7280]">
                    这里负责即时探索；更广的信号总览去 Dashboard，交付和订阅节奏统一放到 Settings。
                  </p>
                </div>

                <div className="my-4 flex flex-wrap gap-2">
                  {[
                    ["semantic_scholar", "Semantic Scholar"],
                    ["arxiv", "arXiv"],
                    ["openalex", "OpenAlex"],
                    ["papers_cool", "papers.cool"],
                    ["hf_daily", "HF Daily"],
                  ].map(([source, label]) => {
                    const active = selectedSources.includes(source)
                    return (
                      <Link
                        key={source}
                        href={buildResearchHref({
                          query,
                          trackId: activeTrack.id,
                          personalized,
                          yearFrom,
                          yearTo,
                          cap,
                          sources: toggleSource(selectedSources, source),
                        })}
                        className={[
                          "inline-flex h-7 items-center rounded-full border px-3 text-[12px]",
                          active
                            ? "border-[#111827] bg-[#111827] text-white"
                            : "border-[#e5e7eb] bg-white text-[#111827]",
                        ].join(" ")}
                      >
                        {label}
                      </Link>
                    )
                  })}
                </div>

                {papers.length === 0 ? (
                  <div className="rounded-[12px] border border-[#e5e7eb] bg-white px-6 py-10 text-center text-[#6b7280]">
                    No papers found. Try adjusting the query, track, or source filters. If this keeps happening, confirm the backend search service is running.
                  </div>
                ) : (
                  <div className="grid gap-3">
                    {papers.map((paper, index) => {
                      const paperReasons = reasons[paper.paper_id] || []
                      return (
                        <article key={paper.paper_id} className="rounded-[12px] border border-[#e5e7eb] bg-white px-5 py-[18px] transition-colors hover:border-[#cbd5e1] hover:shadow-[0_4px_6px_-1px_rgba(0,0,0,.05)]">
                          <div className="flex items-start gap-3">
                            <div className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[6px] bg-[#f5f5f5] text-[11px] font-semibold text-[#6b7280]">
                              #{index + 1}
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <h3 className="text-[15px] font-semibold leading-[1.45] text-[#111827]">{paper.title}</h3>
                                  <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-[#6b7280]">
                                    <span>{paper.authors?.slice(0, 3).join(", ") || "Unknown authors"}</span>
                                    {paper.venue ? <span>{paper.venue}</span> : null}
                                    {paper.year ? <span>{paper.year}</span> : null}
                                    <span>{paper.citation_count ?? 0} citations</span>
                                  </div>
                                </div>
                                <div className="text-right text-[12px] font-medium text-[#111827]">
                                  {typeof paper.latest_judge?.overall === "number"
                                    ? `Judge ${paper.latest_judge.overall.toFixed(1)}`
                                    : "Pending judge"}
                                </div>
                              </div>

                              <p className="mt-3 text-[13.5px] leading-[1.6] text-[#111827]/85">
                                {paper.latest_judge?.one_line_summary || paper.abstract || "No abstract available."}
                              </p>

                              <div className="mt-3 flex flex-wrap gap-1.5">
                                {(paperReasons.length > 0 ? paperReasons : activeTrack.keywords?.slice(0, 3) || []).map((tag) => (
                                  <span key={`${paper.paper_id}-${tag}`} className="rounded-[4px] bg-[#f3f4f6] px-2 py-1 text-[11px] text-[#6b7280]">
                                    {tag}
                                  </span>
                                ))}
                              </div>

                              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-dashed border-[#e5e7eb] pt-3">
                                <Link
                                  href={`/papers/${encodeURIComponent(paper.paper_id)}`}
                                  className="inline-flex h-7 items-center rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[12px] text-[#111827] hover:bg-[#f5f5f5]"
                                >
                                  Open detail
                                </Link>
                                {paper.url ? (
                                  <a
                                    href={paper.url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="inline-flex h-7 items-center rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[12px] text-[#111827] hover:bg-[#f5f5f5]"
                                  >
                                    Open source
                                  </a>
                                ) : null}
                                {paper.latest_judge?.recommendation ? (
                                  <span className="ml-auto text-[12px] text-[#6b7280]">
                                    {paper.latest_judge.recommendation.replaceAll("_", " ")}
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          </div>
                        </article>
                      )
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
