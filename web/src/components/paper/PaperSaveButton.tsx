"use client"

import { useState } from "react"
import { Bookmark, Loader2 } from "lucide-react"

type PaperSaveButtonProps = {
  paperId: string
  title?: string
  className?: string
}

export function PaperSaveButton({ paperId, title, className }: PaperSaveButtonProps) {
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  async function handleSave() {
    if (saving || saved) return
    setSaving(true)
    try {
      const res = await fetch(`/api/papers/${encodeURIComponent(paperId)}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          metadata: {
            source: "paper_detail_page",
            title,
          },
        }),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(text || "Failed to save paper")
      }
      setSaved(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <button type="button" onClick={handleSave} disabled={saving || saved} className={className}>
      {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bookmark className="h-3.5 w-3.5" />}
      {saved ? "Saved" : "Save to Library"}
    </button>
  )
}
