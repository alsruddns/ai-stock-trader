import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "요청 형식이 올바르지 않습니다." }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "요청 형식이 올바르지 않습니다." }, { status: 400 });
  }
  const input = body as Record<string, unknown>;
  const idempotencyKey = input.idempotencyKey;
  const brokerOrderNumber = input.brokerOrderNumber;
  const brokerOrganizationNumber = input.brokerOrganizationNumber;
  if (typeof idempotencyKey !== "string" || !uuidPattern.test(idempotencyKey)
      || typeof brokerOrderNumber !== "string" || !/^[0-9]{1,32}$/.test(brokerOrderNumber)
      || (brokerOrganizationNumber !== undefined && brokerOrganizationNumber !== ""
        && (typeof brokerOrganizationNumber !== "string" || !/^[A-Za-z0-9_-]{1,32}$/.test(brokerOrganizationNumber)))
      || input.confirmed !== true) {
    return NextResponse.json({ error: "주문번호와 명시적 확인을 입력해 주세요." }, { status: 400 });
  }

  const springBase = process.env.SPRING_API_BASE_URL ?? "http://127.0.0.1:18080";
  try {
    const response = await fetch(`${springBase}/api/v1/kis/orders/paper/${encodeURIComponent(idempotencyKey)}/manual-reconcile`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        brokerOrderNumber,
        ...(typeof brokerOrganizationNumber === "string" && brokerOrganizationNumber
          ? { brokerOrganizationNumber } : {}),
        confirmed: true,
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) {
      return NextResponse.json({ error: response.status === 409
        ? "KIS 기록이 주문 정보와 일치하지 않아 연결하지 않았습니다. UNKNOWN 상태를 유지합니다."
        : "서버가 수동 대사를 거부했습니다. 주문 상태를 다시 확인해 주세요." }, { status: response.status });
    }
    const result = await response.json() as { status?: unknown; brokerOrderNumber?: unknown };
    return NextResponse.json({
      status: typeof result.status === "string" ? result.status : "UNKNOWN",
      linked: true,
      brokerOrderNumberPresent: typeof result.brokerOrderNumber === "string" && result.brokerOrderNumber.length > 0,
    }, { status: 200 });
  } catch {
    return NextResponse.json({ error: "Spring API에 연결할 수 없습니다. UNKNOWN 상태를 유지하며 재전송하지 않았습니다." }, { status: 503 });
  }
}
