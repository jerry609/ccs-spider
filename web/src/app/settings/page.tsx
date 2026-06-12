"use client"

import Link from "next/link"
import { Suspense, useCallback, useEffect, useMemo, useState } from "react"
import {
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Mail,
  Megaphone,
  Pencil,
  Plus,
  RefreshCcw,
  Send,
  Trash2,
  Wrench,
} from "lucide-react"
import { useSession, signOut } from "next-auth/react"
import { useSearchParams } from "next/navigation"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { fetchJson, getErrorMessage } from "@/lib/fetch"
import { useWorkflowStore } from "@/lib/stores/workflow-store"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

import { EmbeddingSettingsPanel } from "@/components/settings/EmbeddingSettingsPanel"

// ─── Types ────────────────────────────────────────────────────────────────────

type ModelEndpoint = {
  id: number
  name: string
  vendor: string
  base_url?: string | null
  api_key_env: string
  api_key?: string
  models: string[]
  task_types: string[]
  enabled: boolean
  is_default: boolean
  api_key_present?: boolean
  key_source?: string
}

type FormState = {
  id?: number
  name: string
  vendor: string
  base_url: string
  api_key_env: string
  api_key: string
  models: string
  task_types: string
  enabled: boolean
  is_default: boolean
}

type Preset = {
  label: string
  name: string
  vendor: string
  base_url: string
  api_key_env: string
  models: string[]
  task_types: string[]
}

// ─── Constants ────────────────────────────────────────────────────────────────

const QUICK_PRESETS: Preset[] = [
  {
    label: "OpenAI",
    name: "OpenAI",
    vendor: "openai",
    base_url: "https://api.openai.com/v1",
    api_key_env: "OPENAI_API_KEY",
    models: ["gpt-4o-mini"],
    task_types: ["default", "summary", "chat"],
  },
  {
    label: "Anthropic",
    name: "Anthropic",
    vendor: "anthropic",
    base_url: "",
    api_key_env: "ANTHROPIC_API_KEY",
    models: ["claude-3-5-sonnet-20241022"],
    task_types: ["reasoning", "analysis"],
  },
  {
    label: "OpenRouter",
    name: "OpenRouter",
    vendor: "openai_compatible",
    base_url: "https://openrouter.ai/api/v1",
    api_key_env: "OPENROUTER_API_KEY",
    models: ["openai/gpt-4o-mini"],
    task_types: ["reasoning", "review"],
  },
  {
    label: "Ollama",
    name: "Local Ollama",
    vendor: "ollama",
    base_url: "http://localhost:11434",
    api_key_env: "OLLAMA_API_KEY",
    models: ["llama3.1"],
    task_types: ["default", "chat"],
  },
]

