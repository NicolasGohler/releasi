import type { Metadata } from "next";
import { Geist_Mono } from "next/font/google";
import { DM_Sans, Cormorant_Garamond } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";
import { Sidebar } from "@/components/layout/sidebar";
import { Toaster } from "@/components/ui/sonner";

const dmSans = DM_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
});

const cormorant = Cormorant_Garamond({
  variable: "--font-brand",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Releasi",
  description: "LinkedIn automation dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${dmSans.variable} ${cormorant.variable} ${geistMono.variable} antialiased`}
      >
        <Providers>
          <Sidebar />
          <main className="ml-56 min-h-screen p-6">{children}</main>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
