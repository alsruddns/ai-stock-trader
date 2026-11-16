import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function POST(request: Request) {
  let body: unknown;
  try { body = await request.json(); }
  catch { return NextResponse.json({ error: "요청 형식이 올바르지 않습니다." }, { status: 400 }); }
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "요청 형식이 올바르지 않습니다." }, { status: 400 });
  }
  const input = body as Record<string, unknown>;
  if (typeof input.idempotencyKey !== "string" || !uuidPattern.test(input.idempotencyKey)
      || input.confirmedNoOrder !== true) {
    return NextResponse.json({ error: "KIS 앱에서 주문이 없음을 확인해야 합니다." }, { status: 400 });
  }

  const springBase = process.env.SPRING_API_BASE_URL ?? "http://127.0.0.1:18080";
  try {
    const response = await fetch(`${springBase}/api/v1/kis/orders/paper/${encodeURIComponent(input.idempotencyKey)}/confirm-no-order`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ confirmedNoOrder: true }),
      cache: "no-store",
      signal: AbortSignal.timeout(180000),
    });
    if (!response.ok) {
      return NextResponse.json({ error: response.status === 409
        ? "KIS 주문내역에 해당 종목 행이 있어 UNKNOWN을 유지했습니다. 재전송하지 마세요."
        : "KIS 전체 주문내역을 확인하지 못했습니다. UNKNOWN 상태를 유지합니다." }, { status: response.status });
    }
    const result = await response.json() as { status?: unknown; responseCode?: unknown };
    return NextResponse.json({
      status: typeof result.status === "string" ? result.status : "UNKNOWN",
      responseCode: typeof result.responseCode === "string" ? result.responseCode : null,
    });
  } catch {
    return NextResponse.json({ error: "Spring API에 연결할 수 없습니다. UNKNOWN 상태를 유지합니다." }, { status: 503 });
  }
}
