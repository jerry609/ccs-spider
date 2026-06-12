"use client"

import { Suspense, useState } from "react"
import { signIn } from "next-auth/react"
import { useRouter, useSearchParams } from "next/navigation"
import Link from "next/link"
import { Eye, EyeOff, Loader2 } from "lucide-react"

export default function LoginPage() {
  return (
    <Suspense fallback={<LoginPageContent callbackUrl="/dashboard" />}>
      <LoginPageWithSearchParams />
    </Suspense>
  )
}

function LoginPageWithSearchParams() {
  const searchParams = useSearchParams()
  const callbackUrl = searchParams.get("callbackUrl") || "/dashboard"
  return <LoginPageContent callbackUrl={callbackUrl} />
}

function LoginPageContent({ callbackUrl }: { callbackUrl: string }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [showPassword, setShowPassword] = useState(false)
  const [remember, setRemember] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [githubLoading, setGithubLoading] = useState(false)
  const router = useRouter()
  const registerHref = `/register?callbackUrl=${encodeURIComponent(callbackUrl)}`
  const forgotHref = `/forgot-password?callbackUrl=${encodeURIComponent(callbackUrl)}`

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const check = await fetch("/api/auth/login-check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      })
      if (!check.ok) {
        const data = (await check.json().catch(() => ({}))) as { detail?: string }
        setError(data.detail || "Invalid email or password.")
        setLoading(false)
        return
      }
    } catch {
      // Keep the same sign-in path for transient backend validation failures.
    }

    const res = await signIn("credentials", { email, password, redirect: false })
    setLoading(false)
    if (res?.error) {
      setError("Invalid email or password.")
      return
    }
    router.push(callbackUrl)
  }

  const onGithub = () => {
    setGithubLoading(true)
    signIn("github", { callbackUrl })
  }

  return (
    <div className="min-h-screen bg-[#faf9f4]">
      <div className="grid min-h-screen lg:grid-cols-2">
        <section className="relative hidden overflow-hidden bg-[linear-gradient(180deg,#1e293b_0%,#0f172a_100%)] px-12 py-12 text-white lg:flex lg:flex-col lg:justify-between">
          <div className="absolute right-[-120px] top-[-120px] h-[440px] w-[440px] rounded-full bg-[radial-gradient(circle,rgba(13,148,136,.3),transparent_65%)]" />

          <div className="relative flex items-center gap-2 text-[18px] font-bold text-[#60a5fa]">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="4" width="18" height="16" rx="3" />
              <path d="M7 9h10M7 13h7" />
              <circle cx="17" cy="15" r="2" fill="currentColor" stroke="none" />
            </svg>
            <span>PaperBot</span>
          </div>

          <div className="relative">
            <h2 className="text-[34px] font-semibold leading-[1.3]">
              把阅读论文
              <br />
              变成可被调度的
              <br />
              研究工作流。
            </h2>

            <div className="mt-5 flex items-start gap-3 text-[14px] text-[#cbd5e1]">
              <span className="mt-2 h-1.5 w-1.5 rounded-full bg-[#0d9488]" />
              <span>每日 DailyPaper 自动产出高优候选</span>
            </div>
            <div className="mt-4 flex items-start gap-3 text-[14px] text-[#cbd5e1]">
              <span className="mt-2 h-1.5 w-1.5 rounded-full bg-[#0d9488]" />
              <span>跨 arXiv、S2、HF Daily 的统一检索和 Judge 评分</span>
            </div>
            <div className="mt-4 flex items-start gap-3 text-[14px] text-[#cbd5e1]">
              <span className="mt-2 h-1.5 w-1.5 rounded-full bg-[#0d9488]" />
              <span>DeepCode Studio 一键复现论文实验</span>
            </div>
          </div>

          <p className="relative text-[12px] text-[#94a3b8]">© 2026 PaperBot · v1.0</p>
        </section>

        <section className="flex items-center justify-center px-6 py-10 sm:px-10">
          <div className="w-full max-w-[360px]">
            <h1 className="text-[24px] font-semibold text-[#111827]">欢迎回来</h1>
            <p className="mb-7 mt-1 text-[14px] text-[#6b7280]">用邮箱登录或继续使用第三方账号</p>

            <form onSubmit={onSubmit}>
              <div className="mb-3">
                <label htmlFor="email" className="mb-1.5 block text-[12px] font-medium text-[#374151]">
                  邮箱
                </label>
                <input
                  id="email"
                  type="email"
                  autoComplete={remember ? "email" : "off"}
                  placeholder="you@lab.edu"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={loading || githubLoading}
                  required
                  className="h-[44px] w-full rounded-[10px] border border-[#e5e7eb] bg-white px-4 text-[14px] text-[#111827] outline-none transition-colors placeholder:text-[#9ca3af] focus:border-[#cbd5e1]"
                />
              </div>

              <div className="mb-3">
                <label htmlFor="password" className="mb-1.5 block text-[12px] font-medium text-[#374151]">
                  密码
                </label>
                <div className="relative">
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete={remember ? "current-password" : "off"}
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={loading || githubLoading}
                    required
                    className="h-[44px] w-full rounded-[10px] border border-[#e5e7eb] bg-white px-4 pr-11 text-[14px] text-[#111827] outline-none transition-colors placeholder:text-[#9ca3af] focus:border-[#cbd5e1]"
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
              </div>

              <div className="mb-4 flex items-center justify-between">
                <label className="flex items-center gap-2 text-[12.5px] text-[#374151]">
                  <button
                    type="button"
                    onClick={() => setRemember((value) => !value)}
                    className={[
                      "flex h-4 w-4 items-center justify-center rounded-[4px] border text-[10px]",
                      remember ? "border-[#111827] bg-[#111827] text-white" : "border-[#d1d5db] bg-white text-transparent",
                    ].join(" ")}
                  >
                    ✓
                  </button>
                  记住我
                </label>
                <Link href={forgotHref} className="text-[12.5px] text-[#6b7280] hover:text-[#111827]">
                  忘记密码？
                </Link>
              </div>

              {error ? (
                <p className="mb-3 text-[13px] text-[#dc2626]">{error}</p>
              ) : null}

              <button
                type="submit"
                disabled={loading || githubLoading}
                className="flex h-[44px] w-full items-center justify-center rounded-full bg-[#0f172a] px-4 text-[15px] font-medium text-white transition-colors hover:bg-[#1f2937] disabled:cursor-not-allowed disabled:opacity-70"
              >
                {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                登录 PaperBot
              </button>
            </form>

            <div className="my-5 flex items-center gap-3 text-[12px] text-[#9ca3af]">
              <span className="h-px flex-1 bg-[#e5e7eb]" />
              或
              <span className="h-px flex-1 bg-[#e5e7eb]" />
            </div>

            <button
              type="button"
              onClick={onGithub}
              disabled={loading || githubLoading}
              className="flex h-[44px] w-full items-center justify-center gap-2 rounded-[10px] border border-[#e5e7eb] bg-white text-[13px] text-[#111827] transition-colors hover:bg-[#fafbfc] disabled:cursor-not-allowed disabled:opacity-70"
            >
              {githubLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitHubIcon />}
              使用 GitHub 登录
            </button>

            <button
              type="button"
              disabled
              className="mt-2 flex h-[44px] w-full items-center justify-center gap-2 rounded-[10px] border border-[#e5e7eb] bg-white text-[13px] text-[#9ca3af] opacity-80"
            >
              <span>🎓</span>
              使用 Google Scholar 登录
            </button>

            <p className="mt-5 text-center text-[12.5px] text-[#6b7280]">
              还没有账号？{" "}
              <Link href={registerHref} className="text-[#111827] hover:underline">
                创建账户
              </Link>
            </p>
          </div>
        </section>
      </div>
    </div>
  )
}

function GitHubIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor" aria-hidden="true">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
    </svg>
  )
}
