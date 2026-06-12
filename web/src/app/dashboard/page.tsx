import Link from "next/link"
import { unstable_noStore as noStore } from "next/cache"

import { auth } from "@/auth"
import { fetchDeadlineRadar, fetchPapers } from "@/lib/api"
import { fetchLatestDashboardBrief } from "@/lib/dashboard-brief"
import {
  buildDashboardIntelligenceCards,
  summarizeDashboardIntelligence,
} from "@/lib/dashboard-intelligence"
import {
  fetchDashboardReadingQueue,
  fetchDashboardTrackFeed,
  fetchDashboardTracks,
  fetchIntelligenceFeed,
} from "@/lib/dashboard-api"
import { safeHref, safeInternalHref } from "@/lib/utils"
import type {
  Paper,
  ReadingQueueItem,
  ResearchTrackSummary,
  TrackFeedItem,
} from "@/lib/types"

export const dynamic = "force-dynamic"
export const revalidate = 0

type DashboardRecommendationCardData = {
  id: string
  paperRef: string | null
  internalPaperId: number | null
  title: string
  href: string
  meta: string
  summary: string
  tags: string[]
  metric: string
  recommendation?: string | null
  authors: string[]
  year?: number | null
  paperSource?: "arxiv" | "semantic_scholar" | "openalex" | null
  isSaved?: boolean
}

type DashboardQueueCard = {
  id: string
  paperRef: string | null
  internalPaperId: number | null
  title: string
  venue: string
  summary: string
  tags: string[]
  sourceLabel: string
  priority: "high" | "medium" | "low"
  timeLabel: string
  href: string
  authors: string[]
  isExternal?: boolean
}

type SignalTableRow = {
  id: string
  title: string
  sourceLabel: string
  metricLabel: string
  href: string
  isExternal: boolean
  summary: string
}

