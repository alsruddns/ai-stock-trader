"use client";

import { useCallback, useEffect, useState } from "react";

type Ops = {
  generatedAt: string; tradingMode: string; liveTrading: string; paperAutomationEnabled: boolean;
  kisAccountReadsEnabled: boolean; kisPaperAccountReadsEnabled: boolean; kisPaperOrdersEnabled: boolean; kisPaperDomesticCancelSupported: boolean; kisRealOrdersEnabled: boolean; kisRealAccountReadsEnabled: boolean; kisRealReconciliationEnabled: boolean; realProtectiveExitManagerEnabled: boolean; kisRealProtectiveExitAutomationEnabled: boolean;
  kisPaperCredentialsConfigured: boolean; kisRealCredentialsConfigured: boolean;
  kisRealKrCostsConfigured: boolean; kisRealUsCostsConfigured: boolean;
  realOrderCounts: Record<string, number>;
  paperKisOrderCounts: Record<string, number>;
  paperCostModelEnabled: boolean; paperTransactionCostsTodayByCurrency: Record<string, string>; realizedPnlTodayNetByCurrency: Record<string, string>;
  recentPaperKisOrders?: { market: string; symbol: string; side: string; quantity: number; filledQuantity: number; remainingQuantity: number; currency: string; status: string; responseCode: string | null; createdAt: string; updatedAt: string }[];
  recentRealOrders?: { market: string; symbol: string; side: string; quantity: number; filledQuantity: number; remainingQuantity: number; currency: string; status: string; responseCode?: string | null; createdAt: string; updatedAt: string }[];
  realExitPlans?: { market: string; symbol: string; status: string; reason: string | null; entryFilledQuantity: number | null; exitQuantity: number | null; updatedAt: string }[];
  orderCounts: Record<string, number>;
  consecutiveLosingTrades: number;
  openOrders: number; fillsToday: number; totalOrders: number;
  cashBalances: { currency: string; balance: string }[];
  positions: { market: string; symbol: string; currency: string; quantity: number; averageCost: string; costBasis: string }[];
  paperPerformanceByInstrument?: { market: string; symbol: string; currency: string; strategyId: string | null; modelId: string | null; realizedSellOrders: number; wins: number; losses: number; netPnl: string; averageWin: string; averageLoss: string; winRate: string; expectancy: string; profitFactor: string | null; maxRealizedDrawdown: string }[];
  positionCostBasisByCurrency: Record<string, string>; realizedPnlTodayGrossByCurrency: Record<string, string>;
  recentOrders: { id: number; market: string; symbol: string; side: string; quantity: number; filledQuantity: number; limitPrice: string; currency: string; status: string; createdAt: string }[];
};
type Model = { model_id: string; market?: string; model_type?: string; approved?: boolean; active?: boolean; integrity_error?: boolean; feature_compatible?: boolean; data_rights_verified?: boolean; approval_evidence_verified?: boolean; artifact_sha256?: string; dataset_sha256?: string; test_metrics?: Record<string, number>; quant_baseline_test_metrics?: Record<string, number>; quant_backtest_test_metrics?: Record<string, string | number>; ai_quant_backtest_test_metrics?: Record<string, string | number>; training_config?: Record<string, string | number> };
type Decision = { market: string; symbol: string; exchange: string | null; signal: string; reason: string; bars_in_session: number; evaluated_at: string | null; model_id?: string | null; executable: false };
type MarketSession = { market: "KR" | "US"; session_date: string; is_session: boolean; is_open: boolean; open_at: string | null; close_at: string | null; evaluated_at: string };
type SessionQuality = { market: string; symbol: string; session_date: string; status: string; expected_bars: number; observed_bars: number; missing_bars: number; spread_bars: number; spread_coverage_percent: number | null; missing_bar_timestamps?: string[]; bars_without_spread_timestamps?: string[]; full_session_complete: boolean };
type SessionQualityBatch = { market: string; session_date: string; sessions: SessionQuality[] };
type CostAssumptionStatus = { configured: boolean; missing: string[]; invalid: string[] };
type MarketAiHealth = Record<string, unknown> & { strategy_cost_assumptions?: Partial<Record<"KR" | "US", CostAssumptionStatus>> };
type CandidateRefresh = { state: string; reason?: string; session_open?: string; selected?: number; added?: string[]; removed?: string[]; preserved?: number };
type ScheduledTask = { kind: string; market: string | null; symbol: string | null; taskDate: string; state: string; enabled: boolean; principalLogonType: string; requiresInteractiveLogon: boolean | null; wakeToRun: boolean; startWhenAvailable: boolean; nextRunTime: string | null; lastRunTime: string | null; lastTaskResult: number | null };
type LocalSchedule = { state: string; registeredCount: number; krRegisteredCount?: number; usRegisteredCount?: number; paperLifecycleCount?: number; modelTrainingCount?: number; modelHoldoutCount?: number; tasks: ScheduledTask[] };
type LocalOperationsReport = { generatedAt: string; reportSchemaVersion: number; localStackTask: { state: string; principalLogonType: string; requiresInteractiveLogon: boolean | null; enabled: boolean | null; nextRunTime: string | null; lastRunTime: string | null; lastTaskResult: number | null } | null; sessionMonitorSchedule: LocalSchedule | null; validationTaskSchedule: LocalSchedule | null };
type TrainingReadiness = { market: string; symbol: string; from_date: string; to_date: string; minimum_required_sessions: number; eligible_sessions: number; additional_sessions_needed: number; minimum_met: boolean };
type Payload = { generatedAt: string; springConnected: boolean; pythonConnected: boolean; operations: Ops | null; marketAi: MarketAiHealth | null; paperSession: { state: string; enabled: boolean; subscriptions: number; ticks_received: number; bars_closed: number; orderbook_updates_by_feed?: Record<string, number>; last_orderbook_received_at_by_feed?: Record<string, string | null>; frames_received_by_feed?: Record<string, number>; frame_transaction_counts_by_feed?: Record<string, Record<string, number>>; orders_enabled: false; mode?: string; real_signal_state?: string; real_signal_error?: string | null; last_real_signal?: { market: string; symbol: string; order_status?: string; idempotency_key?: string; submitted_at?: string; executable: false } | null; paper_reconciliation_required?: boolean; paper_unknown_idempotency_key?: string | null; kr_calendar_compatibility?: string; stream_connections?: Record<string, string>; subscription_acknowledgements_by_feed?: Record<string, { accepted_count: number; required_count: number; complete: boolean }>; last_subscription_error_code?: string | null; automation_state?: string; automation_error?: string | null; daily_baseline?: { state?: string; currency?: string; equity?: string; reason?: string } | null; candidate_refresh_by_market?: Record<string, CandidateRefresh>; decisions?: Record<string, Decision> } | null; marketSessions?: { KR: MarketSession | null; US: MarketSession | null }; marketDataQuality?: { KR: SessionQualityBatch | null; US: SessionQualityBatch | null }; localOperationsReport?: LocalOperationsReport | null; localOperationsReportFresh?: boolean; trainingReadiness?: Record<string, TrainingReadiness | null>; aiModels: { models: Model[]; governance_events: Record<string, string>[]; governance_audit?: { integrity_verified: boolean; event_count: number }; executable: false } | null };

