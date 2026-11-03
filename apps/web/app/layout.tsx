import type { Metadata } from "next";
import { AppShell, AuthProvider } from "../lib/auth";
import { developmentDemoEnabled } from "../lib/demo";

export const metadata: Metadata = {
  title: "PrüfAI Platform",
  description: "Governed assessment workspace",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <AuthProvider
          demoMode={developmentDemoEnabled(
            process.env.NODE_ENV,
            process.env.PRUFAI_DEMO_ENABLED,
          )}
        >
          <AppShell>{children}</AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
