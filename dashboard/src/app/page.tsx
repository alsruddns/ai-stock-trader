"use client";

import { useCallback, useEffect, useState } from "react";

type Ops = {
  generatedAt: string; tradingMode: string; liveTrading: string; paperAutomationEnabled: boolean;
  kisAccountReadsEnabled: boolean; kisPaperOrdersEnabled: boolean; orderCounts: Record<string, number>;
  consecutiveLosingTrades: number;
  openOrders: number; fillsToday: number; totalOrders: number;
  cashBalances: { currency: string; balance: string }[];
  positions: { market: string; symbol: string; currency: string; quantity: number; averageCost: string; costBasis: string }[];
  positionCostBasisByCurrency: Record<string, string>; realizedPnlTodayGrossByCurrency: Record<string, string>;
  recentOrders: { id: number; market: string; symbol: string; side: string; quantity: number; filledQuantity: number; limitPrice: string; currency: string; status: string; createdAt: string }[];
};
type Model = { model_id: string; market?: string; model_type?: string; approved?: boolean; active?: boolean; integrity_error?: boolean; test_metrics?: Record<string, number> };
type Decision = { market: string; symbol: string; exchange: string | null; signal: string; reason: string; bars_in_session: number; evaluated_at: string | null; model_id?: string | null; executable: false };
type Payload = { generatedAt: string; springConnected: boolean; pythonConnected: boolean; operations: Ops | null; marketAi: Record<string, unknown> | null; paperSession: { state: string; enabled: boolean; subscriptions: number; ticks_received: number; bars_closed: number; orders_enabled: false; automation_state?: string; automation_error?: string | null; daily_baseline?: { state?: string; currency?: string; equity?: string; reason?: string } | null; decisions?: Record<string, Decision> } | null; aiModels: { models: Model[]; governance_events: Record<string, string>[]; executable: false } | null };

const money = (value: string | number, currency: string) => new Intl.NumberFormat("ko-KR", { style: "currency", currency, maximumFractionDigits: currency === "KRW" ? 0 : 2 }).format(Number(value));
const time = (value: string) => new Intl.DateTimeFormat("ko-KR", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));

