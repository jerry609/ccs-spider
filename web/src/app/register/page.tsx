"use client"

import { Suspense, useMemo, useState } from "react"
import { signIn } from "next-auth/react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { Eye, EyeOff, Loader2 } from "lucide-react"
import { buildLoginHref } from "@/lib/auth-flow"

type Strength = { score: 0 | 1 | 2 | 3 | 4; label: string; width: string }

const INTEREST_OPTIONS = ["LLM", "Multimodal", "CV", "RL", "Systems", "Theory"] as const

function getStrength(pw: string): Strength {
  if (!pw) return { score: 0, label: "", width: "0%" }
  let score = 0
  if (pw.length >= 8) score++
  if (pw.length >= 12) score++
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) score++
  if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) score++

  const levels: Strength[] = [
    { score: 0, label: "", width: "0%" },
    { score: 1, label: "弱", width: "25%" },
    { score: 2, label: "中等", width: "50%" },
    { score: 3, label: "良好", width: "75%" },
    { score: 4, label: "强", width: "100%" },
  ]
  return levels[score as 0 | 1 | 2 | 3 | 4]
}

export default function RegisterPage() {
  return (
    <Suspense fallback={<RegisterPageContent callbackUrl="/dashboard" />}>
      <RegisterPageWithSearchParams />
    </Suspense>
  )
}

function RegisterPageWithSearchParams() {
  const searchParams = useSearchParams()
  const callbackUrl = searchParams.get("callbackUrl") || "/dashboard"
  return <RegisterPageContent callbackUrl={callbackUrl} />
}

