import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: "..",
  },
  reactCompiler: true,
  httpAgentOptions: {
    keepAlive: true,
  },
  async rewrites() {
    // Important: Next route handlers under /app/api take precedence over rewrites.
    // This list only applies to paths that do NOT have a corresponding file-based
    // route. We still keep explicit "bypass" rules here for clarity.
    return [
      // Keep NextAuth handlers on the Next.js side
      {
        source: '/api/auth/:path*',
        destination: '/api/auth/:path*',
      },
      // Keep our own proxy/utility routes handled by Next (app/api/**)
      // NOTE: app/api route files already win over rewrites; these entries are
      // mainly defensive and for future explicit exceptions.
      {
        source: '/api/research/:path*',
        destination: '/api/research/:path*',
      },
      {
        source: '/api/runbook/:path*',
        destination: '/api/runbook/:path*',
      },
      {
        source: '/api/studio/:path*',
        destination: '/api/studio/:path*',
      },
      {
        source: '/api/sandbox/:path*',
        destination: '/api/sandbox/:path*',
      },
      {
        source: '/api/papers/:path*',
        destination: '/api/papers/:path*',
      },
      // Default: proxy any other /api/* calls to the FastAPI backend
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/api/:path*',
      },
    ]
  },
};

export default nextConfig;
