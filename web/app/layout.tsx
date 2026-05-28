import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "竞品情报工作台 · AI Competitive Radar",
  description: "多 Agent 协作的竞品分析工作台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