function formatRelativeTime(value?: string | null): string {
  if (!value) return "just now"

  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value

  const diffMs = Date.now() - parsed.getTime()
  const diffMinutes = Math.max(1, Math.floor(diffMs / 60_000))
  const diffHours = Math.floor(diffMinutes / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMinutes < 60) return `${diffMinutes}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays === 1) return "yesterday"
  if (diffDays < 7) return `${diffDays}d ago`

  return parsed.toLocaleDateString("en-US", { month: "short", day: "numeric" })
}

function formatRecommendationLabel(value?: string | null): string | null {
  const normalized = String(value || "").trim()
  if (!normalized) return null

  switch (normalized) {
    case "must_read":
      return "Must read"
    case "worth_reading":
      return "Worth reading"
    case "skim":
      return "Skim"
    case "skip":
      return "Skip"
    default:
      return normalized
  }
}

function getQueuePriority(item: ReadingQueueItem, index: number): "high" | "medium" | "low" {
  if (typeof item.priority === "number") {
    if (item.priority <= 2) return "high"
    if (item.priority <= 4) return "medium"
    return "low"
  }

  if (index < 2) return "high"
  if (index < 4) return "medium"
  return "low"
}

function getRecommendationPriority(
  recommendation?: string | null,
  index = 0,
): "high" | "medium" | "low" {
  switch (String(recommendation || "").trim().toLowerCase()) {
    case "must read":
    case "must_read":
      return "high"
    case "worth reading":
    case "worth_reading":
      return "medium"
    case "skim":
    case "skip":
      return "low"
    default:
      return index < 2 ? "high" : "medium"
  }
}

function isExternalUrl(value: string): boolean {
  return /^https?:\/\//.test(value)
}

function normalizePaperRef(value: unknown): string | null {
  const normalized = String(value ?? "").trim()
  return normalized || null
}

function parseInternalPaperId(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isInteger(value) && value >= 0 ? value : null
  }

  if (typeof value !== "string") return null

  const normalized = value.trim()
  if (!normalized) return null

  const parsed = Number(normalized)
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null
}

function buildRecommendationCards(args: {
  trackFeedItems: TrackFeedItem[]
  activeTrack: ResearchTrackSummary | null
}): DashboardRecommendationCardData[] {
  const { trackFeedItems, activeTrack } = args

  return trackFeedItems.slice(0, 4).map((item, index) => {
    const recommendation = formatRecommendationLabel(item.latest_judge?.recommendation)
    const judgeScore = item.latest_judge?.overall
    const metric = judgeScore != null
      ? `Judge ${Number(judgeScore).toFixed(1)}`
      : `Feed ${item.feed_score.toFixed(1)}`
    const paperRef = normalizePaperRef(item.paper.id)

    return {
      id: paperRef || `track-feed-${index}`,
      paperRef,
      internalPaperId: parseInternalPaperId(item.paper.id),
      title: item.paper.title,
      href: `/papers/${item.paper.id}`,
      meta: item.paper.venue || activeTrack?.name || "Recommendation",
      summary: recommendation
        ? `${recommendation} in the active track feed.`
        : "Pulled into today’s shortlist.",
      tags: item.matched_terms.slice(0, 3),
      metric,
      recommendation,
      authors: [],
      year: item.paper.year ?? null,
      paperSource: null,
      isSaved: String(item.latest_feedback_action || "").trim().toLowerCase() === "save",
    }
  })
}

function buildReadingQueueCards(args: {
  recommendations: DashboardRecommendationCardData[]
  readingQueue: ReadingQueueItem[]
  paperMap: Map<string, Paper>
  activeTrack: ResearchTrackSummary | null
  latestBriefTime?: string | null
  latestBriefSource?: string | null
}): DashboardQueueCard[] {
  const {
    recommendations,
    readingQueue,
    paperMap,
    activeTrack,
    latestBriefTime,
    latestBriefSource,
  } = args

  if (recommendations.length > 0) {
    const sourceLabel = latestBriefSource ? `${latestBriefSource} brief` : "Daily brief"
    const timeLabel = formatRelativeTime(latestBriefTime)

    return recommendations.slice(0, 3).map((item, index) => ({
      id: item.id,
      paperRef: item.paperRef,
      internalPaperId: item.internalPaperId,
      title: item.title,
      venue: item.meta,
      summary: item.summary,
      tags: item.tags,
      sourceLabel,
      priority: getRecommendationPriority(item.recommendation, index),
      timeLabel,
      href: item.href,
      authors: item.authors,
      isExternal: isExternalUrl(item.href),
    }))
  }

  const fallbackTags = (activeTrack?.keywords || []).slice(0, 2)

  return readingQueue.slice(0, 3).map((item, index) => {
    const paperRef = normalizePaperRef(item.paper_id)
    const paper = paperMap.get(paperRef || String(item.id))
    const internalPaperId = parseInternalPaperId(item.paper_id)
    const tags = (
      paper?.tags?.length
        ? paper.tags
        : fallbackTags.length
          ? fallbackTags
          : ["Reading queue"]
    ).slice(0, 3)

    return {
      id: String(item.id),
      paperRef,
      internalPaperId,
      title: item.title,
      venue:
        paper?.venue ||
        (item.authors ? item.authors.split(",").slice(0, 2).join(", ") : "Paper library"),
      summary: activeTrack ? `Queued for ${activeTrack.name}.` : "Queued for review.",
      tags,
      sourceLabel: activeTrack ? `${activeTrack.name} queue` : "Reading queue",
      priority: getQueuePriority(item, index),
      timeLabel: formatRelativeTime(item.saved_at),
      href: paperRef ? `/papers/${paperRef}` : "/papers",
      authors: item.authors ? item.authors.split(",").map((value) => value.trim()).filter(Boolean) : [],
      isExternal: false,
    }
  })
}

function firstAuthor(authors: string[]): string {
  return authors[0]?.trim() || "Author"
}

function statusPillClass(priority: "high" | "medium" | "low"): string {
  if (priority === "high") return "bg-[#f5f5f5] text-[#171717]"
  if (priority === "medium") return "bg-[#171717] text-[#fafafa]"
  return "bg-[#bb4d00] text-[#fafafa]"
}

function statusLabel(priority: "high" | "medium" | "low"): string {
  if (priority === "high") return "High"
  if (priority === "medium") return "Medium"
  return "Low"
}

function chartSeries(seed: number, amp: number, offsetY = 0) {
  let s = seed
  const next = () => {
    s = (s * 9301 + 49297) % 233280
    return s / 233280
  }

  const points: Array<[number, number]> = []
  const x0 = 5
  const x1 = 715
  const yTop = 5
  const yBot = 195
  const count = 60

  for (let i = 0; i < count; i += 1) {
    const x = x0 + (x1 - x0) * (i / (count - 1))
    const base = 0.45 + 0.15 * Math.sin(i / 3.3) + 0.1 * Math.cos(i / 7)
    const spike = next() > 0.72 ? next() * 0.5 : next() * 0.15
    const v = Math.min(1, base + spike * amp)
    const y = yBot - (yBot - yTop) * v + offsetY
    points.push([x, y])
  }

  return points
}

function toPath(points: Array<[number, number]>, close = false): string {
  if (!points.length) return ""
  const start = `M ${points[0][0].toFixed(1)} ${points[0][1].toFixed(1)}`
  const lines = points
    .slice(1)
    .map(([x, y]) => `L ${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ")

  if (!close) return `${start} ${lines}`
  return `${start} ${lines} L 715 195 L 5 195 Z`
}

function deadlineWidth(daysLeft: number): number {
  if (daysLeft <= 3) return 75
  if (daysLeft <= 14) return 50
  return 50
}

function DashboardStatCard({
  label,
  value,
  badge,
  badgeTone = "default",
  title,
  description,
  href,
}: {
  label: string
  value: number
  badge: string
  badgeTone?: "default" | "green"
  title: string
  description: string
  href?: string
}) {
  const inner = (
    <div className="flex h-full flex-col gap-4 rounded-[14px] border border-[#e5e5e5] bg-white px-0 py-6 shadow-[0_1px_3px_rgba(0,0,0,0.1),0_1px_2px_rgba(0,0,0,0.08)]">
      <div className="px-6 text-[14px] leading-5 text-[#737373]">{label}</div>
      <div className="px-6 text-[24px] font-semibold leading-8 text-[#0a0a0a]">{value}</div>
      <div className="px-6">
        <span
          className={[
            "inline-flex items-center rounded-[8px] px-2 py-0.5 text-[12px] leading-4",
            badgeTone === "green"
              ? "bg-[#e1f5e8] text-[#1d5c35]"
              : "bg-[#f5f5f5] text-[#171717]",
          ].join(" ")}
        >
          {badge}
        </span>
      </div>
      <div className="flex flex-col gap-1 px-6">
        <div className="text-[14px] font-medium text-[#0a0a0a]">{title}</div>
        <div className="text-[14px] text-[#737373]">{description}</div>
      </div>
    </div>
  )

  if (!href) return inner

  const safeLink = safeInternalHref(href)
  if (!safeLink) return inner

  return (
    <Link
      href={safeLink}
      className="transition-transform duration-150 ease-out hover:-translate-y-0.5 hover:[&>div]:border-[#d4d4d4] hover:[&>div]:shadow-[0_2px_6px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.06)]"
    >
      {inner}
    </Link>
  )
}

function TableSection({
  title,
  rows,
  actionLabel,
  anchorId,
}: {
  title: string
  rows: Array<{
    key: string
    paper: string
    author: string
    status: string
    statusTone: "high" | "medium" | "low"
    keyword: string
    action?: { label: string; href: string; external?: boolean }
  }>
  actionLabel?: string
  anchorId?: string
}) {
  return (
    <section id={anchorId}>
      <div className="px-0 py-4 text-[14px] font-medium text-black">{title}</div>
      <div className="overflow-hidden rounded-[8px] border border-[#e5e5e5] bg-white">
        <table className="w-full border-collapse text-[14px]">
          <thead>
            <tr className="border-b border-[#e5e5e5]">
              <th className="w-9 px-3 py-2.5 text-left font-medium text-[#0a0a0a]">
                <span className="inline-block h-4 w-4 rounded-[4px] border border-[#e5e5e5] bg-white shadow-sm" />
              </th>
              <th className="px-3 py-2.5 text-left font-medium text-[#0a0a0a]">Paper</th>
              <th className="w-[200px] px-3 py-2.5 text-left font-medium text-[#0a0a0a]">Author</th>
              <th className="w-[140px] px-3 py-2.5 text-left font-medium text-[#0a0a0a]">Status</th>
              <th className="w-[160px] px-3 py-2.5 text-left font-medium text-[#0a0a0a]">Keyword</th>
              <th className="w-[90px] px-3 py-2.5 text-left font-medium text-[#0a0a0a]">{actionLabel || ""}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.key} className={index === 0 ? "" : "border-t border-[rgba(229,229,229,0.6)]"}>
                <td className="px-3 py-2.5 align-middle">
                  <span className="inline-block h-4 w-4 rounded-[4px] border border-[#e5e5e5] bg-white shadow-sm" />
                </td>
                <td className="px-3 py-2.5 align-middle font-medium text-[#0a0a0a]">{row.paper}</td>
                <td className="px-3 py-2.5 align-middle text-[#0a0a0a]">{row.author}</td>
                <td className="px-3 py-2.5 align-middle">
                  <span
                    className={[
                      "inline-flex items-center justify-center rounded-[8px] px-2 py-0.5 text-[12px] font-medium leading-4",
                      statusPillClass(row.statusTone),
                    ].join(" ")}
                  >
                    {row.status}
                  </span>
                </td>
                <td className="px-3 py-2.5 align-middle text-[#0a0a0a]">{row.keyword}</td>
                <td className="px-3 py-2.5 align-middle">
                  {row.action ? (
                    row.action.external ? (
                      <a
                        href={safeHref(row.action.href) || "#"}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center justify-center rounded-[8px] bg-[#bb4d00] px-2 py-0.5 text-[12px] font-medium leading-4 text-[#fafafa]"
                      >
                        {row.action.label}
                      </a>
                    ) : (
                      <Link
                        href={safeInternalHref(row.action.href) || "#"}
                        className="inline-flex items-center justify-center rounded-[8px] bg-[#bb4d00] px-2 py-0.5 text-[12px] font-medium leading-4 text-[#fafafa]"
                      >
                        {row.action.label}
                      </Link>
                    )
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export default async function DashboardPage() {
  noStore()
  const session = await auth()
  const accessToken = session?.accessToken

  const [
    tracksResult,
    readingQueueResult,
    intelligenceResult,
    papersResult,
    latestBriefResult,
    deadlinesResult,
  ] = await Promise.allSettled([
    fetchDashboardTracks(accessToken),
    fetchDashboardReadingQueue(accessToken, 6),
    fetchIntelligenceFeed(accessToken, 20, { sortBy: "delta", sortOrder: "desc" }),
    fetchPapers(accessToken),
    fetchLatestDashboardBrief(),
    fetchDeadlineRadar(accessToken),
  ])

  const tracks = tracksResult.status === "fulfilled" ? tracksResult.value : []
  const readingQueue = readingQueueResult.status === "fulfilled" ? readingQueueResult.value : []
  const intelligenceFeed = intelligenceResult.status === "fulfilled"
    ? intelligenceResult.value
    : { items: [], refreshed_at: null, refresh_scheduled: false, keywords: [], watch_repos: [], subreddits: [] }
  const papers = papersResult.status === "fulfilled" ? papersResult.value : []
  const latestBrief = latestBriefResult.status === "fulfilled" ? latestBriefResult.value : null
  const deadlinesRaw = deadlinesResult.status === "fulfilled" ? deadlinesResult.value : []

  const orderedTracks = [...tracks].sort(
    (left, right) => Number(Boolean(right.is_active)) - Number(Boolean(left.is_active)),
  )
  const activeTrack = orderedTracks[0] || null
  const activeTrackFeed = activeTrack
    ? await fetchDashboardTrackFeed(activeTrack.id, accessToken, 4).catch(() => ({ items: [], total: 0 }))
    : { items: [], total: 0 }

  const recommendationCards: DashboardRecommendationCardData[] = latestBrief?.recommendations.length
    ? latestBrief.recommendations.map((item) => ({
        id: item.id,
        paperRef: normalizePaperRef(item.paperId),
        internalPaperId: parseInternalPaperId(item.paperId),
        title: item.title,
        href: item.href,
        meta: item.meta,
        summary: item.summary,
        tags: item.tags,
        metric: item.metric,
        recommendation: item.recommendation,
        authors: item.authors,
        year: item.year ?? null,
        paperSource: item.paperSource ?? null,
        isSaved: false,
      }))
    : buildRecommendationCards({
        trackFeedItems: activeTrackFeed.items || [],
        activeTrack,
      })

  const paperMap = new Map(papers.map((paper) => [String(paper.id), paper]))
  const queueCards = buildReadingQueueCards({
    recommendations: recommendationCards,
    readingQueue,
    paperMap,
    activeTrack,
    latestBriefTime: latestBrief?.generatedAt || latestBrief?.date || null,
    latestBriefSource: latestBrief?.sourceLabel || null,
  })

  const intelligenceCards = buildDashboardIntelligenceCards(intelligenceFeed.items)
  const intelligenceSummary = summarizeDashboardIntelligence(intelligenceFeed.items)
  const signalRows: SignalTableRow[] = intelligenceCards.slice(0, 3).map((item) => ({
    id: item.id,
    title: item.title,
    sourceLabel: item.sourceLabel,
    metricLabel: item.metricLabel,
    href: item.href,
    isExternal: item.isExternal,
    summary: item.summary,
  }))

  const deadlines = [...deadlinesRaw]
    .sort((left, right) => left.days_left - right.days_left)
    .slice(0, 3)

  const libraryCount = papers.length
  const queueCount = queueCards.length
  const trackCount = tracks.length
  const signalCount = Math.max(signalRows.length, intelligenceSummary.totalCount)
  const urgentDeadlines = deadlinesRaw.filter((item) => item.days_left <= 14).length

  const chartAPoints = chartSeries(7, 1)
  const chartBPoints = chartSeries(42, 0.75, 18)
  const chartXAxis = ["Apr 7", "Apr 16", "Apr 25", "May 4", "May 13", "May 23", "Jun 1", "Jun 10", "Jun 19", "Jun 30"]
  const chartXAxisX = [43, 111, 180, 253, 322, 400, 478, 547, 619, 688]

  const readingQueueRows = queueCards.slice(0, 2).map((item, index) => ({
    key: item.id,
    paper: item.title,
    author: firstAuthor(item.authors),
    status: statusLabel(item.priority),
    statusTone: item.priority,
    keyword: item.tags[0] || activeTrack?.keywords?.[0] || "Eddie Lake",
    action: index === 1
      ? {
          label: "Open",
          href: item.href,
          external: Boolean(item.isExternal),
        }
      : undefined,
  }))

  const signalsTableRows = signalRows.slice(0, 1).map((item) => ({
    key: item.id,
    paper: item.title,
    author: item.sourceLabel,
    status: "High",
    statusTone: "high" as const,
    keyword: item.metricLabel,
    action: {
      label: "Source",
      href: item.href,
      external: item.isExternal,
    },
  }))

  const trackUpdates = activeTrackFeed.total ? 1 : 0

  return (
    <div className="min-h-screen bg-transparent text-[#0a0a0a]">
      <main className="mx-auto w-full max-w-[1280px] px-9 py-10 pb-16">
        <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0a0a0a]">
          Dashboard
        </div>
        <h1 className="mt-2 text-[36px] font-bold leading-[1.1] tracking-[-0.025em] text-[#0a0a0a]">
          Oh, God! 你回来了！
        </h1>
        <p className="mt-2 text-[13.6px] text-[#45556c]">
          {queueCount} picks ready, {urgentDeadlines} urgent deadlines, {Math.min(signalCount, 3)} fresh signals.
        </p>

        <section className="mt-7 grid grid-cols-1 gap-4 xl:grid-cols-4">
          <DashboardStatCard
            label="Library"
            value={libraryCount}
            badge={`${Math.max(0, recommendationCards.length)} Ready`}
            badgeTone="green"
            title="Repo Growth"
            description={`${Math.max(0, recommendationCards.length)} codes verified this month`}
            href="/papers"
          />
          <DashboardStatCard
            label="Reading queue"
            value={queueCount}
            badge={`${queueCards.filter((item) => item.priority === "high").length} high priority`}
            title="Priority Queue"
            description={`${queueCards.filter((item) => item.priority === "high").length} urgent papers to review`}
            href="/dashboard#readingQueue"
          />
          <DashboardStatCard
            label="Tracks"
            value={trackCount}
            badge={`${trackUpdates} with updates`}
            title="Field Activity"
            description={`New signals detected in ${activeTrack?.name || "LLM"}`}
            href="/tracks"
          />
          <DashboardStatCard
            label="Signals"
            value={signalCount}
            badge={`${urgentDeadlines} deadlines soon`}
            title="Agent Efficiency"
            description="Successful re-runs up 20%"
          />
        </section>

        <section className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="flex flex-col rounded-[14px] border border-[#e5e5e5] bg-white py-6 shadow-[0_1px_3px_rgba(0,0,0,0.1),0_1px_2px_rgba(0,0,0,0.08)]">
            <div className="flex items-start justify-between gap-4 px-6 pb-1">
              <div>
                <div className="text-[16px] font-semibold leading-none text-[#0a0a0a]">Total Tokens</div>
                <div className="mt-1.5 text-[14px] text-[#737373]">Total for the last 3 months</div>
              </div>
              <button
                type="button"
                className="inline-flex items-center gap-2 rounded-[10px] border border-[#e5e5e5] bg-white px-3 py-2 text-[14px] text-[#0a0a0a] shadow-sm"
              >
                <span>Last 3 months</span>
                <span className="h-3 w-3 -translate-y-0.5 rotate-45 border-b-[1.4px] border-r-[1.4px] border-[#737373] opacity-60" />
              </button>
            </div>

            <div className="flex-1 px-6 pt-5">
              <svg className="block h-[250px] w-full" viewBox="0 0 720 250" preserveAspectRatio="none" aria-hidden="true">
                <defs>
                  <linearGradient id="areaA" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="5%" stopColor="#000" stopOpacity=".48" />
                    <stop offset="95%" stopColor="#000" stopOpacity=".06" />
                  </linearGradient>
                  <linearGradient id="areaB" x1="0" x2="0" y1="0" y2="1">
                    <stop offset="5%" stopColor="#000" stopOpacity=".32" />
                    <stop offset="95%" stopColor="#000" stopOpacity=".04" />
                  </linearGradient>
                </defs>

                <g stroke="rgba(229,229,229,.6)" strokeWidth="1">
                  <line x1="5" x2="715" y1="5" y2="5" />
                  <line x1="5" x2="715" y1="51" y2="51" />
                  <line x1="5" x2="715" y1="97" y2="97" />
                  <line x1="5" x2="715" y1="143" y2="143" />
                  <line x1="5" x2="715" y1="189" y2="189" />
                </g>

                <path d={toPath(chartBPoints, true)} fill="url(#areaB)" stroke="none" />
                <path d={toPath(chartBPoints)} fill="none" stroke="#000" strokeOpacity=".55" strokeWidth="1.2" />
                <path d={toPath(chartAPoints, true)} fill="url(#areaA)" stroke="none" />
                <path d={toPath(chartAPoints)} fill="none" stroke="#000" strokeOpacity=".8" strokeWidth="1.4" />

                <g fill="#737373" fontSize="12" textAnchor="middle">
                  {chartXAxis.map((label, index) => (
                    <text key={label} x={chartXAxisX[index]} y="218">
                      {label}
                    </text>
                  ))}
                </g>
              </svg>

              <div className="flex justify-center gap-6 pt-3.5">
                <span className="inline-flex items-center gap-1.5 text-[12px] text-[#0a0a0a]">
                  <span className="h-2 w-2 rounded-[2px] bg-[#0a0a0a]" />
                  Mobile
                </span>
                <span className="inline-flex items-center gap-1.5 text-[12px] text-[#0a0a0a]">
                  <span className="h-2 w-2 rounded-[2px] bg-[#737373]" />
                  Desktop
                </span>
              </div>
            </div>
          </div>

          <aside className="flex flex-col gap-4 rounded-[14px] bg-[rgba(245,245,245,.5)] p-4">
            {deadlines.length > 0 ? (
              deadlines.map((item) => (
                <div key={`${item.name}-${item.deadline}`} className="flex flex-col gap-2">
                  <div>
                    <div className="text-[13.7px] font-bold text-[#1d293d]">{item.name}</div>
                    <div className="text-[12px] text-[#62748e]">
                      {item.field} · {item.days_left} days left
                    </div>
                  </div>
                  <div className="relative h-2 w-full overflow-hidden rounded-full bg-[#f5f5f5]">
                    <div
                      className="absolute inset-y-0 left-0 rounded-full bg-[#171717]"
                      style={{ width: `${deadlineWidth(item.days_left)}%` }}
                    />
                  </div>
                </div>
              ))
            ) : (
              <div className="text-[13px] text-[#737373]">No nearby deadlines.</div>
            )}
          </aside>
        </section>

        <section className="mt-4 flex flex-col gap-6 rounded-[14px] bg-[rgba(245,245,245,.5)] p-4">
          <TableSection title="Reading Queue" rows={readingQueueRows} anchorId="readingQueue" />
          <TableSection title="Signals" rows={signalsTableRows} />
        </section>
      </main>
    </div>
  )
}
