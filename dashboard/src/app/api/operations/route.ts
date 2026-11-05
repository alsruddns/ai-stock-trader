import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const springBase = process.env.SPRING_API_BASE_URL ?? "http://127.0.0.1:8080";
  const pythonBase = process.env.PYTHON_API_BASE_URL ?? "http://127.0.0.1:8000";
  const [spring, python, models] = await Promise.allSettled([
    fetch(`${springBase}/api/v1/operations/summary`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
    fetch(`${pythonBase}/api/v1/health`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
    fetch(`${pythonBase}/api/v1/ai/models`, { cache: "no-store", signal: AbortSignal.timeout(3000) }),
  ]);
  const springResult = spring.status === "fulfilled" && spring.value.ok
    ? await spring.value.json() : null;
  const pythonResult = python.status === "fulfilled" && python.value.ok
    ? await python.value.json() : null;
  const modelsResult = models.status === "fulfilled" && models.value.ok
    ? await models.value.json() : null;
  return NextResponse.json({
    generatedAt: new Date().toISOString(),
    springConnected: springResult !== null,
    pythonConnected: pythonResult !== null,
    operations: springResult,
    marketAi: pythonResult,
    aiModels: modelsResult,
  }, { headers: { "Cache-Control": "no-store" } });
}
