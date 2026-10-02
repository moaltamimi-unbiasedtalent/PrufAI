import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "PrüfAI Platform",
  description: "PrüfAI engineering baseline",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="de">
      <body>{children}</body>
    </html>
  );
}
