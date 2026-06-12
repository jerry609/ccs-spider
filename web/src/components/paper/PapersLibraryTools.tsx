"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import { Bookmark, Copy, FileText, Loader2, Search } from "lucide-react"

import { getErrorMessage } from "@/lib/fetch"
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

type PaperOption = {
  id: number
  title: string
  authors?: string[]
}

type RelatedWorkResponse = {
  markdown?: string
}

type BibtexImportResponse = {
  parsed?: number
  imported?: number
  created?: number
  updated?: number
  skipped?: number
  track_name?: string
  errors?: string[]
}

type ZoteroPullResponse = {
  track_name?: string
  total_remote?: number
  imported?: number
  created?: number
  updated?: number
  skipped?: number
  errors?: string[]
}

type ZoteroPushResponse = {
  local_saved?: number
  remote_items?: number
  to_push?: number
  pushed?: number
  skipped?: number
  dry_run?: boolean
  errors?: string[]
}

type CollectionSummary = {
  id: number
  name: string
  description?: string
  item_count?: number
}

type CollectionItem = {
  id: number
  paper_id: string | number
  note?: string
  tags?: string[]
  paper?: {
    title?: string
    authors?: string[]
  }
}

type CollectionsResponse = {
  items?: CollectionSummary[]
}

type CollectionItemsResponse = {
  items?: CollectionItem[]
}

interface PapersLibraryToolsProps {
  trackId: number | null
  trackName?: string | null
  visiblePapers: PaperOption[]
  relatedPaperIds: string[]
}

function baseFieldClass() {
  return "w-full rounded-[8px] border border-[#dbe3ec] bg-white px-3 py-2 text-[13px] text-[#0f172a] outline-none placeholder:text-[#94a3b8] focus:border-[#006ddd]"
}