export default function Dashboard() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/operations", { cache: "no-store" });
      if (!response.ok) throw new Error("운영 API 응답을 읽을 수 없습니다.");
      setData(await response.json()); setError("");
    } catch { setError("대시보드 API에 연결할 수 없습니다. 로컬 Spring Boot가 실행 중인지 확인하세요."); }
  }, []);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); const timer = window.setInterval(load, 30000); return () => { window.clearTimeout(initial); window.clearInterval(timer); }; }, [load]);
  const ops = data?.operations;
  const connected = data?.springConnected ?? false;
  const pythonConnected = data?.pythonConnected ?? false;
  const states = ops?.orderCounts ?? {};

  return <main className="shell">
    <header className="topbar"><div className="brand-mark">AT</div><div><p className="eyebrow">LOCAL TRADING SYSTEM</p><h1>운영 대시보드</h1></div><div className="top-actions"><span className="mode"><i /> PAPER MODE</span><button onClick={() => void load()} aria-label="새로고침">새로고침</button></div></header>
    <section className="notice"><span className="notice-icon">!</span><div><strong>실거래 잠금 상태</strong><p>이 화면은 조회 전용입니다. 실계좌 주문은 잠겨 있고, paper 자동매매는 안전 조건을 통과한 경우 로컬 모의 장부에만 기록됩니다.</p></div><span className="locked">LIVE OFF</span></section>
    {error && <div className="error">{error}</div>}
    <section className="section-head"><div><p className="eyebrow">SYSTEM STATUS</p><h2>서비스 및 안전 스위치</h2></div><span className="updated">갱신 {data ? time(data.generatedAt) : "확인 중"}</span></section>
    <section className="status-grid">
      <StatusCard title="주문·리스크 API" good={connected} value={connected ? "정상 연결" : "연결 안 됨"} detail="Spring Boot · localhost:8080" />
      <StatusCard title="시세·AI API" good={pythonConnected} value={pythonConnected ? "정상 연결" : "연결 안 됨"} detail="FastAPI · localhost:8000" />
      <StatusCard title="실거래" good={false} value="비활성" detail="LIVE_TRADING_ENABLED=false" />
      <StatusCard title="KIS 모의 주문" good={false} value={ops?.kisPaperOrdersEnabled ? "설정 켜짐" : "잠금"} detail="별도 활성 스위치" />
      <StatusCard title="P5 시세 세션 모니터" good={data?.paperSession?.state === "RUNNING"} value={sessionLabel(data?.paperSession?.state)} detail={`${data?.paperSession?.subscriptions ?? 0}개 구독 · ${data?.paperSession?.bars_closed ?? 0}개 분봉 · 후보 ${Object.values(data?.paperSession?.decisions ?? {}).filter(item => item.signal === "BUY_CANDIDATE").length}개 · paper 자동매매 ${automationLabel(data?.paperSession?.automation_state)} · ${baselineLabel(data?.paperSession?.daily_baseline)}`} />
    </section>
    <section className="metric-grid">
      <Metric title="미체결 주문" value={ops?.openOrders ?? "—"} caption="미체결·부분체결·확인 필요" />
      <Metric title="오늘 모의 체결" value={ops?.fillsToday ?? "—"} caption="한국 시간 기준" />
      <Metric title="오늘 실현 손익 (비용 전)" value={`${money(ops?.realizedPnlTodayGrossByCurrency?.KRW ?? 0, "KRW")} · ${money(ops?.realizedPnlTodayGrossByCurrency?.USD ?? 0, "USD")}`} caption="가상 체결가와 평균 원가 기준" />
      <Metric title="보유 원가 합계" value={`${money(ops?.positionCostBasisByCurrency?.KRW ?? 0, "KRW")} · ${money(ops?.positionCostBasisByCurrency?.USD ?? 0, "USD")}`} caption="통화별 취득 원가 · 시장 평가액 아님" />
    </section>
    <section className="panel signals-panel"><div className="panel-title"><div><p className="eyebrow">P5 STRATEGY PREVIEW</p><h2>종목별 판단 상태</h2></div><span className="tag">paper 진입 게이트 · 실계좌 잠금</span></div>
      {Object.keys(data?.paperSession?.decisions ?? {}).length ? <div className="table-wrap"><table><thead><tr><th>시장</th><th>종목</th><th>판단</th><th>판단 사유</th><th>세션 분봉</th></tr></thead><tbody>{Object.entries(data?.paperSession?.decisions ?? {}).map(([key, item]) => <tr key={key}><td>{item.market}</td><td className="symbol">{item.symbol}{item.exchange ? ` · ${item.exchange}` : ""}</td><td>{signalLabel(item.signal)}</td><td>{decisionReason(item.reason)}</td><td>{item.bars_in_session}</td></tr>)}</tbody></table></div> : <Empty text="세션 모니터가 비활성이거나 아직 종목을 구독하지 않았습니다." />}
    </section>
    <section className="two-col">
      <div className="panel"><div className="panel-title"><div><p className="eyebrow">PAPER ACCOUNT</p><h2>가상 현금</h2></div><span className="tag">실계좌 잔고 아님</span></div>
        {ops?.cashBalances.length ? <div className="cash-list">{ops.cashBalances.map(item => <div className="cash-row" key={item.currency}><span>{item.currency}</span><strong>{money(item.balance, item.currency)}</strong></div>)}</div> : <Empty text="가상 현금 잔고가 아직 없습니다." />}
      </div>
      <div className="panel"><div className="panel-title"><div><p className="eyebrow">RISK SNAPSHOT</p><h2>운영 제한</h2></div><span className="tag green">실거래 주문 잠금</span></div>
        <div className="risk-list"><RiskRow label="운영 모드" value={ops?.tradingMode.toUpperCase() ?? "PAPER"} /><RiskRow label="실거래 주문" value={ops?.liveTrading.toUpperCase() ?? "DISABLED"} /><RiskRow label="KIS 잔고 조회" value={ops?.kisAccountReadsEnabled ? "사용" : "잠금"} /><RiskRow label="KIS 모의 주문" value={ops?.kisPaperOrdersEnabled ? "설정 켜짐" : "잠금"} /><RiskRow label="paper 자동매매" value={automationLabel(data?.paperSession?.automation_state)} /><RiskRow label="연속 손실 주문" value={`${ops?.consecutiveLosingTrades ?? 0}회`} /></div>
      </div>
    </section>
    <section className="two-col lower">
      <div className="panel"><div className="panel-title"><div><p className="eyebrow">PORTFOLIO</p><h2>모의 보유 종목</h2></div><span className="count">{ops?.positions.length ?? 0}</span></div>
        {ops?.positions.length ? <div className="table-wrap"><table><thead><tr><th>시장</th><th>종목</th><th>수량</th><th>평균 원가</th><th>통화</th></tr></thead><tbody>{ops.positions.map(p => <tr key={`${p.market}-${p.symbol}`}><td>{p.market}</td><td className="symbol">{p.symbol}</td><td>{p.quantity}</td><td>{money(p.averageCost, p.currency)}</td><td>{p.currency}</td></tr>)}</tbody></table></div> : <Empty text="모의 보유 종목이 없습니다." />}
      </div>
      <div className="panel"><div className="panel-title"><div><p className="eyebrow">ORDER LEDGER</p><h2>최근 모의 주문</h2></div><span className="count">{Object.values(states).reduce((a, b) => a + b, 0)}</span></div>
        {ops?.recentOrders.length ? <div className="table-wrap"><table><thead><tr><th>종목</th><th>구분</th><th>체결</th><th>상태</th></tr></thead><tbody>{ops.recentOrders.slice(0, 8).map(o => <tr key={o.id}><td><small>{o.market}</small><br /><span className="symbol">{o.symbol}</span></td><td className={o.side === "BUY" ? "buy" : "sell"}>{o.side === "BUY" ? "매수" : "매도"}</td><td>{o.filledQuantity}/{o.quantity}</td><td><span className={`order-state ${o.status.toLowerCase()}`}>{statusLabel(o.status)}</span></td></tr>)}</tbody></table></div> : <Empty text="기록된 paper 주문이 없습니다." />}
      </div>
    </section>
    <section className="panel models-panel"><div className="panel-title"><div><p className="eyebrow">MODEL GOVERNANCE</p><h2>AI 모델 승인 및 롤백</h2></div><span className="tag">주문 실행과 분리</span></div>
      <p className="model-note">모델 승인과 활성화는 시장별 paper 진입 후보 평가에 사용됩니다. AI 신호만으로 주문하지 않고, Spring paper 위험 게이트를 통과해야 로컬 모의 장부에 반영됩니다. KIS 실계좌 주문은 잠겨 있습니다.</p>
      {data?.aiModels?.models.length ? <div className="model-grid">{data.aiModels.models.map(model => <article className="model-card" key={model.model_id}><div className="model-top"><strong>{model.market ?? "시장 미지정"} · {model.model_type ?? model.model_id.split("-")[0]}</strong><div>{model.active && <span className="tag green">ACTIVE</span>}<span className={`tag ${model.approved ? "green" : ""}`}>{model.approved ? "승인" : model.integrity_error ? "검증 오류" : "미승인"}</span></div></div><code>{model.model_id}</code><div className="model-meta"><span>테스트 정확도</span><b>{model.test_metrics?.accuracy == null ? "—" : `${(model.test_metrics.accuracy * 100).toFixed(1)}%`}</b></div></article>)}</div> : <Empty text={pythonConnected ? "등록된 AI 모델이 없습니다." : "Python AI 서비스 연결 후 모델 현황을 표시합니다."} />}
      <p className="audit-note">승인·활성화·롤백은 검토 사유와 작업자명을 기록하는 로컬 API로 관리합니다. 현재 성과 수치는 승인 기준을 대신하지 않습니다.</p>
    </section>
    <footer><span>AI STOCK TRADER · LOCAL ONLY</span><span>데이터 시각 {data ? time(data.generatedAt) : "—"} · 30초마다 자동 새로고침</span></footer>
  </main>;
}

