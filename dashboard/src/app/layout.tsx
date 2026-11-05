import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Stock Trader | 운영 대시보드",
  description: "국내·미국 주식 paper 운영 상태와 위험 통제를 확인합니다.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body>{children}</body></html>;
}