export function PapersLibraryTools({
  trackId,
  trackName = null,
  visiblePapers,
  relatedPaperIds,
}: PapersLibraryToolsProps) {
  const dropdownPanelClass =
    "z-[80] min-w-[220px] overflow-hidden rounded-[12px] border border-[#e5e7eb] bg-white p-1 shadow-[0_18px_36px_-12px_rgba(15,23,42,.22)]"

  const [rwOpen, setRwOpen] = useState(false)
  const [rwTopic, setRwTopic] = useState("")
  const [rwLoading, setRwLoading] = useState(false)
  const [rwMarkdown, setRwMarkdown] = useState("")
  const [rwError, setRwError] = useState<string | null>(null)
  const [rwCopied, setRwCopied] = useState(false)

  const [collectionsOpen, setCollectionsOpen] = useState(false)
  const [collectionsLoading, setCollectionsLoading] = useState(false)
  const [collectionsMessage, setCollectionsMessage] = useState<string | null>(null)
  const [collections, setCollections] = useState<CollectionSummary[]>([])
  const [selectedCollectionId, setSelectedCollectionId] = useState<number | null>(null)
  const [collectionItems, setCollectionItems] = useState<CollectionItem[]>([])
  const [newCollectionName, setNewCollectionName] = useState("")
  const [newCollectionDesc, setNewCollectionDesc] = useState("")

  const [importOpen, setImportOpen] = useState(false)
  const [importLoading, setImportLoading] = useState(false)
  const [importTrackName, setImportTrackName] = useState("")
  const [importBibtex, setImportBibtex] = useState("")
  const [importResult, setImportResult] = useState<string | null>(null)

  const [zoteroOpen, setZoteroOpen] = useState(false)
  const [zoteroLoading, setZoteroLoading] = useState(false)
  const [zoteroMode, setZoteroMode] = useState<"pull" | "push">("pull")
  const [zoteroLibraryType, setZoteroLibraryType] = useState<"user" | "group">("user")
  const [zoteroLibraryId, setZoteroLibraryId] = useState("")
  const [zoteroApiKey, setZoteroApiKey] = useState("")
  const [zoteroTrackName, setZoteroTrackName] = useState("")
  const [zoteroDryRun, setZoteroDryRun] = useState(true)
  const [zoteroResult, setZoteroResult] = useState<string | null>(null)

  const selectedCollection = useMemo(
    () => collections.find((item) => item.id === selectedCollectionId) || null,
    [collections, selectedCollectionId],
  )

  const loadCollections = useCallback(async () => {
    setCollectionsLoading(true)
    setCollectionsMessage(null)
    try {
      const qs = new URLSearchParams({ limit: "200" })
      if (trackId != null) qs.set("track_id", String(trackId))
      const res = await fetch(`/api/research/collections?${qs.toString()}`, { cache: "no-store" })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      const payload = ((await res.json()) as CollectionsResponse) || {}
      const next = payload.items || []
      setCollections(next)
      setSelectedCollectionId((current) => {
        if (current && next.some((item) => item.id === current)) return current
        return next[0]?.id ?? null
      })
    } catch (error) {
      setCollections([])
      setSelectedCollectionId(null)
      setCollectionItems([])
      setCollectionsMessage(`Failed to load collections: ${getErrorMessage(error)}`)
    } finally {
      setCollectionsLoading(false)
    }
  }, [trackId])

  const loadCollectionItems = useCallback(async (collectionId: number) => {
    setCollectionsLoading(true)
    setCollectionsMessage(null)
    try {
      const res = await fetch(`/api/research/collections/${collectionId}/items?limit=500`, { cache: "no-store" })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      const payload = ((await res.json()) as CollectionItemsResponse) || {}
      setCollectionItems(payload.items || [])
    } catch (error) {
      setCollectionItems([])
      setCollectionsMessage(`Failed to load collection items: ${getErrorMessage(error)}`)
    } finally {
      setCollectionsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!collectionsOpen) return
    loadCollections().catch(() => {})
  }, [collectionsOpen, loadCollections])

  useEffect(() => {
    if (!collectionsOpen || !selectedCollectionId) return
    loadCollectionItems(selectedCollectionId).catch(() => {})
  }, [collectionsOpen, loadCollectionItems, selectedCollectionId])

  async function handleGenerateRelatedWork() {
    if (!rwTopic.trim()) return
    setRwLoading(true)
    setRwError(null)
    setRwMarkdown("")
    try {
      const payload: { track_id?: number; topic: string; paper_ids?: string[]; limit: number } = {
        topic: rwTopic.trim(),
        limit: 20,
      }
      if (trackId != null) {
        payload.track_id = trackId
      } else if (relatedPaperIds.length > 0) {
        payload.paper_ids = relatedPaperIds
      }

      const res = await fetch("/api/research/papers/related-work", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      const data = (await res.json()) as RelatedWorkResponse
      setRwMarkdown(data.markdown || "No output generated.")
    } catch (error) {
      setRwError(getErrorMessage(error))
    } finally {
      setRwLoading(false)
    }
  }

  async function handleCopyRelatedWork() {
    if (!rwMarkdown) return
    try {
      await navigator.clipboard.writeText(rwMarkdown)
      setRwCopied(true)
      setTimeout(() => setRwCopied(false), 2000)
    } catch {
      setRwError("Clipboard access failed. Copy the text manually.")
    }
  }

  async function handleCreateCollection() {
    if (!newCollectionName.trim()) return
    setCollectionsLoading(true)
    setCollectionsMessage(null)
    try {
      const res = await fetch("/api/research/collections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newCollectionName.trim(),
          description: newCollectionDesc.trim(),
          track_id: trackId ?? undefined,
        }),
      })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      setNewCollectionName("")
      setNewCollectionDesc("")
      await loadCollections()
      setCollectionsMessage("Collection created.")
    } catch (error) {
      setCollectionsMessage(`Failed to create collection: ${getErrorMessage(error)}`)
    } finally {
      setCollectionsLoading(false)
    }
  }

  async function handleAddVisiblePapers() {
    if (!selectedCollectionId || visiblePapers.length === 0) return
    setCollectionsLoading(true)
    setCollectionsMessage(null)
    try {
      for (const paper of visiblePapers) {
        const res = await fetch(`/api/research/collections/${selectedCollectionId}/items`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paper_id: String(paper.id) }),
        })
        if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      }
      await loadCollections()
      await loadCollectionItems(selectedCollectionId)
      setCollectionsMessage(`Added ${visiblePapers.length} papers from the current page.`)
    } catch (error) {
      setCollectionsMessage(`Failed to add papers: ${getErrorMessage(error)}`)
    } finally {
      setCollectionsLoading(false)
    }
  }

  async function handleRemoveCollectionItem(paperId: string | number) {
    if (!selectedCollectionId) return
    setCollectionsLoading(true)
    setCollectionsMessage(null)
    try {
      const res = await fetch(
        `/api/research/collections/${selectedCollectionId}/items/${encodeURIComponent(String(paperId))}`,
        { method: "DELETE" },
      )
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      await loadCollections()
      await loadCollectionItems(selectedCollectionId)
      setCollectionsMessage("Paper removed from collection.")
    } catch (error) {
      setCollectionsMessage(`Failed to remove paper: ${getErrorMessage(error)}`)
    } finally {
      setCollectionsLoading(false)
    }
  }

  async function handleBibtexImport() {
    if (!importBibtex.trim()) return
    setImportLoading(true)
    setImportResult(null)
    try {
      const res = await fetch("/api/research/papers/import/bibtex", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: importBibtex.trim(),
          track_id: trackId ?? undefined,
          track_name: trackId == null ? importTrackName.trim() || undefined : undefined,
        }),
      })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      const data = (await res.json()) as BibtexImportResponse
      const summary = [
        `Track: ${data.track_name || trackName || "Imports"}`,
        `Parsed ${data.parsed || 0}`,
        `Imported ${data.imported || 0}`,
        `Created ${data.created || 0}`,
        `Updated ${data.updated || 0}`,
        `Skipped ${data.skipped || 0}`,
      ]
      if (data.errors?.length) summary.push(`Errors: ${data.errors.slice(0, 3).join(" | ")}`)
      setImportResult(summary.join(" · "))
    } catch (error) {
      setImportResult(getErrorMessage(error))
    } finally {
      setImportLoading(false)
    }
  }

  async function handleZoteroSync() {
    if (!zoteroLibraryId.trim() || !zoteroApiKey.trim()) return
    setZoteroLoading(true)
    setZoteroResult(null)
    try {
      const endpoint =
        zoteroMode === "pull"
          ? "/api/research/integrations/zotero/pull"
          : "/api/research/integrations/zotero/push"
      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          track_id: trackId ?? undefined,
          track_name: trackId == null ? zoteroTrackName.trim() || undefined : undefined,
          library_type: zoteroLibraryType,
          library_id: zoteroLibraryId.trim(),
          api_key: zoteroApiKey.trim(),
          dry_run: zoteroMode === "push" ? zoteroDryRun : undefined,
        }),
      })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))

      if (zoteroMode === "pull") {
        const data = (await res.json()) as ZoteroPullResponse
        const summary = [
          `Track: ${data.track_name || trackName || "Zotero Imports"}`,
          `Remote ${data.total_remote || 0}`,
          `Imported ${data.imported || 0}`,
          `Created ${data.created || 0}`,
          `Updated ${data.updated || 0}`,
          `Skipped ${data.skipped || 0}`,
        ]
        if (data.errors?.length) summary.push(`Errors: ${data.errors.slice(0, 3).join(" | ")}`)
        setZoteroResult(summary.join(" · "))
      } else {
        const data = (await res.json()) as ZoteroPushResponse
        const summary = [
          `Local ${data.local_saved || 0}`,
          `Remote ${data.remote_items || 0}`,
          `To push ${data.to_push || 0}`,
          `Pushed ${data.pushed || 0}`,
          `Skipped ${data.skipped || 0}`,
          data.dry_run ? "Dry run" : "Live push",
        ]
        if (data.errors?.length) summary.push(`Errors: ${data.errors.slice(0, 3).join(" | ")}`)
        setZoteroResult(summary.join(" · "))
      }
    } catch (error) {
      setZoteroResult(getErrorMessage(error))
    } finally {
      setZoteroLoading(false)
    }
  }

  return (
    <>
      <div className="relative z-[90]">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="inline-flex h-[32px] items-center gap-2 rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[13px] font-medium text-[#0f172a] shadow-sm outline-none">
              Tools
              <span className="h-3 w-3 -translate-y-0.5 rotate-45 border-b-[1.4px] border-r-[1.4px] border-[#94a3b8]" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" sideOffset={6} className={dropdownPanelClass}>
            <DropdownMenuItem
              className="h-[38px] rounded-[8px] px-3 text-[13px] text-[#0f172a]"
              onSelect={(event) => {
                event.preventDefault()
                setRwOpen(true)
              }}
            >
              <FileText className="h-3.5 w-3.5 text-[#64748b]" />
              Related Work
            </DropdownMenuItem>
            <DropdownMenuItem
              className="h-[38px] rounded-[8px] px-3 text-[13px] text-[#0f172a]"
              onSelect={(event) => {
                event.preventDefault()
                setCollectionsOpen(true)
              }}
            >
              <Bookmark className="h-3.5 w-3.5 text-[#64748b]" />
              Collections
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="h-[38px] rounded-[8px] px-3 text-[13px] text-[#0f172a]"
              onSelect={(event) => {
                event.preventDefault()
                setImportOpen(true)
              }}
            >
              <FileText className="h-3.5 w-3.5 text-[#64748b]" />
              Import BibTeX
            </DropdownMenuItem>
            <DropdownMenuItem
              className="h-[38px] rounded-[8px] px-3 text-[13px] text-[#0f172a]"
              onSelect={(event) => {
                event.preventDefault()
                setZoteroOpen(true)
              }}
            >
              <Search className="h-3.5 w-3.5 text-[#64748b]" />
              Zotero Sync
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <Dialog open={rwOpen} onOpenChange={setRwOpen}>
        <DialogContent className="max-w-3xl border-[#e5e7eb] bg-[#fcfbf8] p-0 shadow-[0_28px_70px_-32px_rgba(15,23,42,.35)]">
          <div className="border-b border-[#ebe5d9] px-6 py-5">
            <DialogHeader className="gap-1 text-left">
              <DialogTitle className="text-[18px] font-semibold text-[#0f172a]">Generate Related Work</DialogTitle>
              <DialogDescription className="text-[13px] leading-6 text-[#64748b]">
                Draft from your current library scope{trackName ? ` · ${trackName}` : ""}.
              </DialogDescription>
            </DialogHeader>
          </div>
          <div className="space-y-4 px-6 py-5">
            <input
              value={rwTopic}
              onChange={(event) => setRwTopic(event.target.value)}
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
                disabled={rwLoading || !rwTopic.trim()}
                className="inline-flex h-[34px] items-center gap-2 rounded-[8px] bg-[#006ddd] px-4 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                {rwLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                Generate
              </button>
            </div>
            {rwError ? <p className="text-[12.5px] text-[#b42318]">{rwError}</p> : null}
            {rwMarkdown ? (
              <div className="rounded-[12px] border border-[#e5e7eb] bg-white p-4">
                <pre className="whitespace-pre-wrap break-words text-[12.5px] leading-6 text-[#0f172a]">{rwMarkdown}</pre>
              </div>
            ) : null}
          </div>
          {rwMarkdown ? (
            <DialogFooter className="border-t border-[#ebe5d9] px-6 py-4">
              <button
                type="button"
                onClick={() => handleCopyRelatedWork().catch(() => {})}
                className="inline-flex h-[32px] items-center gap-2 rounded-[8px] border border-[#dbe3ec] bg-white px-3 text-[13px] font-medium text-[#0f172a]"
              >
                <Copy className="h-3.5 w-3.5" />
                {rwCopied ? "Copied" : "Copy"}
              </button>
            </DialogFooter>
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={collectionsOpen} onOpenChange={setCollectionsOpen}>
        <DialogContent className="max-w-5xl border-[#e5e7eb] bg-[#fcfbf8] p-0 shadow-[0_28px_70px_-32px_rgba(15,23,42,.35)]">
          <div className="border-b border-[#ebe5d9] px-6 py-5">
            <DialogHeader className="gap-1 text-left">
              <DialogTitle className="text-[18px] font-semibold text-[#0f172a]">Collections</DialogTitle>
              <DialogDescription className="text-[13px] leading-6 text-[#64748b]">
                Group saved papers from the current page and manage curated subsets.
              </DialogDescription>
            </DialogHeader>
          </div>
          <div className="grid gap-0 md:grid-cols-[280px_minmax(0,1fr)]">
            <div className="border-b border-[#ebe5d9] px-6 py-5 md:border-b-0 md:border-r">
              <div className="space-y-3">
                <input
                  value={newCollectionName}
                  onChange={(event) => setNewCollectionName(event.target.value)}
                  placeholder="Collection name"
                  className={baseFieldClass()}
                />
                <textarea
                  value={newCollectionDesc}
                  onChange={(event) => setNewCollectionDesc(event.target.value)}
                  placeholder="Description"
                  rows={3}
                  className={`${baseFieldClass()} resize-none`}
                />
                <button
                  type="button"
                  onClick={() => handleCreateCollection().catch(() => {})}
                  disabled={collectionsLoading || !newCollectionName.trim()}
                  className="inline-flex h-[34px] w-full items-center justify-center rounded-[8px] bg-[#006ddd] px-4 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Create Collection
                </button>
              </div>

              <div className="mt-5">
                <div className="mb-2 text-[12px] font-medium uppercase tracking-[0.08em] text-[#94a3b8]">Existing</div>
                <div className="space-y-2">
                  {collections.length === 0 ? (
                    <div className="rounded-[10px] border border-dashed border-[#dbe3ec] bg-white px-3 py-6 text-[12.5px] text-[#64748b]">
                      No collections yet.
                    </div>
                  ) : (
                    collections.map((collection) => (
                      <button
                        key={collection.id}
                        type="button"
                        onClick={() => setSelectedCollectionId(collection.id)}
                        className={[
                          "w-full rounded-[10px] border px-3 py-3 text-left transition-colors",
                          selectedCollectionId === collection.id
                            ? "border-[#bfd9f8] bg-[#f5faff]"
                            : "border-[#e5e7eb] bg-white hover:bg-[#fafaf9]",
                        ].join(" ")}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-[13px] font-medium text-[#0f172a]">{collection.name}</span>
                          <span className="text-[11px] text-[#64748b]">{collection.item_count || 0}</span>
                        </div>
                        {collection.description ? (
                          <p className="mt-1 text-[12px] leading-5 text-[#64748b]">{collection.description}</p>
                        ) : null}
                      </button>
                    ))
                  )}
                </div>
              </div>
            </div>

            <div className="px-6 py-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h4 className="text-[15px] font-semibold text-[#0f172a]">
                    {selectedCollection?.name || "Select a collection"}
                  </h4>
                  <p className="mt-1 text-[12.5px] text-[#64748b]">
                    {selectedCollection
                      ? `${selectedCollection.item_count ?? collectionItems.length} papers in this collection`
                      : "Create or select a collection to continue."}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleAddVisiblePapers().catch(() => {})}
                  disabled={!selectedCollectionId || visiblePapers.length === 0 || collectionsLoading}
                  className="inline-flex h-[34px] items-center rounded-[8px] border border-[#dbe3ec] bg-white px-3 text-[13px] font-medium text-[#0f172a] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Add Current Page ({visiblePapers.length})
                </button>
              </div>

              {collectionsMessage ? <p className="mt-3 text-[12.5px] text-[#64748b]">{collectionsMessage}</p> : null}

              <div className="mt-4 space-y-2">
                {!selectedCollectionId ? (
                  <div className="rounded-[10px] border border-dashed border-[#dbe3ec] bg-white px-4 py-10 text-center text-[13px] text-[#64748b]">
                    Choose a collection from the left.
                  </div>
                ) : collectionItems.length === 0 ? (
                  <div className="rounded-[10px] border border-dashed border-[#dbe3ec] bg-white px-4 py-10 text-center text-[13px] text-[#64748b]">
                    No items in this collection yet.
                  </div>
                ) : (
                  collectionItems.map((item) => (
                    <div key={`${item.id}-${item.paper_id}`} className="rounded-[10px] border border-[#e5e7eb] bg-white px-4 py-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-[13px] font-medium leading-5 text-[#0f172a]">
                            {item.paper?.title || `Paper #${item.paper_id}`}
                          </div>
                          {item.paper?.authors?.length ? (
                            <p className="mt-1 text-[12px] text-[#64748b]">{item.paper.authors.join(", ")}</p>
                          ) : null}
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRemoveCollectionItem(item.paper_id).catch(() => {})}
                          disabled={collectionsLoading}
                          className="text-[12px] font-medium text-[#b42318] disabled:opacity-60"
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="max-w-3xl border-[#e5e7eb] bg-[#fcfbf8] p-0 shadow-[0_28px_70px_-32px_rgba(15,23,42,.35)]">
          <div className="border-b border-[#ebe5d9] px-6 py-5">
            <DialogHeader className="gap-1 text-left">
              <DialogTitle className="text-[18px] font-semibold text-[#0f172a]">Import BibTeX</DialogTitle>
              <DialogDescription className="text-[13px] leading-6 text-[#64748b]">
                Paste BibTeX entries and save them into your papers library.
              </DialogDescription>
            </DialogHeader>
          </div>
          <div className="space-y-4 px-6 py-5">
            {trackId == null ? (
              <input
                value={importTrackName}
                onChange={(event) => setImportTrackName(event.target.value)}
                placeholder="Import track name (optional)"
                className={baseFieldClass()}
              />
            ) : null}
            <textarea
              value={importBibtex}
              onChange={(event) => setImportBibtex(event.target.value)}
              placeholder="@article{...}"
              rows={16}
              className={`${baseFieldClass()} resize-y font-mono text-[12px] leading-5`}
            />
            {importResult ? <p className="text-[12.5px] text-[#64748b]">{importResult}</p> : null}
          </div>
          <DialogFooter className="border-t border-[#ebe5d9] px-6 py-4">
            <button
              type="button"
              onClick={() => handleBibtexImport().catch(() => {})}
              disabled={importLoading || !importBibtex.trim()}
              className="inline-flex h-[34px] items-center gap-2 rounded-[8px] bg-[#006ddd] px-4 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {importLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Start Import
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={zoteroOpen} onOpenChange={setZoteroOpen}>
        <DialogContent className="max-w-3xl border-[#e5e7eb] bg-[#fcfbf8] p-0 shadow-[0_28px_70px_-32px_rgba(15,23,42,.35)]">
          <div className="border-b border-[#ebe5d9] px-6 py-5">
            <DialogHeader className="gap-1 text-left">
              <DialogTitle className="text-[18px] font-semibold text-[#0f172a]">Zotero Sync</DialogTitle>
              <DialogDescription className="text-[13px] leading-6 text-[#64748b]">
                Pull references from Zotero or push your saved library back.
              </DialogDescription>
            </DialogHeader>
          </div>
          <div className="space-y-4 px-6 py-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <select
                value={zoteroMode}
                onChange={(event) => setZoteroMode(event.target.value as "pull" | "push")}
                className={baseFieldClass()}
              >
                <option value="pull">Pull from Zotero</option>
                <option value="push">Push to Zotero</option>
              </select>
              <select
                value={zoteroLibraryType}
                onChange={(event) => setZoteroLibraryType(event.target.value as "user" | "group")}
                className={baseFieldClass()}
              >
                <option value="user">User Library</option>
                <option value="group">Group Library</option>
              </select>
            </div>
            <input
              value={zoteroLibraryId}
              onChange={(event) => setZoteroLibraryId(event.target.value)}
              placeholder="Library ID"
              className={baseFieldClass()}
            />
            <input
              value={zoteroApiKey}
              onChange={(event) => setZoteroApiKey(event.target.value)}
              placeholder="Zotero API Key"
              className={baseFieldClass()}
            />
            {trackId == null ? (
              <input
                value={zoteroTrackName}
                onChange={(event) => setZoteroTrackName(event.target.value)}
                placeholder="Import track name (optional)"
                className={baseFieldClass()}
              />
            ) : null}
            {zoteroMode === "push" ? (
              <label className="flex items-center gap-2 text-[13px] text-[#0f172a]">
                <input
                  type="checkbox"
                  checked={zoteroDryRun}
                  onChange={(event) => setZoteroDryRun(event.target.checked)}
                />
                Dry run only
              </label>
            ) : null}
            {zoteroResult ? <p className="text-[12.5px] text-[#64748b]">{zoteroResult}</p> : null}
          </div>
          <DialogFooter className="border-t border-[#ebe5d9] px-6 py-4">
            <button
              type="button"
              onClick={() => handleZoteroSync().catch(() => {})}
              disabled={zoteroLoading || !zoteroLibraryId.trim() || !zoteroApiKey.trim()}
              className="inline-flex h-[34px] items-center gap-2 rounded-[8px] bg-[#006ddd] px-4 text-[13px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {zoteroLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              {zoteroMode === "pull" ? "Start Pull" : "Start Push"}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
