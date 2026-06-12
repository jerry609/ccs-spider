"use client"

import { useEffect } from "react"
import { useParams, useRouter } from "next/navigation"
import { AgentBoard } from "@/components/studio/AgentBoard"
import { useStudioStore } from "@/lib/store/studio-store"

const AGENT_BOARD_FOCUS_BG = "#f3f3f2"

export default function AgentBoardFocusPage() {
  const router = useRouter()
  const params = useParams<{ paperId: string }>()
  const paperId = typeof params?.paperId === "string" ? params.paperId : null
  const loadPapers = useStudioStore((state) => state.loadPapers)
  const selectPaper = useStudioStore((state) => state.selectPaper)

  useEffect(() => {
    loadPapers()
    if (paperId) {
      selectPaper(paperId)
    }
  }, [loadPapers, paperId, selectPaper])

  return (
    <div className="h-screen min-h-0" style={{ background: AGENT_BOARD_FOCUS_BG }}>
      <AgentBoard
        paperId={paperId}
        focusMode
        onBack={() => {
          router.push(
            paperId
              ? `/studio/agent-board?paperId=${encodeURIComponent(paperId)}`
              : "/studio/agent-board",
          )
        }}
      />
    </div>
  )
}