function RegisterPageContent({ callbackUrl }: { callbackUrl: string }) {
  const [firstName, setFirstName] = useState("")
  const [lastName, setLastName] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [accepted, setAccepted] = useState(true)
  const [selectedInterests, setSelectedInterests] = useState<string[]>(["LLM", "Multimodal"])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const router = useRouter()
  const strength = useMemo(() => getStrength(password), [password])
  const loginHref = buildLoginHref(callbackUrl)

  const toggleInterest = (value: string) => {
    setSelectedInterests((current) =>
      current.includes(value) ? current.filter((item) => item !== value) : [...current, value],
    )
  }

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!accepted) {
      setError("请先同意服务条款与隐私政策。")
      return
    }
    setError(null)
    setLoading(true)

    const displayName = [lastName, firstName].map((value) => value.trim()).filter(Boolean).join(" ")

    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, display_name: displayName || undefined }),
    })

    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { detail?: string } | null
      setError(body?.detail ?? "Registration failed. Please try again.")
      setLoading(false)
      return
    }

    const signedIn = await signIn("credentials", { email, password, redirect: false })
    setLoading(false)
    if (signedIn?.error) {
      setError("Account created. Please sign in.")
      router.push(loginHref)
    } else {
      router.push(callbackUrl)
    }
  }

  return (
    <div className="min-h-screen bg-[#faf9f4]">
      <div className="grid min-h-screen lg:grid-cols-2">
        <section className="relative hidden overflow-hidden bg-[linear-gradient(180deg,#134e4a_0%,#0f172a_100%)] px-12 py-12 text-white lg:flex lg:flex-col lg:justify-between">
          <div className="absolute bottom-[-200px] left-[-140px] h-[500px] w-[500px] rounded-full bg-[radial-gradient(circle,rgba(245,158,11,.25),transparent_65%)]" />

          <div className="relative text-[18px] font-bold text-[#5eead4]">PaperBot</div>

          <div className="relative">
            <h2 className="text-[34px] font-semibold leading-[1.3]">
              加入 PaperBot，
              <br />
              重建你的研究节奏。
            </h2>
            <p className="mt-[18px] text-[13px] text-[#94a3b8]">
              注册即享 14 天完整功能，无需信用卡。
            </p>
          </div>

          <div className="relative text-[12px] text-[#94a3b8]">
            已有账号？{" "}
            <Link href={loginHref} className="text-[#5eead4] hover:underline">
              登录
            </Link>
          </div>
        </section>

        <section className="flex items-center justify-center px-6 py-10 sm:px-10">
          <div className="w-full max-w-[400px]">
            <h1 className="text-[24px] font-semibold text-[#111827]">创建账户</h1>
            <p className="mb-6 mt-1 text-[14px] text-[#6b7280]">
              告诉我们你的研究方向，PaperBot 会据此初始化订阅
            </p>

            <form onSubmit={onSubmit}>
              <div className="mb-3 grid grid-cols-2 gap-[10px]">
                <div>
                  <label htmlFor="lastName" className="mb-1 block text-[12px] font-medium text-[#374151]">
                    姓
                  </label>
                  <input
                    id="lastName"
                    type="text"
                    value={lastName}
                    onChange={(e) => setLastName(e.target.value)}
                    placeholder="张"
                    disabled={loading}
                    className="h-[42px] w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-[14px] text-[#111827] outline-none transition-colors placeholder:text-[#9ca3af] focus:border-[#cbd5e1]"
                  />
                </div>
                <div>
                  <label htmlFor="firstName" className="mb-1 block text-[12px] font-medium text-[#374151]">
                    名
                  </label>
                  <input
                    id="firstName"
                    type="text"
                    value={firstName}
                    onChange={(e) => setFirstName(e.target.value)}
                    placeholder="研究"
                    disabled={loading}
                    className="h-[42px] w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-[14px] text-[#111827] outline-none transition-colors placeholder:text-[#9ca3af] focus:border-[#cbd5e1]"
                  />
                </div>
              </div>

              <div className="mb-3">
                <label htmlFor="email" className="mb-1 block text-[12px] font-medium text-[#374151]">
                  邮箱
                </label>
                <input
                  id="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@lab.edu"
                  disabled={loading}
                  required
                  className="h-[42px] w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 text-[14px] text-[#111827] outline-none transition-colors placeholder:text-[#9ca3af] focus:border-[#cbd5e1]"
                />
              </div>

              <div className="mb-3">
                <label htmlFor="password" className="mb-1 block text-[12px] font-medium text-[#374151]">
                  密码
                </label>
                <div className="relative">
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="至少 8 位"
                    disabled={loading}
                    required
                    minLength={8}
                    className="h-[42px] w-full rounded-[10px] border border-[#e5e7eb] bg-white px-3 pr-10 text-[14px] text-[#111827] outline-none transition-colors placeholder:text-[#9ca3af] focus:border-[#cbd5e1]"
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    onClick={() => setShowPassword((value) => !value)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[#9ca3af] hover:text-[#6b7280]"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                <div className="mt-1.5 h-1 overflow-hidden rounded-[2px] bg-[#e5e7eb]">
                  <div
                    className="h-full bg-[linear-gradient(90deg,#f59e0b,#10b981)] transition-all duration-300"
                    style={{ width: strength.width }}
                  />
                </div>
                {strength.label ? (
                  <p className="mt-1 text-[11px] text-[#6b7280]">强度：{strength.label}</p>
                ) : null}
              </div>

              <div className="mb-4">
                <label className="mb-1 block text-[12px] font-medium text-[#374151]">研究方向</label>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {INTEREST_OPTIONS.map((interest) => {
                    const active = selectedInterests.includes(interest)
                    return (
                      <button
                        key={interest}
                        type="button"
                        onClick={() => toggleInterest(interest)}
                        className={[
                          "rounded-full px-3 py-1 text-[12px] transition-colors",
                          active
                            ? "bg-[#ccfbf1] text-[#0f766e]"
                            : "border border-[#e5e7eb] bg-white text-[#374151]",
                        ].join(" ")}
                      >
                        {interest}
                      </button>
                    )
                  })}
                </div>
              </div>

              <label className="mb-4 flex items-center gap-2 text-[12.5px] text-[#374151]">
                <button
                  type="button"
                  onClick={() => setAccepted((value) => !value)}
                  className={[
                    "flex h-4 w-4 items-center justify-center rounded-[4px] border text-[10px]",
                    accepted ? "border-[#111827] bg-[#111827] text-white" : "border-[#d1d5db] bg-white text-transparent",
                  ].join(" ")}
                >
                  ✓
                </button>
                我同意《服务条款》与《隐私政策》
              </label>

              {error ? <p className="mb-3 text-[13px] text-[#dc2626]">{error}</p> : null}

              <button
                type="submit"
                disabled={loading}
                className="flex h-[44px] w-full items-center justify-center rounded-full bg-[#0f172a] px-4 text-[15px] font-medium text-white transition-colors hover:bg-[#1f2937] disabled:cursor-not-allowed disabled:opacity-70"
              >
                {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                创建账户
              </button>
            </form>
          </div>
        </section>
      </div>
    </div>
  )
}
