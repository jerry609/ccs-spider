"use client"

import { useEffect, useMemo, useState } from "react"
import Link from "next/link"
import { Copy, Download, ExternalLink, FileText, FolderSync, Loader2, RefreshCw, Search } from "lucide-react"

import type { ResearchTrackContextResponse } from "@/lib/types"
import { buildObsidianExportCommand, describeObsidianScope } from "@/lib/obsidian"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet"

export type TrackTableRow = {
  id: number
  name: string
  saved: number
  feedback: number
  approved: number
  coverage: string
  pending: number
  desc: string
}

type MemoryItem = {
  id: number
  kind?: string
  content?: string
  tags?: string[]
  created_at?: string | null
}

type MemoryResponse = {
  user_id: string
  items: MemoryItem[]
}

interface TracksTableClientProps {
  initialRows: TrackTableRow[]
  initialQuery?: string
}

function formatWhen(value?: string | null): string {
  if (!value) return ""
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

export function TracksTableClient({ initialRows, initialQuery = "" }: TracksTableClientProps) {
  const [query, setQuery] = useState(initialQuery)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [drawerTab, setDrawerTab] = useState<"saved" | "memory">("saved")
  const [activeTrack, setActiveTrack] = useState<TrackTableRow | null>(null)
  const [trackContext, setTrackContext] = useState<ResearchTrackContextResponse | null>(null)
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([])
  const [loadingDrawer, setLoadingDrawer] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [relatedWorkOpen, setRelatedWorkOpen] = useState(false)
  const [relatedWorkTopic, setRelatedWorkTopic] = useState("")
  const [relatedWorkLoading, setRelatedWorkLoading] = useState(false)
  const [relatedWorkMarkdown, setRelatedWorkMarkdown] = useState("")
  const [relatedWorkError, setRelatedWorkError] = useState<string | null>(null)
  const [relatedWorkCopied, setRelatedWorkCopied] = useState(false)
  const [obsidianOpen, setObsidianOpen] = useState(false)
  const [obsidianVaultPath, setObsidianVaultPath] = useState("~/Obsidian/MyVault")
  const [obsidianRootDir, setObsidianRootDir] = useState("PaperBot")
  const [obsidianCopied, setObsidianCopied] = useState(false)
  const [obsidianCopyError, setObsidianCopyError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [exportingFormat, setExportingFormat] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return initialRows
    return initialRows.filter(
      (item) =>
        item.name.toLowerCase().includes(keyword) ||
        item.desc.toLowerCase().includes(keyword),
    )
  }, [initialRows, query])

  const allVisibleSelected = filtered.length > 0 && filtered.every((row) => selectedIds.includes(row.id))

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => filtered.some((row) => row.id === id)))
  }, [filtered])

  async function loadDrawer(track: TrackTableRow) {
    setLoadingDrawer(true)
    setDrawerError(null)
    try {
      const [contextRes, memoryRes] = await Promise.all([
        fetch(`/api/research/tracks/${track.id}/context`, { cache: "no-store" }),
        fetch(`/api/research/memory/inbox?track_id=${track.id}&limit=100`, { cache: "no-store" }),
      ])

      if (!contextRes.ok) {
        throw new Error(`${contextRes.status} ${contextRes.statusText}`)
      }

      const contextPayload = (await contextRes.json()) as ResearchTrackContextResponse
      const memoryPayload = memoryRes.ok
        ? ((await memoryRes.json()) as MemoryResponse)
        : { user_id: "", items: [] }

      setTrackContext(contextPayload)
      setMemoryItems(memoryPayload.items || [])
    } catch (error) {
      setDrawerError(error instanceof Error ? error.message : String(error))
      setTrackContext(null)
      setMemoryItems([])
    } finally {
      setLoadingDrawer(false)
    }
  }

  async function openDrawer(track: TrackTableRow) {
    setActiveTrack(track)
    setDrawerTab("saved")
    setDrawerOpen(true)
    await loadDrawer(track)
  }

  function toggleRow(id: number) {
    setSelectedIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]))
  }

  function toggleAllVisible() {
    if (allVisibleSelected) {
      setSelectedIds((current) => current.filter((id) => !filtered.some((row) => row.id === id)))
      return
    }
    const next = new Set(selectedIds)
    filtered.forEach((row) => next.add(row.id))
    setSelectedIds([...next])
  }

  const savedItems = trackContext?.saved_papers.recent_items || []
  const activeTrackName = activeTrack?.name || "—"
  const obsidianCommand = useMemo(
    () =>
      buildObsidianExportCommand({
        vaultPath: obsidianVaultPath,
        rootDir: obsidianRootDir,
        trackId: activeTrack?.id ?? null,
      }),
    [activeTrack?.id, obsidianRootDir, obsidianVaultPath],
  )
  const obsidianScope = useMemo(
    () => describeObsidianScope(activeTrack?.id ?? null, activeTrack?.name ?? null),
    [activeTrack?.id, activeTrack?.name],
  )

  async function handleExport(format: "bibtex" | "ris" | "markdown" | "csl_json") {
    if (!activeTrack) return
    setExportingFormat(format)
    setActionError(null)
    try {
      const qs = new URLSearchParams({ format, track_id: String(activeTrack.id) })
      const res = await fetch(`/api/papers/export?${qs.toString()}`, { cache: "no-store" })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      const blob = await res.blob()
      const extMap: Record<string, string> = { bibtex: "bib", ris: "ris", markdown: "md", csl_json: "csl.json" }
      const ext = extMap[format] || "txt"
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = `${activeTrack.name.replace(/\s+/g, "-").toLowerCase() || "track"}.${ext}`
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error))
    } finally {
      setExportingFormat(null)
    }
  }

  async function handleGenerateRelatedWork() {
    if (!activeTrack || !relatedWorkTopic.trim()) return
    setRelatedWorkLoading(true)
    setRelatedWorkError(null)
    setRelatedWorkMarkdown("")
    try {
      const res = await fetch("/api/research/papers/related-work", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          track_id: activeTrack.id,
          topic: relatedWorkTopic.trim(),
        }),
      })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      const data = (await res.json()) as { markdown?: string }
      setRelatedWorkMarkdown(data.markdown || "No output generated.")
    } catch (error) {
      setRelatedWorkError(error instanceof Error ? error.message : String(error))
    } finally {
      setRelatedWorkLoading(false)
    }
  }

  async function handleCopyRelatedWork() {
    if (!relatedWorkMarkdown) return
    try {
      await navigator.clipboard.writeText(relatedWorkMarkdown)
      setRelatedWorkCopied(true)
      setTimeout(() => setRelatedWorkCopied(false), 2000)
    } catch {
      setRelatedWorkError("Clipboard access failed. Copy the text manually.")
    }
  }

  async function handleCopyObsidianCommand() {
    try {
      await navigator.clipboard.writeText(obsidianCommand)
      setObsidianCopyError(null)
      setObsidianCopied(true)
      setTimeout(() => setObsidianCopied(false), 2000)
    } catch {
      setObsidianCopied(false)
      setObsidianCopyError("Clipboard access failed. Copy the command manually.")
      setTimeout(() => setObsidianCopyError(null), 3000)
    }
  }

  function baseFieldClass() {
    return "w-full rounded-[8px] border border-[#dbe3ec] bg-white px-3 py-2 text-[13px] text-[#0f172a] outline-none placeholder:text-[#94a3b8] focus:border-[#006ddd]"
  }

  return (
    <>
      <div className="mb-3 flex items-center gap-2">
        <Link
          href="/research"
          className="inline-flex h-[26px] items-center gap-1 rounded-[4px] border border-[#1566b8] bg-[#006ddd] px-2 text-[13px] text-white"
        >
          + Track
        </Link>
        <div className="inline-flex h-[26px] w-[260px] items-center gap-2 rounded-[4px] bg-[#edf2f7] px-2">
          <Search className="h-3.5 w-3.5 text-slate-900" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by name..."
            className="w-full bg-transparent text-[13px] outline-none placeholder:text-slate-500"
          />
        </div>
      </div>

      <div className="overflow-hidden rounded-[8px] border border-slate-200">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-slate-200 text-[13px] text-slate-500">
              <th className="w-10 px-4 py-3 text-left">
                <button
                  type="button"
                  onClick={toggleAllVisible}
                  className={[
                    "inline-block h-[18px] w-[18px] rounded border",
                    allVisibleSelected ? "border-[#1566b8] bg-[#006ddd]" : "border-slate-300 bg-white",
                  ].join(" ")}
                />
              </th>
              <th className="px-2 py-3 text-left">Name</th>
              <th className="px-2 py-3 text-center">Saved Papers</th>
              <th className="px-2 py-3 text-center">Feedback</th>
              <th className="px-2 py-3 text-center">Approved Memory</th>
              <th className="px-2 py-3 text-center">Feedback Coverage</th>
              <th className="px-2 py-3 text-center">Pending Memory</th>
              <th className="px-2 py-3 text-center">Description</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((track) => {
              const selected = selectedIds.includes(track.id)
              return (
                <tr key={track.id} className="border-t border-slate-100 text-[13px] hover:bg-[#fafbfc]">
                  <td className="px-4 py-3">
                    <button
                      type="button"
                      onClick={() => toggleRow(track.id)}
                      className={[
                        "inline-block h-[18px] w-[18px] rounded border",
                        selected ? "border-[#1566b8] bg-[#006ddd]" : "border-slate-300 bg-white",
                      ].join(" ")}
                    />
                  </td>
                  <td className="px-2 py-3 font-medium">
                    <button
                      type="button"
                      onClick={() => openDrawer(track)}
                      className="text-left text-[#0f172a] transition-colors hover:text-[#006ddd] hover:underline"
                    >
                      {track.name}
                    </button>
                  </td>
                  <td className="px-2 py-3 text-center">
                    <span className="inline-flex min-w-[18px] items-center justify-center rounded-[6px] bg-[#edf2f7] px-1.5 py-0.5">
                      {track.saved}
                    </span>
                  </td>
                  <td className="px-2 py-3 text-center">
                    <span className="inline-flex min-w-[18px] items-center justify-center rounded-[6px] bg-[#edf2f7] px-1.5 py-0.5">
                      {track.feedback}
                    </span>
                  </td>
                  <td className="px-2 py-3 text-center">
                    <span className="inline-flex min-w-[18px] items-center justify-center rounded-[6px] bg-[#edf2f7] px-1.5 py-0.5">
                      {track.approved}
                    </span>
                  </td>
                  <td className="px-2 py-3 text-center">
                    <span className="inline-flex min-w-[18px] items-center justify-center rounded-[6px] bg-[#edf2f7] px-1.5 py-0.5">
                      {track.coverage}
                    </span>
                  </td>
                  <td className="px-2 py-3 text-center">{track.pending}</td>
                  <td className="px-2 py-3 text-center text-slate-600">{track.desc}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {filtered.length === 0 ? (
          <div className="p-12 text-center text-sm text-slate-500">没有匹配的 Track</div>
        ) : null}
      </div>

      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent
          side="right"
          className="w-[480px] max-w-[calc(100vw-40px)] gap-0 border-l border-[#edf2f7] bg-white p-0 shadow-[-8px_0_24px_-8px_rgba(15,23,42,.12)] sm:max-w-[480px]"
        >
          <SheetHeader className="border-b border-[#edf2f7] px-5 py-[18px] text-left">
            <div className="flex items-start justify-between gap-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <SheetTitle className="text-[15px] font-semibold text-[#0f172a]">
                  Workspace Library
                </SheetTitle>
                <span className="text-[13px] text-[#64748b]">
                  Track: <span className="font-medium text-[#0f172a]">{activeTrackName}</span>
                </span>
              </div>
            </div>
            <SheetDescription className="mt-1 max-w-[95%] text-[12.5px] leading-[1.55] text-[#64748b]">
              Review saved papers, trigger export handoffs, and inspect track memory without leaving the research workspace.
            </SheetDescription>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span className="inline-flex h-[22px] items-center rounded-[6px] bg-[#f1f5f9] px-2 text-[12px] font-medium text-[#0f172a]">
                {activeTrackName}
              </span>
              <span className="inline-flex h-[22px] items-center rounded-[6px] border border-[#e2e8f0] bg-white px-2 text-[12px] font-medium text-[#0f172a]">
                Mode: Personalized
              </span>
              <span className="inline-flex h-[22px] items-center rounded-[6px] border border-[#e2e8f0] bg-white px-2 text-[12px] font-medium text-[#0f172a]">
                Query: No active query
              </span>
            </div>
          </SheetHeader>

          <div className="px-5 pb-4 pt-3">
            <div className="grid grid-cols-2 gap-1 rounded-[8px] bg-[#f1f5f9] p-1">
              <button
                type="button"
                onClick={() => setDrawerTab("saved")}
                className={[
                  "h-[30px] rounded-[6px] text-[13px] font-medium transition-colors",
                  drawerTab === "saved" ? "bg-white text-[#0f172a] shadow-[0_1px_2px_rgba(15,23,42,.06)]" : "text-[#64748b]",
                ].join(" ")}
              >
                Saved
              </button>
              <button
                type="button"
                onClick={() => setDrawerTab("memory")}
                className={[
                  "h-[30px] rounded-[6px] text-[13px] font-medium transition-colors",
                  drawerTab === "memory" ? "bg-white text-[#0f172a] shadow-[0_1px_2px_rgba(15,23,42,.06)]" : "text-[#64748b]",
                ].join(" ")}
              >
                Memory
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto px-5 pb-5">
            {loadingDrawer ? (
              <div className="rounded-[10px] border border-dashed border-[#e2e8f0] bg-[#fafbfc] px-4 py-10 text-center text-[13px] text-[#64748b]">
                Loading track details...
              </div>
            ) : drawerError ? (
              <div className="rounded-[10px] border border-dashed border-[#e2e8f0] bg-[#fafbfc] px-4 py-10 text-center text-[13px] text-[#64748b]">
                {drawerError}
              </div>
            ) : drawerTab === "saved" ? (
              <>
                <div className="mb-4 flex items-start justify-between gap-3 rounded-[10px] border border-[#edf2f7] bg-white px-[14px] py-3">
                  <div className="min-w-0 flex-1">
                    <span className="inline-flex h-[22px] items-center rounded-[6px] bg-[#f1f5f9] px-2 text-[12px] font-medium text-[#0f172a]">
                      Track: {activeTrackName}
                    </span>
                    <div className="mt-1.5 text-[12.5px] font-medium text-[#0f172a]">
                      {trackContext?.saved_papers.total_items || 0} saved
                    </div>
                    <div className="mt-0.5 text-[12px] text-[#64748b]">
                      Saved papers scoped to the current track.
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col gap-1.5">
                    <div className="flex gap-1.5">
                      <button
                        type="button"
                        onClick={() => setObsidianOpen(true)}
                        disabled={savedItems.length === 0}
                        className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#e2e8f0] bg-white px-2.5 text-[12px] font-medium text-[#0f172a] disabled:opacity-50"
                      >
                        <FolderSync className="h-3 w-3" />
                        Obsidian
                      </button>
                      <button
                        type="button"
                        onClick={() => setRelatedWorkOpen(true)}
                        disabled={savedItems.length === 0}
                        className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#e2e8f0] bg-white px-2.5 text-[12px] font-medium text-[#0f172a] disabled:opacity-50"
                      >
                        <FileText className="h-3 w-3" />
                        Related Work
                      </button>
                    </div>
                    <div className="flex gap-1.5">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            disabled={savedItems.length === 0 || exportingFormat !== null}
                            className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#e2e8f0] bg-white px-2.5 text-[12px] font-medium text-[#0f172a] disabled:opacity-50"
                          >
                            {exportingFormat ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />}
                            Export
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="min-w-[170px] rounded-[10px] border border-[#e5e7eb] p-1">
                          <DropdownMenuItem onClick={() => handleExport("bibtex")}>BibTeX</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => handleExport("ris")}>RIS</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => handleExport("markdown")}>Markdown</DropdownMenuItem>
                          <DropdownMenuItem onClick={() => handleExport("csl_json")}>Zotero (CSL-JSON)</DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                      <button
                        type="button"
                        onClick={() => activeTrack && loadDrawer(activeTrack)}
                        className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#e2e8f0] bg-white px-2.5 text-[12px] font-medium text-[#0f172a]"
                      >
                        <RefreshCw className="h-3 w-3" />
                        Refresh
                      </button>
                    </div>
                  </div>
                </div>
                {actionError ? <p className="mb-3 text-[12px] text-[#b42318]">{actionError}</p> : null}

                {savedItems.length > 0 ? (
                  <div className="space-y-2.5">
                    {savedItems.map((item, index) => {
                      const paper = item.paper
                      return (
                        <article key={`${paper?.id || index}`} className="rounded-[10px] border border-[#edf2f7] bg-white px-[14px] py-3">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <h4 className="text-[13.5px] font-semibold leading-[1.4] text-[#0f172a]">
                                <span className="mr-1 font-medium text-[#64748b]">#{index + 1}</span>
                                {paper?.title || "Untitled paper"}
                              </h4>
                              <div className="mt-1 text-[12px] leading-[1.5] text-[#64748b]">
                                {(paper?.authors || []).join(", ") || "Unknown authors"}
                                {paper?.venue ? <span className="mx-1.5">·</span> : null}
                                {paper?.venue || null}
                                {paper?.year ? <span className="mx-1.5">·</span> : null}
                                {paper?.year || null}
                                {typeof paper?.citation_count === "number" ? (
                                  <>
                                    <span className="mx-1.5">·</span>
                                    {paper.citation_count.toLocaleString()} citations
                                  </>
                                ) : null}
                              </div>
                              {paper?.abstract ? (
                                <p className="mt-2 line-clamp-3 text-[12.5px] leading-[1.55] text-[#0f172a]">
                                  {paper.abstract}
                                </p>
                              ) : null}
                              <div className="mt-2 flex items-center gap-2">
                                <button className="inline-flex items-center gap-1 text-[12px] text-[#64748b] transition-colors hover:text-[#0f172a]">
                                  Structured Card
                                </button>
                              </div>
                            </div>
                            {paper?.url ? (
                              <a
                                href={paper.url}
                                target="_blank"
                                rel="noreferrer"
                                className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-[5px] border border-[#e2e8f0] bg-white text-[#64748b] transition-colors hover:bg-[#f8fafc] hover:text-[#0f172a]"
                                aria-label="Open paper"
                              >
                                <ExternalLink className="h-3 w-3" />
                              </a>
                            ) : null}
                          </div>
                        </article>
                      )
                    })}
                  </div>
                ) : (
                  <div className="rounded-[10px] border border-dashed border-[#e2e8f0] bg-[#fafbfc] px-4 py-10 text-center text-[13px] text-[#64748b]">
                    No saved papers in this track yet.
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-[12.5px] text-[#64748b]">Memory inbox for current track.</p>
                  <button
                    type="button"
                    onClick={() => activeTrack && loadDrawer(activeTrack)}
                    className="inline-flex h-[26px] items-center gap-1 rounded-[6px] border border-[#e2e8f0] bg-white px-2.5 text-[12px] font-medium text-[#0f172a]"
                  >
                    <RefreshCw className="h-3 w-3" />
                    Refresh
                  </button>
                </div>

                {memoryItems.length > 0 ? (
                  <div className="space-y-2.5">
                    {memoryItems.map((item) => (
                      <article key={item.id} className="rounded-[10px] border border-[#edf2f7] bg-white px-[14px] py-3">
                        <div className="mb-1.5 flex items-center gap-2">
                          <span className="inline-flex h-5 items-center rounded-[5px] bg-[#f1f5f9] px-2 text-[11.5px] font-medium text-[#0f172a]">
                            {item.kind || "note"}
                          </span>
                          {item.created_at ? (
                            <span className="text-[11.5px] text-[#64748b]">{formatWhen(item.created_at)}</span>
                          ) : null}
                        </div>
                        <p className="text-[12.5px] leading-[1.55] text-[#0f172a]">{item.content || "(empty)"}</p>
                        {item.tags?.length ? (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {item.tags.map((tag) => (
                              <span
                                key={`${item.id}-${tag}`}
                                className="inline-flex h-[19px] items-center rounded-[5px] border border-[#e2e8f0] px-1.5 text-[11px] text-[#64748b]"
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        ) : null}
                      </article>
                    ))}
                  </div>
                ) : (
                  <div className="rounded-[10px] border border-dashed border-[#e2e8f0] bg-[#fafbfc] px-4 py-10 text-center text-[13px] text-[#64748b]">
                    No memory items for this track yet.
                  </div>
                )}
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <Dialog open={relatedWorkOpen} onOpenChange={setRelatedWorkOpen}>
        <DialogContent className="max-w-3xl border-[#e5e7eb] bg-[#fcfbf8] p-0 shadow-[0_28px_70px_-32px_rgba(15,23,42,.35)]">
          <div className="border-b border-[#ebe5d9] px-6 py-5">
            <DialogHeader className="gap-1 text-left">
              <DialogTitle className="text-[18px] font-semibold text-[#0f172a]">Generate Related Work</DialogTitle>
              <DialogDescription className="text-[13px] leading-6 text-[#64748b]">
                Generate a track-scoped draft from saved papers in {activeTrackName}.
              </DialogDescription>
            </DialogHeader>
          </div>
          <div className="space-y-4 px-6 py-5">
            <input
              value={relatedWorkTopic}
              onChange={(event) => setRelatedWorkTopic(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") handleGenerateRelatedWork().catch(() => {})
              }}
              placeholder="Enter a research topic..."
              className={baseFieldClass()}
            />
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => handleGenerateRelatedWork().catch(() => {})}
                disabled={relatedWorkLoading || !relatedWorkTopic.trim()}
                className="inline-flex h-[34px] items-center gap-2 rounded-[8px] bg-[#006ddd] px-4 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                {relatedWorkLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                Generate
              </button>
            </div>
            {relatedWorkError ? <p className="text-[12.5px] text-[#b42318]">{relatedWorkError}</p> : null}
            {relatedWorkMarkdown ? (
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <pre className="whitespace-pre-wrap break-words text-[12.5px] leading-6 text-[#0f172a]">{relatedWorkMarkdown}</pre>
              </div>
            ) : null}
          </div>
          {relatedWorkMarkdown ? (
            <DialogFooter className="border-t border-[#ebe5d9] px-6 py-4">
              <button
                type="button"
                onClick={() => handleCopyRelatedWork().catch(() => {})}
                className="inline-flex h-[32px] items-center gap-2 rounded-[8px] border border-[#dbe3ec] bg-white px-3 text-[13px] font-medium text-[#0f172a]"
              >
                <Copy className="h-3.5 w-3.5" />
                {relatedWorkCopied ? "Copied" : "Copy"}
              </button>
            </DialogFooter>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={obsidianOpen} onOpenChange={setObsidianOpen}>
        <DialogContent className="max-w-3xl border-[#e5e7eb] bg-[#fcfbf8] p-0 shadow-[0_28px_70px_-32px_rgba(15,23,42,.35)]">
          <div className="border-b border-[#ebe5d9] px-6 py-5">
            <DialogHeader className="gap-1 text-left">
              <DialogTitle className="text-[18px] font-semibold text-[#0f172a]">Export to Obsidian</DialogTitle>
              <DialogDescription className="text-[13px] leading-6 text-[#64748b]">
                Copy the CLI command and run it on the machine that owns your Obsidian vault.
              </DialogDescription>
            </DialogHeader>
          </div>
          <div className="space-y-4 px-6 py-5">
            <div className="rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3">
              <div className="text-[13px] font-medium text-[#0f172a]">{obsidianScope}</div>
              <p className="mt-1 text-[12px] text-[#64748b]">
                Markdown fallback remains available through Export if you only need a flat bundle.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <input
                value={obsidianVaultPath}
                onChange={(event) => setObsidianVaultPath(event.target.value)}
                placeholder="~/Obsidian/MyVault"
                className={baseFieldClass()}
              />
              <input
                value={obsidianRootDir}
                onChange={(event) => setObsidianRootDir(event.target.value)}
                placeholder="PaperBot"
                className={baseFieldClass()}
              />
            </div>
            <div className="rounded-[12px] bg-[#0f172a] p-4">
              <pre className="whitespace-pre-wrap break-all text-[12px] leading-6 text-white">{obsidianCommand}</pre>
            </div>
            {obsidianCopyError ? <p className="text-[12.5px] text-[#b42318]">{obsidianCopyError}</p> : null}
          </div>
          <DialogFooter className="border-t border-[#ebe5d9] px-6 py-4">
            <button
              type="button"
              onClick={() => handleCopyObsidianCommand().catch(() => {})}
              className="inline-flex h-[32px] items-center gap-2 rounded-[8px] border border-[#dbe3ec] bg-white px-3 text-[13px] font-medium text-[#0f172a]"
            >
              <Copy className="h-3.5 w-3.5" />
              {obsidianCopied ? "Copied" : "Copy Command"}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
