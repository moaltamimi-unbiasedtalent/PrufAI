import type { Metadata } from "next";
import { AppShell, AuthProvider } from "../lib/auth";

export const metadata: Metadata = {
  title: "PrüfAI Platform",
  description: "Governed assessment platform local demo",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="de">
      <body>
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
