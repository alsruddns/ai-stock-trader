import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const springBase = process.env.SPRING_API_BASE_URL ?? "http://127.0.0.1:8080";
  const pythonBase = process.env.PYTHON_API_BASE_URL ?? "http://127.0.0.1:8000";
  const qualityBase = process.env.MARKET_QUALITY_API_BASE_URL ?? pythonBase;
  const [spring, python, models, paperSession, krSession, usSession] = await Promise.allSettled([
    fetch(`${springBase}/api/v1/operations/summary`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
    fetch(`${pythonBase}/api/v1/health`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
    fetch(`${pythonBase}/api/v1/ai/models`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
    fetch(`${pythonBase}/api/v1/paper/session`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
    fetch(`${pythonBase}/api/v1/market/sessions/KR`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
    fetch(`${pythonBase}/api/v1/market/sessions/US`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
  ]);
  const springResult = spring.status === "fulfilled" && spring.value.ok
    ? await spring.value.json() : null;
  const pythonResult = python.status === "fulfilled" && python.value.ok
    ? await python.value.json() : null;
  const modelsResult = models.status === "fulfilled" && models.value.ok
    ? await models.value.json() : null;
  const paperSessionResult = paperSession.status === "fulfilled" && paperSession.value.ok
    ? await paperSession.value.json() : null;
  const krSessionResult = krSession.status === "fulfilled" && krSession.value.ok
    ? await krSession.value.json() : null;
  const usSessionResult = usSession.status === "fulfilled" && usSession.value.ok
    ? await usSession.value.json() : null;
  const decisions = paperSessionResult && typeof paperSessionResult === "object"
    ? (paperSessionResult as { decisions?: Record<string, { market?: string; symbol?: string }> }).decisions ?? {}
    : {};
  const qualityByMarket: Record<string, unknown> = { KR: null, US: null };
  for (const market of ["KR", "US"] as const) {
    const session = market === "KR" ? krSessionResult : usSessionResult;
    const target = session && typeof session === "object"
      ? session as { session_date?: string; is_session?: boolean }
      : null;
    const symbols = [...new Set(Object.values(decisions)
      .filter((item): item is { market?: string; symbol: string } =>
        item.market === market && typeof item.symbol === "string")
      .map((item) => item.symbol.trim().toUpperCase())
      .filter(Boolean))];
    if (!target?.session_date || !symbols.length || symbols.length > 50) continue;
    const query = new URLSearchParams({ session_date: target.session_date });
    for (const symbol of symbols) query.append("symbols", symbol);
    try {
      const response = await fetch(
        `${qualityBase}/api/v1/market/session-quality/${market}?${query.toString()}`,
        { cache: "no-store", signal: AbortSignal.timeout(5000) },
      );
      if (response.ok) qualityByMarket[market] = await response.json();
    } catch {
      // Keep unavailable quality data null; never convert it into a healthy state.
    }
  }
  return NextResponse.json({
    generatedAt: new Date().toISOString(),
    springConnected: springResult !== null,
    pythonConnected: pythonResult !== null,
    operations: springResult,
    marketAi: pythonResult,
    aiModels: modelsResult,
    paperSession: paperSessionResult,
    marketSessions: { KR: krSessionResult, US: usSessionResult },
    marketDataQuality: qualityByMarket,
  }, { headers: { "Cache-Control": "no-store" } });
}
