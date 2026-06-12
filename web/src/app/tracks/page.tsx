import Link from "next/link"
import { redirect } from "next/navigation"

import { auth } from "@/auth"
import { TracksTableClient } from "@/components/tracks/TracksTableClient"
import { fetchDashboardTrackContext, fetchDashboardTracks } from "@/lib/dashboard-api"

type SearchParams = Promise<{ q?: string | string[] }>

function asText(value: string | string[] | undefined): string {
  const raw = Array.isArray(value) ? value[0] : value
  return (raw || "").trim().toLowerCase()
}

function asPercent(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(numeric)) return "0%"
  return `${Math.round(Math.max(0, Math.min(1, numeric)) * 100)}%`
}

export default async function TracksPage({ searchParams }: { searchParams: SearchParams }) {
  const session = await auth()
  if (!session) {
    redirect("/login?callbackUrl=/tracks")
  }

  const params = await searchParams
  const query = asText(params.q)
  const tracks = await fetchDashboardTracks(session.accessToken)
  const items = await Promise.all(
    tracks.map(async (track) => {
      const context = await fetchDashboardTrackContext(track.id, session.accessToken).catch(() => null)
      return {
        id: track.id,
        name: track.name,
        saved: Number(context?.saved_papers.total_items || 0),
        feedback: Number(context?.feedback.total_items || 0),
        approved: Number(context?.memory.approved_items || 0),
        coverage: asPercent(context?.eval_summary?.feedback_coverage),
        pending: Number(context?.memory.pending_items || 0),
        desc: track.description || "",
      }
    }),
  )

  return (
    <div className="min-h-screen bg-transparent">
      <main className="w-full px-6 py-5">
        <nav className="mb-3 flex items-center gap-2 text-[12px] text-slate-500">
          <Link href="/dashboard" className="hover:text-slate-900">Dashboard</Link>
          <span>/</span>
          <span className="text-slate-900">Tracks</span>
        </nav>
        <TracksTableClient initialRows={items} initialQuery={query} />
      </main>
    </div>
  )
}
