import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SwiggyOpt | Food price comparison",
  description: "Compare real Swiggy checkout prices without exposing account credentials."
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