function StatusCard({ title, good, value, detail }: { title: string; good: boolean; value: string; detail: string }) { return <article className="status-card"><div className="card-head"><span>{title}</span><i className={good ? "dot good" : "dot bad"} /></div><strong>{value}</strong><small>{detail}</small></article>; }
function Metric({ title, value, caption }: { title: string; value: string | number; caption: string }) { return <article className="metric"><span>{title}</span><strong>{value}</strong><small>{caption}</small></article>; }
function RiskRow({ label, value }: { label: string; value: string }) { return <div className="risk-row"><span>{label}</span><strong>{value}</strong></div>; }
function Empty({ text }: { text: string }) { return <div className="empty">{text}</div>; }
function statusLabel(value: string) { return ({ PENDING: "대기", PARTIALLY_FILLED: "부분체결", FILLED: "체결", REJECTED: "거부", CANCEL_PENDING: "취소 확인", CANCELLED: "취소", EXPIRED: "만료", UNKNOWN: "확인 필요" } as Record<string, string>)[value] ?? value; }
function sessionLabel(value?: string) { return ({ RUNNING: "시세 수집 중", CONNECTING: "연결 중", RECONNECTING: "재연결 대기", BLOCKED: "설정 확인 필요", STOPPED: "중지", DISABLED: "비활성" } as Record<string, string>)[value ?? ""] ?? "확인 중"; }
function signalLabel(value: string) { return value === "BUY_CANDIDATE" ? "AI·퀀트 후보" : "관망"; }
function decisionReason(value: string) { return ({ WAITING_FOR_CLOSED_BARS: "닫힌 분봉 대기", INSUFFICIENT_SESSION_HISTORY: "세션 이력 부족", STRATEGY_FILTERS_NOT_MET: "퀀트 조건 미충족", AI_MODEL_NOT_APPROVED_FOR_MARKET: "시장 승인 모델 없음", AI_THRESHOLD_NOT_MET: "AI 기준 미달", AI_INFERENCE_UNAVAILABLE: "AI 추론 불가", BAR_SEQUENCE_INVALID: "분봉 순서 오류", QUANT_AND_APPROVED_AI_CANDIDATE: "퀀트 및 승인 AI 통과" } as Record<string, string>)[value] ?? value; }
function automationLabel(value?: string) { return ({ EXIT_MONITORING: "감시 중", BASELINE_CAPTURED: "기준선 저장", BASELINE_PRESENT: "기준선 확인", FILLED: "paper 체결", PENDING: "paper 주문 대기", UNKNOWN: "확인 필요", BLOCKED: "차단", DISABLED: "잠금" } as Record<string, string>)[value ?? ""] ?? "잠금"; }
function baselineLabel(value?: { state?: string; currency?: string; equity?: string; reason?: string } | null) {
  if (!value) return "일일 기준선 대기";
  if (value.state === "BASELINE_CAPTURED") return `기준선 ${value.currency ?? "통화"} 저장`;
  if (value.state === "BASELINE_PRESENT") return `기준선 ${value.currency ?? "통화"} 확인`;
  if (value.state === "BLOCKED") return `기준선 차단 · ${value.reason ?? "확인 필요"}`;
  return "일일 기준선 대기";
}
