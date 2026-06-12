"use client"

import { useSyncExternalStore } from "react"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"
import { Sidebar } from "./Sidebar"

const SIDEBAR_STORAGE_KEY = "paperbot.sidebar.mode"
const SIDEBAR_EVENT = "paperbot-sidebar-mode"

function subscribe(callback: () => void) {
  window.addEventListener("storage", callback)
  window.addEventListener(SIDEBAR_EVENT, callback)
  return () => {
    window.removeEventListener("storage", callback)
    window.removeEventListener(SIDEBAR_EVENT, callback)
  }
}

function getSidebarSnapshot() {
  return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "narrow"
}

function getSidebarServerSnapshot() {
  return false
}

export function LayoutShell({ children }: { children: React.ReactNode }) {
  const collapsed = useSyncExternalStore(
    subscribe,
    getSidebarSnapshot,
    getSidebarServerSnapshot,
  )
  const pathname = usePathname()
  const isAgentBoardFocusPage =
    pathname === "/studio/agent-board" || pathname.startsWith("/studio/agent-board/")
  const isAuthPage =
    pathname === "/login" ||
    pathname === "/register" ||
    pathname === "/forgot-password" ||
    pathname === "/reset-password"
  const hideShellChrome = isAgentBoardFocusPage || isAuthPage

  const handleToggle = () => {
    const next = !collapsed
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, next ? "narrow" : "wide")
    window.dispatchEvent(new Event(SIDEBAR_EVENT))
  }

  return (
    <div className="min-h-screen">
      {!hideShellChrome && (
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-50 hidden border-r border-sidebar-border/80 bg-sidebar/95 backdrop-blur md:flex",
            "transition-[width] duration-200 ease-out",
            collapsed ? "w-14" : "w-56",
          )}
        >
          <Sidebar collapsed={collapsed} onToggle={handleToggle} />
        </aside>
      )}
      <main
        className={cn(
          "min-h-screen transition-[padding] duration-200 ease-out",
          !hideShellChrome && (collapsed ? "md:pl-14" : "md:pl-56"),
        )}
      >
        {children}
      </main>
    </div>
  )
}
