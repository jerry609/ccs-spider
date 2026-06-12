"use client"

import { useRouter } from "next/navigation"
import { Calendar } from "lucide-react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

type TrackOption = {
  id: number
  name: string
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
  if (args.sources && args.sources.join(",") !== "semantic_scholar,arxiv,openalex,papers_cool,hf_daily") {
    qp.set("sources", args.sources.join(","))
  }
  const qs = qp.toString()
  return qs ? `/research?${qs}` : "/research"
}

interface ResearchYearMenuProps {
  query: string
  trackId: number
  personalized: boolean
  yearFrom: string
  yearTo: string
  cap: number
  sources: string[]
}

export function ResearchYearMenu({
  query,
  trackId,
  personalized,
  yearFrom,
  yearTo,
  cap,
  sources,
}: ResearchYearMenuProps) {
  const router = useRouter()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 items-center gap-2 rounded-[6px] border border-[#e5e7eb] bg-white px-3 text-[13px] text-[#111827] outline-none"
        >
          <Calendar className="h-3.5 w-3.5 text-[#6b7280]" />
          {yearFrom ? `Since ${yearFrom}` : "Any time"}
          <span className="h-2.5 w-2.5 rotate-45 border-b-[1.3px] border-r-[1.3px] border-[#94a3b8]" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        sideOffset={6}
        className="min-w-[220px] rounded-[10px] border border-[#e5e7eb] bg-white p-1 shadow-[0_8px_24px_-8px_rgba(15,23,42,.12)]"
      >
        {[
          ["", "Any time"],
          ["2025", "Since 2025"],
          ["2023", "Since 2023"],
          ["2020", "Since 2020"],
        ].map(([value, label]) => (
          <DropdownMenuItem
            key={label}
            className="rounded-[6px] px-3 py-2 text-[13px] text-[#111827] focus:bg-[#f1f5f9] focus:text-[#111827]"
            onSelect={(event) => {
              event.preventDefault()
              router.push(
                buildResearchHref({
                  query,
                  trackId,
                  personalized,
                  yearFrom: value,
                  yearTo,
                  cap,
                  sources,
                }),
              )
            }}
          >
            {label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

interface ResearchTrackMenuProps {
  query: string
  tracks: TrackOption[]
  activeTrackName: string
  personalized: boolean
  yearFrom: string
  yearTo: string
  cap: number
  sources: string[]
}

export function ResearchTrackMenu({
  query,
  tracks,
  activeTrackName,
  personalized,
  yearFrom,
  yearTo,
  cap,
  sources,
}: ResearchTrackMenuProps) {
  const router = useRouter()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-8 items-center gap-2 rounded-[6px] px-2 text-[14px] font-medium text-[#6b7280] outline-none transition-colors hover:bg-[#f1f5f9] hover:text-[#111827]"
        >
          {activeTrackName}
          <span className="h-2.5 w-2.5 rotate-45 border-b-[1.3px] border-r-[1.3px] border-[#94a3b8]" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={6}
        className="min-w-[240px] rounded-[10px] border border-[#e5e7eb] bg-white p-1 shadow-[0_8px_24px_-8px_rgba(15,23,42,.12)]"
      >
        {tracks.map((track) => (
          <DropdownMenuItem
            key={track.id}
            className="rounded-[6px] px-3 py-2 text-[13px] text-[#111827] focus:bg-[#f1f5f9] focus:text-[#111827]"
            onSelect={(event) => {
              event.preventDefault()
              router.push(
                buildResearchHref({
                  query,
                  trackId: track.id,
                  personalized,
                  yearFrom,
                  yearTo,
                  cap,
                  sources,
                }),
              )
            }}
          >
            {track.name}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator className="my-1 bg-[#e5e7eb]" />
        <DropdownMenuItem
          className="rounded-[6px] px-3 py-2 text-[13px] text-[#6b7280] focus:bg-[#f1f5f9] focus:text-[#6b7280]"
          onSelect={(event) => {
            event.preventDefault()
            router.push("/tracks")
          }}
        >
          → Manage Tracks...
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
