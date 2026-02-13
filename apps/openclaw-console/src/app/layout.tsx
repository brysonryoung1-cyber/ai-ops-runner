import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "OpenClaw Console",
  description: "Private management console for OpenClaw on aiops-1",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="font-sans antialiased bg-apple-bg">
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 overflow-auto">
            <div className="max-w-5xl mx-auto px-8 py-8">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
