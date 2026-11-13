import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async generateBuildId() {
    return process.env.PRUFAI_RELEASE_BUILD_ID ?? null;
  },
  async rewrites() {
    const backend = process.env.PRUFAI_API_BASE_URL ?? "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