const money = (value: string | number, currency: string) => new Intl.NumberFormat("ko-KR", { style: "currency", currency, maximumFractionDigits: currency === "KRW" ? 0 : 2 }).format(Number(value));
const percent = (value?: number) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const basisPoints = (value?: number) => value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)} bps`;
const trainingConfig = (value?: Record<string, string | number>) => value?.symbol ? `레이블 ${value.label_horizon_bars ?? "?"}봉 · 비용 ${value.label_round_trip_cost_bps ?? "?"} bps · 1주/세션 · 매수/매도 수수료 ${value.backtest_buy_commission_bps ?? "?"}/${value.backtest_sell_commission_bps ?? "?"} · 매도세금 ${value.backtest_sell_tax_bps ?? "?"} · 편도 슬리피지 ${value.backtest_slippage_bps ?? "?"} bps` : "이전 모델 · 미기록";
const time = (value: string) => new Intl.DateTimeFormat("ko-KR", { dateStyle: "short", timeStyle: "medium" }).format(new Date(value));
const marketMinute = (value: string, market: string) => new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", timeZone: market === "KR" ? "Asia/Seoul" : "America/New_York" }).format(new Date(value));

export default function Dashboard() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/operations", { cache: "no-store" });
      if (!response.ok) throw new Error("운영 API 응답을 읽을 수 없습니다.");
      const payload = await response.json() as Payload;
      const reportTime = payload.localOperationsReport ? Date.parse(payload.localOperationsReport.generatedAt) : Number.NaN;
      const reportAgeHours = (Date.now() - reportTime) / 3_600_000;
      payload.localOperationsReportFresh = Number.isFinite(reportAgeHours) && reportAgeHours >= -1 && reportAgeHours <= 26;
      setData(payload); setError("");
    } catch { setError("대시보드 API에 연결할 수 없습니다. 로컬 Spring Boot가 실행 중인지 확인하세요."); }
  }, []);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); const timer = window.setInterval(load, 30000); return () => { window.clearTimeout(initial); window.clearInterval(timer); }; }, [load]);
  const ops = data?.operations;
  const realEntryGatesOpen = Boolean(ops && ops.tradingMode === "real" && ops.liveTrading === "enabled"
    && ops.kisRealOrdersEnabled && ops.kisAccountReadsEnabled && ops.kisRealAccountReadsEnabled
    && ops.kisRealReconciliationEnabled && ops.realProtectiveExitManagerEnabled
    && ops.kisRealProtectiveExitAutomationEnabled && ops.kisRealCredentialsConfigured
    && ops.kisRealKrCostsConfigured && ops.kisRealUsCostsConfigured);
  const connected = data?.springConnected ?? false;
  const pythonConnected = data?.pythonConnected ?? false;
  const costReadiness = data?.marketAi?.strategy_cost_assumptions;
  const costStatus = (market: "KR" | "US") => {
    const status = costReadiness?.[market];
    if (!status) return "조회 불가";
    if (status.configured) return "입력 완료";
    if (status.invalid.length) return `값 확인 필요 ${status.invalid.length}개`;
    return `미설정 ${status.missing.length}개`;
  };
  const missingCostDetails = (["KR", "US"] as const).flatMap((market) => {
    const status = costReadiness?.[market];
    if (!status || status.configured) return [];
    const keys = [...status.missing, ...status.invalid];
    return keys.length ? [`${market}: ${keys.join(", ")}`] : [];
  }).join(" · ");
  const localReport = data?.localOperationsReport;
  const scheduledTasks = [
    ...(localReport?.sessionMonitorSchedule?.tasks ?? []),
    ...(localReport?.validationTaskSchedule?.tasks ?? []),
  ].sort((left, right) => (left.nextRunTime ?? "9999").localeCompare(right.nextRunTime ?? "9999"));
  const localReportFresh = data?.localOperationsReportFresh ?? false;
  const trainingReadiness = Object.values(data?.trainingReadiness ?? {}).filter(
    (item): item is TrainingReadiness => item !== null,
  );
  const trainingReadinessSummary = trainingReadiness.length
    ? trainingReadiness.map((item) => `${item.market} ${item.symbol} ${item.eligible_sessions}/${item.minimum_required_sessions}`).join(" · ")
    : "학습 데이터를 확인할 수 없음";
  const trainingReadinessDetail = trainingReadiness.length
    ? trainingReadiness.map((item) => `${item.market} ${item.symbol}: ${item.additional_sessions_needed > 0 ? `완전한 호가 포함 세션 ${item.additional_sessions_needed}개 더 필요` : "학습 최소 세션 충족"} · ${item.from_date}~${item.to_date}`).join(" / ")
    : "P4 재학습 작업 또는 저장 데이터 상태를 확인하세요.";
  const interactiveTasks = scheduledTasks.filter((task) => task.requiresInteractiveLogon === true).length
    + (localReport?.localStackTask?.requiresInteractiveLogon === true ? 1 : 0);
  const states = ops?.orderCounts ?? {};
  const qualitySessions = [
    ...(data?.marketDataQuality?.KR?.sessions ?? []),
    ...(data?.marketDataQuality?.US?.sessions ?? []),
  ];
  const qualityBySymbol = new Map(qualitySessions.map((item) => [`${item.market}:${item.symbol}`, item]));
  const completeQualityCount = qualitySessions.filter((item) => item.full_session_complete
    && item.spread_coverage_percent === 100).length;
  const qualitySummary = qualitySessions.length
    ? `${completeQualityCount}/${qualitySessions.length}개 종목이 정규장 봉·스프레드 100%`
    : "저장 품질을 확인할 수 없음";
  const qualityDetails = qualitySessions.slice(0, 3).map((item) => {
    const missingBars = item.missing_bar_timestamps ?? [];
    const missingSpread = item.bars_without_spread_timestamps ?? [];
    const gapDetail = missingBars.length ? ` · 분봉누락 ${missingBars.slice(0, 3).map(value => marketMinute(value, item.market)).join(",")}${missingBars.length > 3 ? ` 외 ${missingBars.length - 3}개` : ""}` : "";
    const spreadDetail = missingSpread.length ? ` · 호가누락 ${missingSpread.slice(0, 3).map(value => marketMinute(value, item.market)).join(",")}${missingSpread.length > 3 ? ` 외 ${missingSpread.length - 3}개` : ""}` : "";
    return `${item.market} ${item.session_date} ${item.symbol} ${item.status} · ${item.observed_bars}/${item.expected_bars}봉 · spread ${item.spread_coverage_percent ?? "—"}%${gapDetail}${spreadDetail}`;
  }).join(" / ");

  return <main className="shell">
    <header className="topbar"><div className="brand-mark">AT</div><div><p className="eyebrow">LOCAL TRADING SYSTEM</p><h1>운영 대시보드</h1></div><div className="top-actions"><span className="mode"><i /> {ops?.tradingMode.toUpperCase() ?? "PAPER"} MODE</span><button onClick={() => void load()} aria-label="새로고침">새로고침</button></div></header>
    <section className="notice"><span className="notice-icon">!</span><div><strong>{realEntryGatesOpen ? "실거래 진입 설정이 열려 있습니다" : "실거래 신규진입 잠금 상태"}</strong><p>이 화면은 조회 전용입니다. 실계좌 신규매수는 계좌읽기·체결대조·보호청산 준비 설정까지 모두 켜져야 허용됩니다. PAPER 자동매매는 로컬 모의 장부만 사용합니다.</p></div><span className="locked">{realEntryGatesOpen ? "ENTRY GATES ON" : "ENTRIES LOCKED"}</span></section>
    {error && <div className="error">{error}</div>}
    <section className="section-head"><div><p className="eyebrow">SYSTEM STATUS</p><h2>서비스 및 안전 스위치</h2></div><span className="updated">갱신 {data ? time(data.generatedAt) : "확인 중"}</span></section>
    <section className="status-grid">
      <StatusCard title="주문·리스크 API" good={connected} value={connected ? "정상 연결" : "연결 안 됨"} detail="Spring Boot · localhost:18080" />
      <StatusCard title="시세·AI API" good={pythonConnected} value={pythonConnected ? "정상 연결" : "연결 안 됨"} detail="FastAPI · localhost:18000" />
      <StatusCard title="P5 KR·US 거래 세션" good={Boolean(data?.marketSessions?.KR && data?.marketSessions?.US)} value={`KR ${marketSessionLabel(data?.marketSessions?.KR)} · US ${marketSessionLabel(data?.marketSessions?.US)}`} detail={`거래일 ${data?.marketSessions?.KR?.session_date ?? "미확인"} · XKRX 캘린더 ${calendarLabel(data?.paperSession?.kr_calendar_compatibility)} · 판정 불가 시 진입 차단`} />
      <StatusCard title="P3 저장 분봉 품질" good={qualitySessions.length > 0 && completeQualityCount === qualitySessions.length} value={qualitySummary} detail={qualityDetails || "정규장 세션 날짜에 대한 PostgreSQL 자료가 필요합니다."} />
      <StatusCard title="P4 모델 학습 데이터" good={trainingReadiness.length > 0 && trainingReadiness.every((item) => item.minimum_met)} value={trainingReadinessSummary} detail={trainingReadinessDetail} />
      <StatusCard title="P7 Windows 예약작업" good={Boolean(localReportFresh && localReport?.localStackTask?.state === "Running" && localReport.sessionMonitorSchedule?.state === "AVAILABLE" && localReport.validationTaskSchedule?.state === "AVAILABLE")} value={localReport ? `${localReport.sessionMonitorSchedule?.registeredCount ?? 0}개 감시 · ${localReport.validationTaskSchedule?.registeredCount ?? 0}개 검증` : "상태 확인 불가"} detail={localReport ? `보고서 ${time(localReport.generatedAt)} · 로그인 필요 ${interactiveTasks}개${localReportFresh ? " · 최신" : " · 오래된 보고서"}` : "최신 Windows 운영 보고서가 없습니다."} />
      <StatusCard title="실거래 주문" good={false} value={ops?.kisRealOrdersEnabled ? "설정 켜짐" : "잠금"} detail={`실주문 ${ops?.liveTrading.toUpperCase() ?? "DISABLED"} · KIS_REAL_ORDERS_ENABLED`} />
      <StatusCard title="REAL 신규매수 보호" good={false} value={realEntryGatesOpen ? "진입 설정 열림" : "진입 잠금"} detail={`보호청산 · 주문대조 · 계좌조회 · 비용률 형식 KR ${ops?.kisRealKrCostsConfigured ? "유효" : "미설정"}/US ${ops?.kisRealUsCostsConfigured ? "유효" : "미설정"} 필요`} />
      <StatusCard title="KIS 모의 주문" good={false} value={ops?.kisPaperOrdersEnabled ? "설정 켜짐" : "잠금"} detail="별도 활성 스위치" />
      <StatusCard title="국내 PAPER 취소" good={ops?.kisPaperDomesticCancelSupported ?? false} value={ops?.kisPaperDomesticCancelSupported ? "지원 확인됨" : "잠금"} detail="모의 취소 가능수량 조회 계약 미확인 · 시험 주문 제출 차단" />
      <StatusCard title="KIS 자격정보 설정" good={ops?.kisPaperCredentialsConfigured ?? false} value={ops?.kisPaperCredentialsConfigured ? "PAPER 값 입력됨" : "PAPER 값 미설정"} detail={`실계좌 ${ops?.kisRealCredentialsConfigured ? "입력됨" : "미설정"} · 계좌조회 스위치 PAPER ${ops?.kisAccountReadsEnabled && ops?.kisPaperAccountReadsEnabled ? "켜짐" : "잠금"} / REAL ${ops?.kisAccountReadsEnabled && ops?.kisRealAccountReadsEnabled ? "켜짐" : "잠금"} · 인증 성공 여부 아님`} />
      <StatusCard title={data?.paperSession?.mode === "REAL_SIGNAL" ? "P6 REAL 신호 감시기" : "P5 시세 세션 모니터"} good={data?.paperSession?.state === "RUNNING"} value={sessionLabel(data?.paperSession?.state)} detail={`${streamFeedLabel(data?.paperSession?.stream_connections, data?.paperSession?.mode)} · ${subscriptionAckLabel(data?.paperSession?.subscription_acknowledgements_by_feed, data?.paperSession?.mode)} · ${streamFrameDiagnosticsLabel(data?.paperSession?.frames_received_by_feed, data?.paperSession?.frame_transaction_counts_by_feed)}${data?.paperSession?.last_subscription_error_code ? ` · KIS 구독 오류 ${data.paperSession.last_subscription_error_code}` : ""} · ${data?.paperSession?.subscriptions ?? 0}개 종목 · 호가 ${Object.values(data?.paperSession?.orderbook_updates_by_feed ?? {}).reduce((sum, count) => sum + count, 0)}회 · ${data?.paperSession?.bars_closed ?? 0}개 분봉 · 후보 ${Object.values(data?.paperSession?.decisions ?? {}).filter(item => item.signal === "BUY_CANDIDATE").length}개 · ${data?.paperSession?.mode === "REAL_SIGNAL" ? `REAL 신호 전달 ${realSignalLabel(data?.paperSession?.real_signal_state)} · ${data?.paperSession?.real_signal_error ?? "오류 없음"}` : `후보 갱신 ${candidateRefreshLabel(data?.paperSession?.candidate_refresh_by_market)} · paper 자동매매 ${automationLabel(data?.paperSession?.automation_state)} · ${baselineLabel(data?.paperSession?.daily_baseline)} · ${data?.paperSession?.paper_reconciliation_required ? `주문 대조 필요 ${data?.paperSession?.paper_unknown_idempotency_key ?? "멱등키 미수신"}` : "주문 대조 필요 없음"}`}`} />
    </section>
    <section className="panel lower"><div className="panel-title"><div><p className="eyebrow">P7 WINDOWS OPERATIONS</p><h2>예약 실행 및 로그인 상태</h2></div><span className="tag">{localReportFresh ? "최근 운영 보고서" : "보고서 확인 필요"}</span></div>
      {localReport ? <><div className="risk-list"><RiskRow label="로컬 서비스 자동기동" value={`${localReport.localStackTask?.state ?? "확인 불가"} · ${localReport.localStackTask?.principalLogonType ?? "UNKNOWN"}${localReport.localStackTask?.requiresInteractiveLogon ? " · 로그인 필요" : ""}`} /><RiskRow label="국내 / 미국 세션 감시" value={`${localReport.sessionMonitorSchedule?.krRegisteredCount ?? 0} / ${localReport.sessionMonitorSchedule?.usRegisteredCount ?? 0}개 · ${localReport.sessionMonitorSchedule?.state ?? "확인 불가"}`} /><RiskRow label="모의 주문 / AI 검증" value={`${localReport.validationTaskSchedule?.paperLifecycleCount ?? 0} / ${(localReport.validationTaskSchedule?.modelTrainingCount ?? 0) + (localReport.validationTaskSchedule?.modelHoldoutCount ?? 0)}개 · ${localReport.validationTaskSchedule?.state ?? "확인 불가"}`} /><RiskRow label="로그인 필요 작업" value={`${interactiveTasks}개 · 로그아웃 중 실행 보장 안 됨`} /></div>
        {scheduledTasks.length ? <div className="table-wrap"><table><thead><tr><th>작업</th><th>시장</th><th>상태</th><th>로그인</th><th>다음 실행</th></tr></thead><tbody>{scheduledTasks.slice(0, 8).map((task) => <tr key={task.kind + task.market + task.symbol + task.taskDate}><td>{scheduledTaskLabel(task.kind)}{task.symbol ? ` · ${task.symbol}` : ""}</td><td>{task.market ?? "—"}</td><td>{task.enabled ? task.state : "사용 안 함"}</td><td>{task.requiresInteractiveLogon === true ? "필요" : task.requiresInteractiveLogon === false ? "불필요" : "미확인"}</td><td>{task.nextRunTime ? time(task.nextRunTime) : "—"}</td></tr>)}</tbody></table></div> : <Empty text="등록된 세션 감시·검증 예약작업이 없습니다." />}
        <p className="audit-note">보고서 시각 {time(localReport.generatedAt)} · 사용자 로그인 세션이 필요한 작업은 로그아웃 중 실행되지 않을 수 있습니다. 이 패널은 상태 조회만 수행합니다.</p>
      </> : <Empty text="Windows 작업 스케줄러 상태를 포함한 일일 운영 보고서를 생성하면 여기에 표시됩니다." />}
    </section>
    <section className="metric-grid">
      <Metric title="미체결 주문" value={ops?.openOrders ?? "—"} caption="미체결·부분체결·확인 필요" />
      <Metric title="REAL 확인 필요" value={ops?.realOrderCounts?.UNKNOWN ?? 0} caption="자동 재전송 없이 대조 대기" />
      <Metric title="오늘 모의 체결" value={ops?.fillsToday ?? "—"} caption="한국 시간 기준" />
      <Metric title="오늘 실현 손익 (총액)" value={`${money(ops?.realizedPnlTodayGrossByCurrency?.KRW ?? 0, "KRW")} · ${money(ops?.realizedPnlTodayGrossByCurrency?.USD ?? 0, "USD")}`} caption="수수료·세금 차감 전" />
      <Metric title="오늘 실현 손익 (비용 후)" value={`${money(ops?.realizedPnlTodayNetByCurrency?.KRW ?? 0, "KRW")} · ${money(ops?.realizedPnlTodayNetByCurrency?.USD ?? 0, "USD")}`} caption={ops?.paperCostModelEnabled ? "입력된 paper 비용률 반영" : "비용 모형 비활성 · 총액과 동일"} />
      <Metric title="오늘 paper 거래비용" value={`${money(ops?.paperTransactionCostsTodayByCurrency?.KRW ?? 0, "KRW")} · ${money(ops?.paperTransactionCostsTodayByCurrency?.USD ?? 0, "USD")}`} caption={ops?.paperCostModelEnabled ? "체결 시 기록된 추정비용" : "비용 모형 비활성"} />
      <Metric title="보유 원가 합계" value={`${money(ops?.positionCostBasisByCurrency?.KRW ?? 0, "KRW")} · ${money(ops?.positionCostBasisByCurrency?.USD ?? 0, "USD")}`} caption="통화별 취득 원가 · 시장 평가액 아님" />
    </section>
    <section className="panel signals-panel"><div className="panel-title"><div><p className="eyebrow">P7 PAPER PERFORMANCE</p><h2>종목별 누적 실현 성과</h2></div><span className="tag">모의 장부 · 비용 반영</span></div>
      {ops?.paperPerformanceByInstrument?.length ? <div className="table-wrap"><table><thead><tr><th>시장</th><th>종목</th><th>전략</th><th>AI 모델</th><th>매도 주문</th><th>승률</th><th>승 / 패</th><th>누적 순손익</th><th>기대손익 / 주문</th><th>손익비</th><th>실현 하락폭</th></tr></thead><tbody>{ops.paperPerformanceByInstrument.map(item => <tr key={`${item.market}:${item.symbol}:${item.currency}:${item.strategyId}:${item.modelId}`}><td>{item.market}</td><td className="symbol">{item.symbol}</td><td>{item.strategyId ?? "미분류"}</td><td>{item.modelId ?? "미분류"}</td><td>{item.realizedSellOrders}</td><td>{percent(Number(item.winRate))}</td><td>{item.wins} / {item.losses}</td><td>{money(item.netPnl, item.currency)}</td><td>{money(item.expectancy, item.currency)}</td><td>{item.profitFactor == null ? "—" : Number(item.profitFactor).toFixed(2)}</td><td>{money(item.maxRealizedDrawdown, item.currency)}</td></tr>)}</tbody></table></div> : <Empty text="실현 매도 체결이 쌓이면 종목별 PAPER 성과가 표시됩니다." />}
      <p className="audit-note">매수 체결의 평균 원가와 체결비용으로 계산하고, 청산 주문에 보존된 전략·모델 ID로 분류합니다. 기대손익은 매도 주문별 평균 순손익, 손익비는 총이익÷총손실, 실현 하락폭은 누적 실현 손익 경로의 최대 고점 대비 하락액입니다. 미분류·과거 기록은 구분되며, 실계좌 수익이나 평가손익을 뜻하지 않습니다.</p>
    </section>
    <section className="panel signals-panel"><div className="panel-title"><div><p className="eyebrow">P5 STRATEGY PREVIEW</p><h2>종목별 판단 상태</h2></div><span className="tag">paper 진입 게이트 · 실계좌 잠금</span></div>
      {Object.keys(data?.paperSession?.decisions ?? {}).length ? <div className="table-wrap"><table><thead><tr><th>시장</th><th>종목</th><th>판단</th><th>판단 사유</th><th>수신 분봉</th><th>저장 품질</th></tr></thead><tbody>{Object.entries(data?.paperSession?.decisions ?? {}).map(([key, item]) => { const quality = qualityBySymbol.get(`${item.market}:${item.symbol}`); return <tr key={key}><td>{item.market}</td><td className="symbol">{item.symbol}{item.exchange ? ` · ${item.exchange}` : ""}</td><td>{signalLabel(item.signal)}</td><td>{decisionReason(item.reason)}</td><td>{item.bars_in_session}</td><td>{quality ? `${quality.status} · ${quality.observed_bars}/${quality.expected_bars} · spread ${quality.spread_coverage_percent ?? "—"}%` : "판정 불가"}</td></tr>; })}</tbody></table></div> : <Empty text="세션 모니터가 비활성이거나 아직 종목을 구독하지 않았습니다." />}
    </section>
    <section className="two-col">
      <div className="panel"><div className="panel-title"><div><p className="eyebrow">PAPER ACCOUNT</p><h2>가상 현금</h2></div><span className="tag">실계좌 잔고 아님</span></div>
        {ops?.cashBalances.length ? <div className="cash-list">{ops.cashBalances.map(item => <div className="cash-row" key={item.currency}><span>{item.currency}</span><strong>{money(item.balance, item.currency)}</strong></div>)}</div> : <Empty text="가상 현금 잔고가 아직 없습니다." />}
      </div>
      <div className="panel"><div className="panel-title"><div><p className="eyebrow">RISK SNAPSHOT</p><h2>운영 제한</h2></div><span className="tag green">실거래 주문 잠금</span></div>
        <div className="risk-list"><RiskRow label="운영 모드" value={ops?.tradingMode.toUpperCase() ?? "PAPER"} /><RiskRow label="실거래 허용" value={ops?.liveTrading.toUpperCase() ?? "DISABLED"} /><RiskRow label="KIS 실주문 스위치" value={ops?.kisRealOrdersEnabled ? "설정 켜짐" : "잠금"} /><RiskRow label="KIS 실계좌 조회" value={ops?.kisRealAccountReadsEnabled ? "사용" : "잠금"} /><RiskRow label="REAL 체결 자동대조" value={ops?.kisRealReconciliationEnabled ? "설정 켜짐" : "잠금"} /><RiskRow label="보호청산 준비" value={ops?.realProtectiveExitManagerEnabled ? "설정 켜짐" : "잠금"} /><RiskRow label="REAL 자동 보호매도" value={ops?.kisRealProtectiveExitAutomationEnabled ? "설정 켜짐" : "잠금"} /><RiskRow label="REAL 비용률 형식 KR / US" value={`${ops?.kisRealKrCostsConfigured ? "유효" : "미설정"} / ${ops?.kisRealUsCostsConfigured ? "유효" : "미설정"}`} /><RiskRow label="KIS 공통 계좌 조회" value={ops?.kisAccountReadsEnabled ? "사용" : "잠금"} /><RiskRow label="KIS 모의 주문" value={ops?.kisPaperOrdersEnabled ? "설정 켜짐" : "잠금"} /><RiskRow label="국내 PAPER 취소" value={ops?.kisPaperDomesticCancelSupported ? "지원 확인" : "미지원·잠금"} /><RiskRow label="paper 거래비용" value={ops?.paperCostModelEnabled ? "설정 적용" : "비활성"} /><RiskRow label="paper 자동매매" value={automationLabel(data?.paperSession?.automation_state)} /><RiskRow label="연속 손실 주문" value={`${ops?.consecutiveLosingTrades ?? 0}회`} /></div>
        <div className="risk-list"><RiskRow label="P3/P4 백테스트 비용 입력" value={`KR ${costStatus("KR")} · US ${costStatus("US")}`} /></div>
        {missingCostDetails && <p className="audit-note">누락 또는 확인할 `.env_local` 항목: {missingCostDetails}</p>}
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
    <section className="panel"><div className="panel-title"><div><p className="eyebrow">KIS PAPER ORDER INTENTS</p><h2>KIS 모의계좌 주문 상태</h2></div><span className="tag">증권사 주문 조회 · 재전송 없음</span></div>
      {ops?.recentPaperKisOrders?.length ? <div className="table-wrap"><table><thead><tr><th>시장·종목</th><th>구분</th><th>체결 / 주문</th><th>잔량</th><th>상태</th><th>응답 코드 / 진단</th><th>갱신</th></tr></thead><tbody>{ops.recentPaperKisOrders.map((o, index) => { const responseLabel = paperKisResponseLabel(o.status, o.responseCode); return <tr key={`${o.market}-${o.symbol}-${o.createdAt}-${index}`}><td><small>{o.market}</small><br /><span className="symbol">{o.symbol}</span></td><td className={o.side === "BUY" ? "buy" : "sell"}>{o.side === "BUY" ? "매수" : "매도"}</td><td>{o.filledQuantity ?? 0}/{o.quantity}</td><td>{o.remainingQuantity ?? "—"}</td><td><span className={`order-state ${o.status.toLowerCase()}`}>{statusLabel(o.status)}</span></td><td title={responseLabel === "원인 미확인" ? "저장된 KIS 응답 코드가 없어 거절 원인을 확인할 수 없습니다." : undefined}>{responseLabel}</td><td>{time(o.updatedAt)}</td></tr>; })}</tbody></table></div> : <Empty text="KIS 모의 주문 의도가 아직 없습니다." />}
      <p className="audit-note">KIS PAPER 계좌 주문 요청·대조 기록입니다. 내부 모의 장부 주문과 별도이며 이 대시보드에서는 주문·취소를 전송하지 않습니다. 응답 코드가 보존되지 않은 거절과 취소 거절·불명 상태는 증권사 내역을 읽기 전용으로 대조하기 전까지 재전송하지 않습니다.</p>
    </section>
    <section className="panel"><div className="panel-title"><div><p className="eyebrow">REAL ORDER INTENTS</p><h2>실계좌 주문 의도</h2></div><span className="tag">주문별 상태 · 조회 전용</span></div>
      {ops?.recentRealOrders?.length ? <div className="table-wrap"><table><thead><tr><th>시장·종목</th><th>구분</th><th>체결 / 주문</th><th>잔량</th><th>상태</th><th>갱신</th></tr></thead><tbody>{ops.recentRealOrders.map((o, index) => <tr key={`${o.market}-${o.symbol}-${o.createdAt}-${index}`}><td><small>{o.market}</small><br /><span className="symbol">{o.symbol}</span></td><td className={o.side === "BUY" ? "buy" : "sell"}>{o.side === "BUY" ? "매수" : "매도"}</td><td>{o.filledQuantity ?? 0}/{o.quantity}</td><td>{o.remainingQuantity ?? "—"}</td><td><span className={`order-state ${o.status.toLowerCase()}`}>{statusLabel(o.status)}</span></td><td>{time(o.updatedAt)}</td></tr>)}</tbody></table></div> : <Empty text="저장된 실계좌 주문 의도가 없습니다. 이 상태 표시는 계좌 잔고와 별도입니다." />}
      <div className="panel-title"><div><p className="eyebrow">PROTECTIVE EXIT PLANS</p><h2>보호청산 계획</h2></div><span className="tag">감시 상태 · 조회 전용</span></div>
      {ops?.realExitPlans?.length ? <div className="table-wrap"><table><thead><tr><th>시장·종목</th><th>상태</th><th>사유</th><th>진입 체결</th><th>청산 수량</th><th>갱신</th></tr></thead><tbody>{ops.realExitPlans.map((plan, index) => <tr key={`${plan.market}-${plan.symbol}-${plan.updatedAt}-${index}`}><td><small>{plan.market}</small><br /><span className="symbol">{plan.symbol}</span></td><td><span className={`order-state ${plan.status === "MANUAL_REVIEW" ? "unknown" : plan.status.toLowerCase()}`}>{realExitStatusLabel(plan.status)}</span></td><td>{realExitReasonLabel(plan.reason)}</td><td>{plan.entryFilledQuantity ?? 0}</td><td>{plan.exitQuantity ?? "—"}</td><td>{time(plan.updatedAt)}</td></tr>)}</tbody></table></div> : <Empty text="진행 중인 REAL 보호청산 계획이 없습니다." />}
      <p className="audit-note">수동 확인 상태는 운영자가 증권사 주문·잔고를 확인해야 합니다. 이 목록은 저장된 계획 상태이며 실계좌 보유 잔고를 대신하지 않습니다.</p>
    </section>
    <section className="panel models-panel"><div className="panel-title"><div><p className="eyebrow">MODEL GOVERNANCE</p><h2>AI 모델 승인 및 롤백</h2></div><span className="tag">주문 실행과 분리</span></div>
      <p className="model-note">모델 승인과 활성화는 시장별 paper 진입 후보 평가에 사용됩니다. AI 신호만으로 주문하지 않고, Spring paper 위험 게이트를 통과해야 로컬 모의 장부에 반영됩니다. 실거래 실행 여부는 위 안전 스위치에서 확인하며, 이 화면은 주문 권한이 없습니다.</p>
      <p className="audit-note">승인 감사 이력 무결성: {data?.aiModels?.governance_audit?.integrity_verified === true ? `확인됨 · ${data.aiModels.governance_audit.event_count}건` : data?.aiModels?.governance_audit?.integrity_verified === false ? "검증 실패 · 모델 승인이 잠겨야 합니다" : "확인 불가"}</p>
      {data?.aiModels?.models.length ? <div className="model-grid">{data.aiModels.models.map(model => { const currency = model.market === "US" ? "USD" : "KRW"; const aiBacktest = model.ai_quant_backtest_test_metrics; const quantBacktest = model.quant_backtest_test_metrics; return <article className="model-card" key={model.model_id}><div className="model-top"><strong>{model.market ?? "시장 미지정"} · {model.model_type ?? model.model_id.split("-")[0]}</strong><div>{model.active && <span className="tag green">ACTIVE</span>}<span className={`tag ${model.approved ? "green" : ""}`}>{model.approved ? "승인" : model.integrity_error ? "검증 오류" : "미승인"}</span></div></div><code>{model.model_id}</code>{model.data_rights_verified !== true && <p className="audit-note">데이터 권리 확인 자료가 없어 승인과 활성화를 막고 있습니다.</p>}{model.approval_evidence_verified === false && <p className="audit-note">승인 요건 미충족: 미사용 세션·AI+퀀트 체결 수·퀀트 대비 순손익을 확인해야 합니다.</p>}<div className="model-meta"><span>미사용 테스트 정확도</span><b>{percent(model.test_metrics?.accuracy)}</b></div><div className="model-meta"><span>AI+퀀트 후보 양성률 / 비중</span><b>{percent(model.test_metrics?.candidate_positive_rate)} · {percent(model.test_metrics?.candidate_coverage)}</b></div><div className="model-meta"><span>AI+퀀트 후보 평균 비용후 전방수익</span><b>{basisPoints(model.test_metrics?.candidate_mean_forward_net_bps)}</b></div><div className="model-meta"><span>퀀트 기준선 양성률 / 비중</span><b>{percent(model.quant_baseline_test_metrics?.candidate_positive_rate)} · {percent(model.quant_baseline_test_metrics?.candidate_coverage)}</b></div><div className="model-meta"><span>퀀트 후보 평균 비용후 전방수익</span><b>{basisPoints(model.quant_baseline_test_metrics?.candidate_mean_forward_net_bps)}</b></div><div className="model-meta"><span>미사용 세션 AI+퀀트 백테스트</span><b>{aiBacktest ? `${Math.round(Number(aiBacktest.trade_count ?? 0))}회 · ${money(aiBacktest.net_pnl ?? 0, currency)}` : "—"}</b></div><div className="model-meta"><span>미사용 세션 퀀트 기준선</span><b>{quantBacktest ? `${Math.round(Number(quantBacktest.trade_count ?? 0))}회 · ${money(quantBacktest.net_pnl ?? 0, currency)}` : "—"}</b></div><div className="model-meta"><span>학습·백테스트 가정</span><b>{trainingConfig(model.training_config)}</b></div><div className="model-meta"><span>입력 데이터 SHA-256</span><b title={model.dataset_sha256}>{model.dataset_sha256?.slice(0, 16) ?? "이전 모델 · 미기록"}</b></div><div className="model-meta"><span>모델 파일 SHA-256</span><b title={model.artifact_sha256}>{model.artifact_sha256?.slice(0, 16) ?? "—"}</b></div>{model.feature_compatible === false && <p className="audit-note">이 모델은 현재 피처 버전과 호환되지 않습니다. 재학습이 필요합니다.</p>}</article>; })}</div> : <Empty text={pythonConnected ? "등록된 AI 모델이 없습니다." : "Python AI 서비스 연결 후 모델 현황을 표시합니다."} />}
      <p className="audit-note">후보 양성률과 평균 전방수익은 거래비용 가정을 반영한 표본별 레이블 통계이며, 겹치는 보유 구간이 있을 수 있습니다. 백테스트는 미사용 세션별 1주 독립 실행으로 포트폴리오 자본 복리, 계좌별 수수료·세금, 시장 충격은 반영하지 않습니다. 두 결과 모두 실계좌 체결 수익이나 수익 보장이 아니며, 승인·활성화 판단은 별도 검토가 필요합니다.</p>
    </section>
    <footer><span>AI STOCK TRADER · LOCAL ONLY</span><span>데이터 시각 {data ? time(data.generatedAt) : "—"} · 30초마다 자동 새로고침</span></footer>
  </main>;
}

function StatusCard({ title, good, value, detail }: { title: string; good: boolean; value: string; detail: string }) { return <article className="status-card"><div className="card-head"><span>{title}</span><i className={good ? "dot good" : "dot bad"} /></div><strong>{value}</strong><small>{detail}</small></article>; }
function Metric({ title, value, caption }: { title: string; value: string | number; caption: string }) { return <article className="metric"><span>{title}</span><strong>{value}</strong><small>{caption}</small></article>; }
function RiskRow({ label, value }: { label: string; value: string }) { return <div className="risk-row"><span>{label}</span><strong>{value}</strong></div>; }
function Empty({ text }: { text: string }) { return <div className="empty">{text}</div>; }
function statusLabel(value: string) { return ({ PENDING: "대기", PARTIALLY_FILLED: "부분체결", FILLED: "체결", REJECTED: "거부", CANCEL_PENDING: "취소 확인", CANCELLED: "취소", EXPIRED: "만료", UNKNOWN: "확인 필요" } as Record<string, string>)[value] ?? value; }
function paperKisResponseLabel(status: string, responseCode: string | null) {
  if (status === "REJECTED" && (!responseCode || responseCode === "rejected")) return "원인 미확인";
  return responseCode ?? "—";
}
function scheduledTaskLabel(value: string) { return ({ PAPER_LIFECYCLE: "미국 모의 주문·취소", KRX_SESSION_MONITOR: "국내장 시세 감시", US_SESSION_MONITOR: "미국장 시세 감시", P4_MODEL_TRAIN: "AI 모델 재학습", P4_MODEL_HOLDOUT: "AI 모델 미사용 검증" } as Record<string, string>)[value] ?? value; }
function realExitStatusLabel(value: string) { return ({ WAITING_FOR_ENTRY: "진입 체결 대기", ARMED: "감시 중", EXIT_PENDING: "청산 주문 처리 중", MANUAL_REVIEW: "수동 확인 필요" } as Record<string, string>)[value] ?? value; }
function realExitReasonLabel(value: string | null) { return ({ STOP_LOSS: "손절", TAKE_PROFIT: "익절", SESSION_END: "장 마감" } as Record<string, string>)[value ?? ""] ?? "—"; }
function sessionLabel(value?: string) { return ({ RUNNING: "시세 수집 중", PARTIAL: "일부 feed만 연결", CONNECTING: "연결 중", RECONNECTING: "재연결 대기", BLOCKED: "설정 확인 필요", STOPPED: "중지", DISABLED: "비활성" } as Record<string, string>)[value ?? ""] ?? "확인 중"; }
function calendarLabel(value?: string) { return ({ COMPATIBLE: "호환", PANDAS_UNSUPPORTED: "pandas 버전 확인 필요", EXCHANGE_CALENDAR_UNSUPPORTED: "캘린더 패키지 확인 필요", DEPENDENCIES_MISSING: "의존성 미설치", VERSION_UNKNOWN: "버전 확인 불가" } as Record<string, string>)[value ?? ""] ?? "상태 미수신"; }
function marketSessionLabel(value?: MarketSession | null) { return !value ? "판정 불가" : !value.is_session ? "휴장" : value.is_open ? "장중" : "장외"; }
function streamFeedLabel(value?: Record<string, string>, mode?: string) {
  const feeds = Object.entries(value ?? {});
  if (!feeds.length) return "feed 미설정";
  if (mode === "REAL_SIGNAL") return feeds.map(([, state]) => `REAL 시세 ${sessionLabel(state)}`).join(" · ");
  return feeds.map(([environment, state]) => `${environment === "paper" ? "국내 PAPER" : "미국 REAL"} ${sessionLabel(state)}`).join(" · ");
}
function subscriptionAckLabel(value?: Record<string, { accepted_count: number; required_count: number; complete: boolean }>, mode?: string) {
  const feeds = Object.entries(value ?? {});
  if (!feeds.length) return "구독 승인 확인 중";
  return feeds.map(([environment, status]) => {
    const market = mode === "REAL_SIGNAL" || environment === "real" ? "REAL" : "PAPER";
    const label = status.complete ? "승인" : "대기";
    return `${market} 구독 ${label} ${status.accepted_count}/${status.required_count}`;
  }).join(" · ");
}
function streamFrameDiagnosticsLabel(
  totals?: Record<string, number>,
  transactions?: Record<string, Record<string, number>>,
) {
  if (!totals || !transactions) return "수신 프레임 진단 적용 대기";
  const feeds = Object.entries(totals);
  if (!feeds.length) return "수신 프레임 없음";
  return feeds.map(([feed, total]) => {
    const counts = transactions[feed] ?? {};
    return `${feed === "paper" ? "PAPER" : "REAL"} 프레임 ${total}회 · 국내 체결/호가 ${counts.H0STCNT0 ?? 0}/${counts.H0STASP0 ?? 0} · 미국 체결 ${counts.HDFSCNT0 ?? 0} · 기타 ${counts.OTHER ?? 0}`;
  }).join(" / ");
}
function signalLabel(value: string) { return value === "BUY_CANDIDATE" ? "AI·퀀트 후보" : "관망"; }
function decisionReason(value: string) { return ({ WAITING_FOR_CLOSED_BARS: "닫힌 분봉 대기", INSUFFICIENT_SESSION_HISTORY: "세션 이력 부족", STRATEGY_FILTERS_NOT_MET: "퀀트 조건 미충족", AI_MODEL_NOT_APPROVED_FOR_MARKET: "시장 승인 모델 없음", AI_THRESHOLD_NOT_MET: "AI 기준 미달", AI_INFERENCE_UNAVAILABLE: "AI 추론 불가", BAR_SEQUENCE_INVALID: "분봉 순서 오류", QUANT_AND_APPROVED_AI_CANDIDATE: "퀀트 및 승인 AI 통과" } as Record<string, string>)[value] ?? value; }
function automationLabel(value?: string) { return ({ EXIT_MONITORING: "감시 중", BASELINE_CAPTURED: "기준선 저장", BASELINE_PRESENT: "기준선 확인", FILLED: "paper 체결", PENDING: "paper 주문 대기", UNKNOWN: "확인 필요", BLOCKED: "차단", DISABLED: "잠금" } as Record<string, string>)[value ?? ""] ?? "잠금"; }
function realSignalLabel(value?: string) { return ({ READY: "준비", QUEUED: "전송 대기", SUBMITTED: "Spring 접수", REJECTED: "거부", LOCKED_BY_SAFETY_GATES: "안전 잠금", LOCKED_KIS_REST_SETTINGS: "REAL REST 시세 잠금", BLOCKED_QUEUE_FULL: "큐 초과·중지", BLOCKED_CLIENT_UNAVAILABLE: "클라이언트 오류", BLOCKED_SPRING_REJECTED: "Spring 거부·중지", MANUAL_RECONCILIATION_REQUIRED: "결과 대조 필요" } as Record<string, string>)[value ?? ""] ?? "비활성"; }
function candidateRefreshLabel(value?: Record<string, CandidateRefresh>) {
  const statuses = Object.entries(value ?? {});
  if (!statuses.length) return "상태 미기록";
  return statuses.map(([market, status]) => {
    const label = ({ WAITING: "개장 전 대기", WAITING_FOR_FEED: "시세 연결 대기", COMPLETED: "완료", SKIPPED_LATE_START: "늦은 기동으로 건너뜀", SKIPPED_FEED_NOT_READY: "feed 미준비로 건너뜀", BLOCKED: "안전 잠금", FAILED: "갱신 실패" } as Record<string, string>)[status.state] ?? status.state;
    const count = status.selected == null ? "" : ` · ${status.selected}종목 선택`;
    const session = status.session_open ? ` · ${status.session_open.slice(0, 10)}` : "";
    return `${market} ${label}${count}${session}`;
  }).join(" / ");
}
function baselineLabel(value?: { state?: string; currency?: string; equity?: string; reason?: string } | null) {
  if (!value) return "일일 기준선 대기";
  if (value.state === "BASELINE_CAPTURED") return `기준선 ${value.currency ?? "통화"} 저장`;
  if (value.state === "BASELINE_PRESENT") return `기준선 ${value.currency ?? "통화"} 확인`;
  if (value.state === "BLOCKED") return `기준선 차단 · ${value.reason ?? "확인 필요"}`;
  return "일일 기준선 대기";
}
