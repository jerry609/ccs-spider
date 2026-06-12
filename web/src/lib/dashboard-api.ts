import type {
  Activity,
  AnchorPreviewItem,
  IntelligenceFeedResponse,
  ReadingQueueItem,
  ResearchTrackContextResponse,
  ResearchTrackSummary,
  TrackFeedItem,
} from "./types"

export interface IntelligenceFeedFilters {
  source?: string
  keyword?: string
  repo?: string
  sortBy?: string
  sortOrder?: string
  trackId?: number
}

const API_BASE_URL = (process.env.PAPERBOT_API_BASE_URL || "http://127.0.0.1:8000") + "/api"

function formatDateLabel(value?: string | null): string {
  if (!value) return "Recently"
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return "Recently"
  return parsed.toLocaleDateString("en-US", { month: "short", day: "numeric" })
}

async function fetchJsonOrNull<T>(path: string, accessToken?: string): Promise<T | null> {
  try {
    const headers: Record<string, string> = {}
    if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`
    const res = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store", headers })
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

export async function fetchDashboardTracks(accessToken?: string): Promise<ResearchTrackSummary[]> {
  const payload = await fetchJsonOrNull<{ tracks?: ResearchTrackSummary[] }>(
    `/research/tracks`,
    accessToken,
  )
  return payload?.tracks || []
}

export async function fetchDashboardTrackFeed(
  trackId: number,
  accessToken?: string,
  limit: number = 6,
): Promise<{ items: TrackFeedItem[]; total: number }> {
  const qs = new URLSearchParams({ limit: String(limit), offset: "0" })
  const payload = await fetchJsonOrNull<{ items?: TrackFeedItem[]; total?: number }>(
    `/research/tracks/${encodeURIComponent(String(trackId))}/feed?${qs.toString()}`,
    accessToken,
  )
  return {
    items: payload?.items || [],
    total: Number(payload?.total || 0),
  }
}

export async function fetchDashboardTrackContext(
  trackId: number,
  accessToken?: string,
): Promise<ResearchTrackContextResponse | null> {
  return fetchJsonOrNull<ResearchTrackContextResponse>(
    `/research/tracks/${encodeURIComponent(String(trackId))}/context`,
    accessToken,
  )
}

export async function fetchDashboardAnchors(
  trackId: number,
  accessToken?: string,
  limit: number = 4,
): Promise<AnchorPreviewItem[]> {
  const qs = new URLSearchParams({
    limit: String(limit),
    window_days: "730",
    personalized: "true",
  })
  const payload = await fetchJsonOrNull<{ items?: AnchorPreviewItem[] }>(
    `/research/tracks/${encodeURIComponent(String(trackId))}/anchors/discover?${qs.toString()}`,
    accessToken,
  )
  return payload?.items || []
}

export async function fetchDashboardReadingQueue(
  accessToken?: string,
  limit: number = 8,
): Promise<ReadingQueueItem[]> {
  const qs = new URLSearchParams({ limit: String(limit) })
  const payload = await fetchJsonOrNull<{
    items?: Array<{
      saved_at?: string
      paper?: {
        id?: number | string
        title?: string
        year?: number | null
        authors?: string[]
      }
    }>
  }>(`/research/papers/saved?${qs.toString()}`, accessToken)

  return (payload?.items || []).map((row, index) => {
    const paper = row.paper || {}
    return {
      id: `${paper.id ?? index + 1}`,
      paper_id: String(paper.id ?? ""),
      title: paper.title || "Untitled paper",
      authors: (paper.authors || []).join(", "),
      saved_at: row.saved_at,
      priority: index + 1,
    }
  })
}

export async function fetchIntelligenceFeed(
  accessToken?: string,
  limit: number = 6,
  filters?: IntelligenceFeedFilters,
): Promise<IntelligenceFeedResponse> {
  const qs = new URLSearchParams({
    limit: String(limit),
  })
  if (filters?.source) qs.set("source", filters.source)
  if (filters?.keyword) qs.set("keyword", filters.keyword)
  if (filters?.repo) qs.set("repo", filters.repo)
  if (filters?.sortBy) qs.set("sort_by", filters.sortBy)
  if (filters?.sortOrder) qs.set("sort_order", filters.sortOrder)
  if (filters?.trackId) qs.set("track_id", String(filters.trackId))

  const payload = await fetchJsonOrNull<IntelligenceFeedResponse>(`/intelligence/feed?${qs.toString()}`, accessToken)
  return payload || {
    items: [],
    refreshed_at: null,
    refresh_scheduled: false,
    keywords: [],
    watch_repos: [],
    subreddits: [],
  }
}

export async function fetchDashboardActivities(accessToken?: string): Promise<Activity[]> {
  const [runsPayload, savedPayload] = await Promise.all([
    fetchJsonOrNull<{
      runs?: Array<{
        run_id: number
        source: string
        status: string
        total_found: number
        created_at: string
      }>
    }>(`/harvest/runs?limit=4`),
    fetchJsonOrNull<{
      items?: Array<{
        saved_at?: string
        paper?: {
          id?: number | string
          title?: string
          authors?: string[]
          venue?: string | null
          year?: number | null
        }
      }>
    }>(`/research/papers/saved?limit=4`, accessToken),
  ])

  const activities: Activity[] = []

  for (const run of runsPayload?.runs || []) {
    activities.push({
      id: `harvest-${run.run_id}`,
      type: "milestone",
      timestamp: formatDateLabel(run.created_at),
      milestone: {
        title: `${run.source} harvest`,
        description: `${run.total_found} papers · ${run.status}`,
        current_value: run.total_found,
        trend: run.status === "completed" ? "up" : "flat",
      },
    })
  }

  for (const [index, row] of (savedPayload?.items || []).entries()) {
    const paper = row.paper || {}
    const firstAuthor = paper.authors?.[0] || "Unknown"
    activities.push({
      id: `saved-${paper.id || paper.title || index}`,
      type: "published",
      timestamp: formatDateLabel(row.saved_at),
      scholar: {
        name: firstAuthor,
        avatar: `https://avatar.vercel.sh/${encodeURIComponent(firstAuthor)}.png`,
        affiliation: "",
      },
      paper: {
        title: paper.title || "Untitled paper",
        venue: paper.venue || "",
        year: String(paper.year || ""),
        citations: 0,
        tags: [],
      },
    })
  }

  return activities.slice(0, 8)
}