const EMPTY_FORM: FormState = {
  name: "",
  vendor: "openai_compatible",
  base_url: "",
  api_key_env: "OPENAI_API_KEY",
  api_key: "",
  models: "",
  task_types: "",
  enabled: true,
  is_default: false,
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function toPayload(form: FormState) {
  return {
    name: form.name.trim(),
    vendor: form.vendor,
    base_url: form.base_url.trim() || null,
    api_key_env: form.api_key_env.trim(),
    api_key: form.api_key,
    models: form.models.split(",").map((x) => x.trim()).filter(Boolean),
    task_types: form.task_types.split(",").map((x) => x.trim()).filter(Boolean),
    enabled: form.enabled,
    is_default: form.is_default,
  }
}

function maskKey(key?: string): string {
  if (!key) return ""
  if (key.length <= 8) return "****"
  return "****" + key.slice(-4)
}

function statusDot(item: ModelEndpoint) {
  if (item.is_default) return "bg-green-500"
  if (!item.api_key_present) return "bg-red-500"
  return "bg-gray-400"
}

type SubscriberCounts = {
  active: number
  total: number
}

// ─── Account Section ──────────────────────────────────────────────────────────

function AccountSection() {
  const { data: session, update: updateSession } = useSession()

  const isOAuth = session?.provider === "github"

  const [displayName, setDisplayName] = useState("")
  const [nameLoading, setNameLoading] = useState(false)
  const [nameMsg, setNameMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null)

  const [currentPw, setCurrentPw] = useState("")
  const [newPw, setNewPw] = useState("")
  const [confirmPw, setConfirmPw] = useState("")
  const [showCurrentPw, setShowCurrentPw] = useState(false)
  const [showNewPw, setShowNewPw] = useState(false)
  const [pwLoading, setPwLoading] = useState(false)
  const [pwMsg, setPwMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null)

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleteConfirmText, setDeleteConfirmText] = useState("")
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteErr, setDeleteErr] = useState<string | null>(null)

  useEffect(() => {
    setDisplayName(session?.user?.name || "")
  }, [session])

  async function saveName() {
    setNameLoading(true)
    setNameMsg(null)
    try {
      const data = await fetchJson<{ display_name?: string }>("/api/auth/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: displayName.trim() || null }),
      })
      await updateSession({ name: data.display_name ?? displayName.trim() })
      setNameMsg({ type: "ok", text: "Display name updated." })
    } catch (e) {
      setNameMsg({ type: "err", text: getErrorMessage(e) })
    } finally {
      setNameLoading(false)
    }
  }

  async function changePassword() {
    if (newPw !== confirmPw) {
      setPwMsg({ type: "err", text: "New passwords do not match." })
      return
    }
    setPwLoading(true)
    setPwMsg(null)
    try {
      await fetchJson("/api/auth/me/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPw, new_password: newPw }),
      })
      setCurrentPw("")
      setNewPw("")
      setConfirmPw("")
      setPwMsg({ type: "ok", text: "Password updated successfully." })
    } catch (e) {
      setPwMsg({ type: "err", text: getErrorMessage(e) })
    } finally {
      setPwLoading(false)
    }
  }

  async function deleteAccount() {
    setDeleteLoading(true)
    setDeleteErr(null)
    try {
      const res = await fetch("/api/auth/me", { method: "DELETE" })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        throw new Error(data?.detail || `${res.status}`)
      }
      await signOut({ callbackUrl: "/login" })
    } catch (e) {
      setDeleteErr(getErrorMessage(e))
      setDeleteLoading(false)
    }
  }

  const email = session?.user?.email ?? undefined

  return (
    <div className="space-y-4">
      {/* Profile */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Profile</CardTitle>
          <CardDescription>Update your display name shown across the app.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {email && (
            <div className="space-y-1.5">
              <Label>Email</Label>
              <Input value={email} disabled className="text-muted-foreground" />
            </div>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="display-name">Display name</Label>
            <div className="flex gap-2">
              <Input
                id="display-name"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your name"
                disabled={nameLoading}
              />
              <Button onClick={saveName} disabled={nameLoading} variant="outline">
                {nameLoading
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Pencil className="h-4 w-4 mr-1" />}
                Save
              </Button>
            </div>
            {nameMsg && (
              <p className={`text-xs ${nameMsg.type === "ok" ? "text-green-600" : "text-destructive"}`}>
                {nameMsg.text}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Security */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Security</CardTitle>
          <CardDescription>
            {isOAuth
              ? "Your account is managed via GitHub. Password sign-in is not available."
              : "Change your password. You'll stay signed in on this device."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isOAuth ? (
            <p className="text-sm text-muted-foreground">Signed in with GitHub — no password to manage.</p>
          ) : (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="current-pw">Current password</Label>
                <div className="relative">
                  <Input
                    id="current-pw"
                    type={showCurrentPw ? "text" : "password"}
                    value={currentPw}
                    onChange={(e) => setCurrentPw(e.target.value)}
                    disabled={pwLoading}
                    className="pr-9"
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    onClick={() => setShowCurrentPw((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {showCurrentPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="new-pw">New password</Label>
                <div className="relative">
                  <Input
                    id="new-pw"
                    type={showNewPw ? "text" : "password"}
                    value={newPw}
                    onChange={(e) => setNewPw(e.target.value)}
                    disabled={pwLoading}
                    minLength={8}
                    placeholder="Min. 8 characters"
                    className="pr-9"
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    onClick={() => setShowNewPw((v) => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  >
                    {showNewPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="confirm-pw">Confirm new password</Label>
                <Input
                  id="confirm-pw"
                  type="password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                  disabled={pwLoading}
                  className={confirmPw && confirmPw !== newPw ? "border-destructive" : ""}
                />
                {confirmPw && confirmPw !== newPw && (
                  <p className="text-xs text-destructive">Passwords do not match.</p>
                )}
              </div>

              {pwMsg && (
                <p className={`text-sm ${pwMsg.type === "ok" ? "text-green-600" : "text-destructive"}`}>
                  {pwMsg.text}
                </p>
              )}

              <Button
                onClick={changePassword}
                disabled={pwLoading || !currentPw || !newPw || newPw !== confirmPw}
              >
                {pwLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                Update password
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Danger Zone */}
      <Card className="border-destructive/40">
        <CardHeader>
          <CardTitle className="text-base text-destructive">Danger zone</CardTitle>
          <CardDescription>These actions are irreversible. Please proceed with caution.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between rounded-md border px-4 py-3">
            <div>
              <p className="text-sm font-medium">Sign out</p>
              <p className="text-xs text-muted-foreground">End your current session.</p>
            </div>
            <Button variant="outline" onClick={() => signOut({ callbackUrl: "/login" })}>
              Sign out
            </Button>
          </div>

          <div className="flex items-center justify-between rounded-md border border-destructive/30 px-4 py-3">
            <div>
              <p className="text-sm font-medium">Delete account</p>
              <p className="text-xs text-muted-foreground">
                Permanently deactivate your account. Your data will no longer be accessible.
              </p>
            </div>
            <Button variant="destructive" onClick={() => setDeleteDialogOpen(true)}>
              Delete account
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Delete confirmation dialog */}
      <Dialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete your account?</DialogTitle>
            <DialogDescription>
              This will permanently deactivate your account. This action cannot be undone.
              Type <span className="font-mono font-semibold">delete my account</span> to confirm.
            </DialogDescription>
          </DialogHeader>
          <Input
            value={deleteConfirmText}
            onChange={(e) => setDeleteConfirmText(e.target.value)}
            placeholder="delete my account"
            disabled={deleteLoading}
          />
          {deleteErr && <p className="text-sm text-destructive">{deleteErr}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteDialogOpen(false)} disabled={deleteLoading}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteConfirmText !== "delete my account" || deleteLoading}
              onClick={deleteAccount}
            >
              {deleteLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              Delete account
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function DailyBriefSection() {
  const notifyEmail = useWorkflowStore((state) => state.notifyEmail)
  const notifyEnabled = useWorkflowStore((state) => state.notifyEnabled)
  const resendEnabled = useWorkflowStore((state) => state.resendEnabled)
  const config = useWorkflowStore((state) => state.config)
  const setNotifyEmail = useWorkflowStore((state) => state.setNotifyEmail)
  const setNotifyEnabled = useWorkflowStore((state) => state.setNotifyEnabled)
  const setResendEnabled = useWorkflowStore((state) => state.setResendEnabled)
  const updateConfig = useWorkflowStore((state) => state.updateConfig)

  const [subscriberCounts, setSubscriberCounts] = useState<SubscriberCounts>({ active: 0, total: 0 })
  const [subscriberEmail, setSubscriberEmail] = useState("")
  const [loadingSubscribers, setLoadingSubscribers] = useState(false)
  const [submittingSubscriber, setSubmittingSubscriber] = useState(false)
  const [subscriberMessage, setSubscriberMessage] = useState<{
    type: "ok" | "err"
    text: string
  } | null>(null)

  const loadSubscribers = useCallback(async () => {
    setLoadingSubscribers(true)
    try {
      const response = await fetch("/api/newsletter/subscribers", { cache: "no-store" })
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
      const payload = (await response.json()) as Partial<SubscriberCounts>
      setSubscriberCounts({
        active: Number(payload.active || 0),
        total: Number(payload.total || 0),
      })
    } catch {
      setSubscriberCounts({ active: 0, total: 0 })
    } finally {
      setLoadingSubscribers(false)
    }
  }, [])

  useEffect(() => {
    loadSubscribers().catch(() => {})
  }, [loadSubscribers])

  async function handleAddSubscriber() {
    if (!subscriberEmail.trim()) return

    setSubmittingSubscriber(true)
    setSubscriberMessage(null)
    try {
      const response = await fetch("/api/newsletter/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: subscriberEmail.trim() }),
      })

      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(String((payload as { detail?: string }).detail || response.status))
      }

      setSubscriberEmail("")
      setSubscriberMessage({ type: "ok", text: "Subscriber added to Daily Brief delivery." })
      await loadSubscribers()
    } catch (error) {
      setSubscriberMessage({ type: "err", text: getErrorMessage(error) })
    } finally {
      setSubmittingSubscriber(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Daily Brief Defaults</CardTitle>
          <CardDescription>
            These defaults are stored in this browser and picked up by DailyPaper runs.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded-xl border px-4 py-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
                    <Mail className="h-4 w-4 text-slate-600" />
                    Email override
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Send the next digest to a specific address instead of just relying on the runtime input.
                  </p>
                </div>
                <Switch checked={notifyEnabled} onCheckedChange={setNotifyEnabled} />
              </div>
              {notifyEnabled ? (
                <div className="mt-3 space-y-1.5">
                  <Label htmlFor="daily-brief-notify-email">Recipient</Label>
                  <Input
                    id="daily-brief-notify-email"
                    value={notifyEmail}
                    onChange={(event) => setNotifyEmail(event.target.value)}
                    placeholder="reviewer@example.com"
                  />
                </div>
              ) : null}
            </div>

            <div className="rounded-xl border px-4 py-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
                    <Megaphone className="h-4 w-4 text-slate-600" />
                    Newsletter broadcast
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Push the digest to active subscribers through the configured backend delivery channel.
                  </p>
                </div>
                <Switch checked={resendEnabled} onCheckedChange={setResendEnabled} />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="rounded-full">
                  {loadingSubscribers ? "Checking subscribers..." : `${subscriberCounts.active} active subscribers`}
                </Badge>
                <Badge variant="secondary" className="rounded-full">
                  {subscriberCounts.total} total captured
                </Badge>
              </div>
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
            <div className="rounded-xl border px-4 py-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-slate-900">Artifact persistence</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Keep DailyPaper files on disk by default instead of generating preview-only output.
                  </p>
                </div>
                <Switch
                  checked={config.saveDaily}
                  onCheckedChange={(checked) => updateConfig({ saveDaily: checked })}
                />
              </div>
              <div className="mt-3 space-y-1.5">
                <Label htmlFor="daily-brief-output-dir">Output directory</Label>
                <Input
                  id="daily-brief-output-dir"
                  value={config.outputDir}
                  onChange={(event) => updateConfig({ outputDir: event.target.value })}
                  placeholder="./reports/dailypaper"
                />
              </div>
            </div>

            <div className="rounded-xl border bg-slate-50/70 px-4 py-4">
              <p className="text-sm font-medium text-slate-900">Where the rest lives</p>
              <p className="mt-2 text-sm leading-6 text-slate-600">
                Search, DailyPaper generation, judge, and final delivery still run from the Research workbench.
                Settings now owns the reusable delivery defaults and subscriber management.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button asChild size="sm" className="rounded-full bg-slate-900 hover:bg-slate-800">
                  <Link href="/research">
                    Open Research
                    <ArrowRight className="h-4 w-4" />
                  </Link>
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="rounded-full"
                  onClick={() => loadSubscribers().catch(() => {})}
                  disabled={loadingSubscribers}
                >
                  {loadingSubscribers ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
                  Refresh counts
                </Button>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Newsletter Subscribers</CardTitle>
          <CardDescription>
            Add recipients for Daily Brief broadcasts. Unsubscribe links are still issued from the backend delivery flow.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="rounded-full">
              {subscriberCounts.active} active
            </Badge>
            <Badge variant="secondary" className="rounded-full">
              {subscriberCounts.total} total
            </Badge>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={subscriberEmail}
              onChange={(event) => setSubscriberEmail(event.target.value)}
              placeholder="subscriber@example.com"
              type="email"
            />
            <Button
              type="button"
              onClick={handleAddSubscriber}
              disabled={submittingSubscriber || !subscriberEmail.trim()}
            >
              {submittingSubscriber ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              Add subscriber
            </Button>
          </div>

          {subscriberMessage ? (
            <p className={`text-sm ${subscriberMessage.type === "ok" ? "text-green-600" : "text-destructive"}`}>
              {subscriberMessage.text}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  )
}

// ─── Model Providers Section ──────────────────────────────────────────────────

function ModelProvidersSection() {
  const [items, setItems] = useState<ModelEndpoint[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const editing = useMemo(() => typeof form.id === "number", [form.id])

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch("/api/model-endpoints")
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const payload = await res.json()
      setItems(payload.items || [])
    } catch (e) {
      setError(getErrorMessage(e))
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load().catch(() => {}) }, [])

  function openAdd(preset?: Preset) {
    const base = { ...EMPTY_FORM }
    if (preset) {
      base.name = preset.name
      base.vendor = preset.vendor
      base.base_url = preset.base_url
      base.api_key_env = preset.api_key_env
      base.models = preset.models.join(", ")
      base.task_types = preset.task_types.join(", ")
    }
    setForm(base)
    setError(null)
    setMessage(null)
    setDialogOpen(true)
  }

  function openEdit(item: ModelEndpoint) {
    setForm({
      id: item.id,
      name: item.name,
      vendor: item.vendor,
      base_url: item.base_url || "",
      api_key_env: item.api_key_env,
      api_key: "",
      models: (item.models || []).join(", "),
      task_types: (item.task_types || []).join(", "),
      enabled: item.enabled,
      is_default: item.is_default,
    })
    setError(null)
    setMessage(null)
    setDialogOpen(true)
  }

  async function saveItem() {
    setSaving(true)
    setError(null)
    setMessage(null)
    try {
      const payload = toPayload(form)
      if (!payload.name) throw new Error("Name is required")
      if (!payload.models.length) throw new Error("At least one model is required")
      const res = await fetch(editing ? `/api/model-endpoints/${form.id}` : "/api/model-endpoints", {
        method: editing ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const text = await res.text().catch(() => "")
        throw new Error(text || `${res.status} ${res.statusText}`)
      }
      await load()
      setDialogOpen(false)
      setMessage(editing ? "Provider updated." : "Provider created.")
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setSaving(false)
    }
  }

  async function deleteItem(id: number) {
    if (!confirm("Delete this provider?")) return
    setError(null)
    setMessage(null)
    try {
      const res = await fetch(`/api/model-endpoints/${id}`, { method: "DELETE" })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      await load()
      setMessage("Provider removed.")
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  async function activateItem(id: number) {
    setError(null)
    setMessage(null)
    try {
      const res = await fetch(`/api/model-endpoints/${id}/activate`, { method: "POST" })
      if (!res.ok) throw new Error(await res.text().catch(() => `${res.status}`))
      await load()
      setMessage("Provider activated.")
    } catch (e) {
      setError(getErrorMessage(e))
    }
  }

  async function testItem(id: number) {
    setTestingId(id)
    setError(null)
    setMessage(null)
    try {
      const res = await fetch(`/api/model-endpoints/${id}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ remote: false }),
      })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(String(payload?.detail || `${res.status}`))
      setMessage(payload?.message || "Connection test passed.")
    } catch (e) {
      setError(getErrorMessage(e))
    } finally {
      setTestingId(null)
    }
  }

  return (
    <>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {message && <p className="text-sm text-green-600">{message}</p>}

      <div className="space-y-2">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading providers...</p>
        ) : !items.length ? (
          <p className="text-sm text-muted-foreground">No providers configured yet.</p>
        ) : (
          items.map((item) => (
            <Card
              key={item.id}
              className={`relative overflow-hidden ${item.is_default ? "border-l-4 border-l-green-500" : ""}`}
            >
              <CardContent className="p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className={`h-2.5 w-2.5 rounded-full shrink-0 ${statusDot(item)}`} />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-sm">{item.name}</span>
                        {item.is_default && <Badge className="text-xs">Default</Badge>}
                      </div>
                      <p className="text-xs text-muted-foreground truncate">
                        {(item.models || []).join(", ") || "no models"} · {item.base_url || "(default URL)"}
                      </p>
                      <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                        <span className="text-xs text-muted-foreground">
                          Key: {item.api_key_present ? maskKey(item.api_key || "present") : "missing"}
                        </span>
                        {item.key_source === "keychain" && (
                          <span className="inline-flex items-center gap-0.5 text-xs text-muted-foreground">
                            <KeyRound className="h-3 w-3" /> Keychain
                          </span>
                        )}
                        {(item.task_types || []).length > 0 && (
                          <span className="text-xs text-muted-foreground">
                            Tasks: {item.task_types.join(", ")}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    <Button size="sm" variant="ghost" onClick={() => testItem(item.id)} disabled={testingId === item.id} title="Test">
                      {testingId === item.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => openEdit(item)} title="Edit">
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    {!item.is_default && (
                      <Button size="sm" variant="ghost" onClick={() => activateItem(item.id)} title="Set default">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    <Button size="sm" variant="ghost" onClick={() => deleteItem(item.id)} title="Delete" className="text-destructive hover:text-destructive">
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>

      <div className="space-y-3">
        <Button onClick={() => openAdd()} variant="outline">
          <Plus className="h-4 w-4 mr-1.5" /> Add Provider
        </Button>
        <div>
          <p className="text-xs text-muted-foreground mb-1.5">Quick Presets</p>
          <div className="flex flex-wrap gap-1.5">
            {QUICK_PRESETS.map((preset) => (
              <Button key={preset.label} variant="secondary" size="sm" onClick={() => openAdd(preset)}>
                {preset.label}
              </Button>
            ))}
          </div>
        </div>
      </div>

      <div className="pt-4 border-t">
        <EmbeddingSettingsPanel />
      </div>



      {/* Add/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit Provider" : "Add Provider"}</DialogTitle>
            <DialogDescription>
              {editing ? "Update provider configuration." : "Configure a new LLM provider."}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-sm font-medium">Name</label>
                <Input value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))} placeholder="My Provider" />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium">Vendor</label>
                <select
                  value={form.vendor}
                  onChange={(e) => setForm((p) => ({ ...p, vendor: e.target.value }))}
                  className="h-9 rounded-md border bg-background px-3 text-sm w-full"
                >
                  <option value="openai_compatible">OpenAI Compatible</option>
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="ollama">Ollama</option>
                </select>
              </div>
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium">Base URL</label>
              <Input value={form.base_url} onChange={(e) => setForm((p) => ({ ...p, base_url: e.target.value }))} placeholder="https://api.openai.com/v1" />
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-sm font-medium">API Key Env</label>
                <Input value={form.api_key_env} onChange={(e) => setForm((p) => ({ ...p, api_key_env: e.target.value }))} placeholder="OPENAI_API_KEY" />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium">API Key</label>
                <Input
                  type="password"
                  value={form.api_key}
                  onChange={(e) => setForm((p) => ({ ...p, api_key: e.target.value }))}
                  placeholder={editing ? "leave blank to keep" : "sk-..."}
                />
                <p className="text-xs text-muted-foreground flex items-center gap-1">
                  <KeyRound className="h-3 w-3" /> Stored in Keychain
                </p>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-sm font-medium">Models</label>
                <Input value={form.models} onChange={(e) => setForm((p) => ({ ...p, models: e.target.value }))} placeholder="gpt-4o-mini, gpt-4o" />
              </div>
              <div className="space-y-1">
                <label className="text-sm font-medium">Task Routing</label>
                <Input value={form.task_types} onChange={(e) => setForm((p) => ({ ...p, task_types: e.target.value }))} placeholder="default, summary" />
              </div>
            </div>

            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.enabled} onChange={(e) => setForm((p) => ({ ...p, enabled: e.target.checked }))} />
                Enabled
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.is_default} onChange={(e) => setForm((p) => ({ ...p, is_default: e.target.checked }))} />
                Default
              </label>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={saveItem} disabled={saving}>
              {saving && <Loader2 className="h-4 w-4 animate-spin mr-1.5" />}
              {editing ? "Update" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

function SettingsPageContent() {
  const searchParams = useSearchParams()
  const requestedTab = searchParams.get("tab")
  const resolvedTab =
    requestedTab === "daily-brief" || requestedTab === "models" || requestedTab === "account"
      ? requestedTab
      : "account"
  const [activeTab, setActiveTab] = useState<"account" | "daily-brief" | "models">(
    resolvedTab as "account" | "daily-brief" | "models",
  )

  return (
    <div className="min-h-screen px-4 py-8 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="rounded-[30px] border border-slate-200 bg-white/95 px-6 py-6 shadow-[0_20px_44px_rgba(15,23,42,0.06)]">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">Settings</p>
          <h2 className="mt-2 text-4xl font-semibold tracking-[-0.03em] text-slate-950">Settings</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Configure providers, delivery routes, account preferences, and the daily brief pipeline.
          </p>
        </div>
        <Tabs
          value={activeTab}
          onValueChange={(value) => setActiveTab(value as "account" | "daily-brief" | "models")}
        >
          <TabsList className="mb-6 rounded-2xl border border-slate-200 bg-white p-1 shadow-sm">
            <TabsTrigger value="account">Account</TabsTrigger>
            <TabsTrigger value="daily-brief">Daily Brief</TabsTrigger>
            <TabsTrigger value="models">Model Providers</TabsTrigger>
          </TabsList>

          <TabsContent value="account">
            <AccountSection />
          </TabsContent>

          <TabsContent value="daily-brief">
            <DailyBriefSection />
          </TabsContent>

          <TabsContent value="models">
            <div className="space-y-4">
              <div>
                <p className="text-sm text-muted-foreground">Configure LLM providers for paper analysis.</p>
              </div>
              <ModelProvidersSection />
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<div className="min-h-screen px-4 py-8 sm:px-6 lg:px-8" />}>
      <SettingsPageContent />
    </Suspense>
  )
}
