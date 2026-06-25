import type { NextConfig } from "next";

// When NEXT_PUBLIC_API_URL=self, the browser calls the web app's own origin and
// the Next server proxies /api/* to the backend. BACKEND_ORIGIN is the
// in-cluster (or compose) address of the CottonMouth backend, baked at build time.
const backendOrigin = process.env.BACKEND_ORIGIN || "http://cottonmouth-backend:8150";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
