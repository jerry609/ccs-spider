"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useSession, signOut } from "next-auth/react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  LayoutDashboard,
  FileText,
  Code2,
  Settings,
  BookOpen,
  PanelLeftClose,
  PanelLeft,
  Rocket,
  NotebookPen,
  Bookmark,
  Search,
  User as UserIcon,
  LogOut,
  LogIn,
  ChevronUp,
} from "lucide-react"

type SidebarProps = React.HTMLAttributes<HTMLDivElement> & {
  collapsed?: boolean
  onToggle?: () => void
}

const routes = [
  { label: "仪表盘", icon: LayoutDashboard, href: "/dashboard" },
  { label: "Research", icon: Search, href: "/research" },
  { label: "论文库", icon: FileText, href: "/papers" },
  { label: "Tracks", icon: Bookmark, href: "/tracks" },
  { label: "DeepCode", icon: Code2, href: "/studio" },
  { label: "Wiki", icon: BookOpen, href: "/wiki" },
  { label: "设置", icon: Settings, href: "/settings" },
]

export function Sidebar({ className, collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname()
  const demoUrl = process.env.NEXT_PUBLIC_DEMO_URL
  const { data: session } = useSession()

  const isAuthenticated = !!session
  const displayName =
    session?.user?.name ||
    session?.user?.email ||
    String(session?.userId ?? "") ||
    "Guest"

  return (
    <div className={cn("flex min-h-screen flex-col pb-4", className)}>
      <div className="flex flex-1 flex-col px-2 py-4">
        <div className={cn("mb-4", collapsed ? "flex justify-center" : "px-2")}>
          <div className={cn(collapsed ? "flex items-center justify-center" : "grid grid-cols-[auto_1fr_auto] items-center gap-3")}>
            <Link
              href="/dashboard"
              className={cn(
                "flex items-center gap-3 rounded-2xl",
                collapsed ? "justify-center" : "min-w-0",
              )}
            >
              <span className="flex h-9 w-9 items-center justify-center rounded-2xl border border-sidebar-border bg-white text-[#0f5ea8] shadow-sm">
                <NotebookPen className="h-4 w-4" />
              </span>
              {!collapsed ? (
                <span className="min-w-0 truncate text-[17px] font-semibold tracking-tight text-[#0f5ea8]">
                  PaperBot
                </span>
              ) : null}
            </Link>

            {!collapsed ? (
              <Button
                variant="ghost"
                size="icon"
                className="ml-auto size-8 shrink-0 rounded-full border border-sidebar-border bg-white text-sidebar-foreground shadow-sm hover:bg-sidebar-accent"
                onClick={onToggle}
              >
                <PanelLeftClose className="size-4" />
              </Button>
            ) : null}
          </div>
        </div>

        {collapsed ? (
          <Button
            variant="ghost"
            size="icon"
            className="mb-3 ml-1 size-10 rounded-2xl border border-transparent text-sidebar-foreground hover:border-sidebar-border hover:bg-sidebar-accent"
            onClick={onToggle}
          >
            <PanelLeft className="size-4" />
          </Button>
        ) : null}

        <nav className="space-y-1">
          {routes.map((route) => {
            const isActive = pathname === route.href || pathname.startsWith(`${route.href}/`)
            return (
              <Link
                key={route.href}
                href={route.href}
                title={collapsed ? route.label : undefined}
                className={cn(
                  "group flex items-center rounded-2xl text-[13px] font-medium transition-colors",
                  collapsed
                    ? "mx-auto h-10 w-10 justify-center"
                    : "h-11 gap-3 px-3",
                  isActive
                    ? "bg-[#eeebe3] text-[#0f172a] shadow-sm"
                    : "text-[#6b7280] hover:bg-sidebar-accent/80 hover:text-[#0f172a]",
                )}
              >
                <route.icon className="h-4 w-4 shrink-0" />
                {!collapsed ? <span className="truncate">{route.label}</span> : null}
              </Link>
            )
          })}
        </nav>

        <div className="mt-auto space-y-2 pt-4">
          {demoUrl ? (
            <Button
              asChild
              variant="outline"
              className={cn(
                "h-10 rounded-2xl border-sidebar-border bg-white shadow-sm",
                collapsed ? "w-10 px-0" : "w-full justify-start",
              )}
              title={collapsed ? "Live Demo" : undefined}
            >
              <a href={demoUrl} target="_blank" rel="noreferrer">
                <Rocket className={cn("h-4 w-4", !collapsed && "mr-2")} />
                {!collapsed && "Live Demo"}
              </a>
            </Button>
          ) : null}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className={cn(
                "flex w-full items-center rounded-2xl border border-sidebar-border bg-[#1f2937] px-2 py-2 text-xs text-white shadow-sm transition-colors hover:bg-[#111827]",
                collapsed ? "h-10 justify-center px-0" : "justify-between",
              )}
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-white/10">
                  <UserIcon className="h-3.5 w-3.5" />
                </span>
                {!collapsed && (
                  <div className="flex min-w-0 flex-col text-left">
                    <span className="truncate font-medium">{displayName}</span>
                    <span className="text-[10px] text-white/60">
                      {isAuthenticated ? "Signed in" : "Guest mode"}
                    </span>
                  </div>
                )}
              </div>
              {!collapsed && <ChevronUp className="h-3 w-3 shrink-0 text-white/55" />}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="start" className="w-52">
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col space-y-0.5">
                <span className="text-sm font-medium">{displayName}</span>
                {isAuthenticated && (
                  <span className="text-xs text-muted-foreground truncate">
                    {session?.user?.email}
                  </span>
                )}
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/settings" className="cursor-pointer">
                <Settings className="mr-2 h-4 w-4" />
                Settings
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            {isAuthenticated ? (
              <DropdownMenuItem
                className="text-destructive focus:text-destructive cursor-pointer"
                onClick={() => signOut({ callbackUrl: "/login" })}
              >
                <LogOut className="mr-2 h-4 w-4" />
                Sign out
              </DropdownMenuItem>
            ) : (
              <DropdownMenuItem asChild>
                <Link href="/login" className="cursor-pointer">
                  <LogIn className="mr-2 h-4 w-4" />
                  Sign in
                </Link>
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
        </div>
      </div>
    </div>
  )
}
