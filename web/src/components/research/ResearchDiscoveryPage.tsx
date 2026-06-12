"use client"

import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "next/navigation"

import { ArrowLeft, Compass } from "lucide-react"
import { fetchJson, getErrorMessage } from "@/lib/fetch"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

import DiscoveryGraphWorkspace from "./DiscoveryGraphWorkspace"

type SeedType = "doi" | "arxiv" | "openalex" | "semantic_scholar" | "author"
type Track = {
  id: number
  name: string
  is_active?: boolean
}

export default function ResearchDiscoveryPage() {
  const searchParams = useSearchParams()
  const [tracks, setTracks] = useState<Track[]>([])
  const [activeTrackId, setActiveTrackId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const routeTrackId = Number(searchParams.get("track_id") || 0)
  const routeQuery = searchParams.get("query")?.trim() || ""
  const routeSeedId = searchParams.get("seed_id")?.trim() || ""
  const routeSeedType = useMemo<SeedType>(() => {
    const value = searchParams.get("seed_type")?.trim() || ""
    if (
      value === "doi" ||
      value === "arxiv" ||
      value === "openalex" ||
      value === "semantic_scholar" ||
      value === "author"
    ) {
      return value
    }
    return "doi"
  }, [searchParams])

  const activeTrack = useMemo(
    () => tracks.find((track) => track.id === activeTrackId) || null,
    [tracks, activeTrackId]
  )

  const refreshTracks = useCallback(async () => {
    const data = await fetchJson<{ tracks: Track[] }>(
      `/api/research/tracks`
    )
    setTracks(data.tracks || [])
    const active = data.tracks.find((track) => track.is_active)
    setActiveTrackId(active?.id ?? null)
  }, [])

  const activateTrack = useCallback(async (trackId: number) => {
    setLoading(true)
    setError(null)
    try {
      await fetchJson(
        `/api/research/tracks/${trackId}/activate`,
        {
          method: "POST",
          body: "{}",
          headers: { "Content-Type": "application/json" },
        }
      )
      await refreshTracks()
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [refreshTracks])

  useEffect(() => {
    refreshTracks().catch((err) => setError(getErrorMessage(err)))
  }, [refreshTracks])

  useEffect(() => {
    if (!routeTrackId || !Number.isFinite(routeTrackId)) return
    if (!tracks.some((track) => track.id === routeTrackId)) return
    if (activeTrackId === routeTrackId) return
    activateTrack(routeTrackId).catch(() => {})
  }, [activateTrack, activeTrackId, routeTrackId, tracks])

  async function handleDiscoverySave(paper: {
    paper_id: string
    title: string
    abstract?: string
    authors?: string[]
    year?: number
    venue?: string
    citation_count?: number
    url?: string
    source?: string
  }) {
    await fetchJson(`/api/research/papers/feedback`, {
      method: "POST",
      body: JSON.stringify({
        track_id: activeTrackId,
        paper_id: paper.paper_id,
        action: "save",
        weight: 1.0,
        metadata: {
          import_source: "discovery_graph",
          anchor_mode: "personalized",
        },
        paper_title: paper.title,
        paper_abstract: paper.abstract || "",
        paper_authors: paper.authors || [],
        paper_year: paper.year,
        paper_venue: paper.venue,
        paper_citation_count: paper.citation_count || 0,
        paper_url: paper.url || "",
        paper_source: paper.source || "semantic_scholar",
      }),
      headers: { "Content-Type": "application/json" },
    })
  }

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-gradient-to-b from-background via-background to-muted/20 py-6 sm:py-8">
      <div className="mx-auto w-full max-w-[1400px] px-4 sm:px-6 lg:px-8">
        <Card className="mb-4 border-border/70 bg-card/85">
          <CardHeader className="pb-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2 text-lg sm:text-xl">
                <Compass className="h-5 w-5 text-primary" />
                Discovery Workspace
              </CardTitle>
              <Button asChild size="sm" variant="outline">
                <Link href="/research">
                  <ArrowLeft className="h-4 w-4" />
                  Back to Research
                </Link>
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-3 pt-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline">Track: {activeTrack?.name || "Global"}</Badge>
              {routeQuery ? <Badge variant="secondary">From query: {routeQuery}</Badge> : null}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="text-sm text-muted-foreground">Active Track</label>
              <select
                value={activeTrackId ?? ""}
                onChange={(event) => {
                  const nextTrackId = Number(event.target.value)
                  if (!nextTrackId || Number.isNaN(nextTrackId)) return
                  activateTrack(nextTrackId).catch(() => {})
                }}
                className="h-9 min-w-[220px] rounded-md border bg-background px-2 text-sm"
                disabled={loading || tracks.length === 0}
              >
                {tracks.map((track) => (
                  <option key={track.id} value={track.id}>
                    {track.name}
                  </option>
                ))}
              </select>
            </div>
          </CardContent>
        </Card>

        {error ? (
          <Card className="mb-4 border-destructive/40">
            <CardContent className="p-3 text-sm text-destructive">{error}</CardContent>
          </Card>
        ) : null}

        <DiscoveryGraphWorkspace
          className="mt-0"
          trackId={activeTrackId}
          onSavePaper={handleDiscoverySave}
          initialSeedType={routeSeedType}
          initialSeedId={routeSeedId}
        />
      </div>
    </div>
  )
}
