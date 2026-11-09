import { NextResponse } from "next/server";
import { readdir, readFile, stat } from "node:fs/promises";
import { resolve, sep } from "node:path";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

type ScheduledTask = {
  kind: string;
  market: string | null;
  symbol: string | null;
  taskDate: string;
  state: string;
  enabled: boolean;
  principalLogonType: string;
  requiresInteractiveLogon: boolean | null;
  wakeToRun: boolean;
  startWhenAvailable: boolean;
  nextRunTime: string | null;
  lastRunTime: string | null;
  lastTaskResult: number | null;
};

type LocalOperationsReport = {
  generatedAt: string;
  reportSchemaVersion: number;
  localStackTask: {
    state: string;
    principalLogonType: string;
    requiresInteractiveLogon: boolean | null;
    enabled: boolean | null;
    nextRunTime: string | null;
    lastRunTime: string | null;
    lastTaskResult: number | null;
  } | null;
  sessionMonitorSchedule: {
    state: string;
    registeredCount: number;
    krRegisteredCount: number;
    usRegisteredCount: number;
    tasks: ScheduledTask[];
  } | null;
  domesticSessionMonitor: {
    state: string;
    recordedAt: string | null;
    sessionDate: string | null;
    validationScope: string | null;
    sessionCloseReached: boolean | null;
    sessionQualityStatus: string | null;
    ticksDelta: number | null;
    barsDelta: number | null;
    orderbookFramesDelta: number | null;
    orderbookUpdatesDelta: number | null;
    maxNoTickSeconds: number | null;
    failureStage: string | null;
    failureCode: string | null;
  } | null;
  validationTaskSchedule: {
    state: string;
    registeredCount: number;
    paperLifecycleCount: number;
    modelTrainingCount: number;
    modelHoldoutCount: number;
    tasks: ScheduledTask[];
  } | null;
};

const TRAINING_READINESS_CACHE_MS = 5 * 60 * 1000;
const trainingReadinessCache = new Map<string, { expiresAt: number; value: unknown }>();
const trainingReadinessInFlight = new Map<string, Promise<unknown | null>>();

function asObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function scheduledTasks(value: unknown): ScheduledTask[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const task = asObject(entry);
    if (!task || typeof task.state !== "string") return [];
    const market = optionalString(task.market);
    const kind = typeof task.kind === "string"
      ? task.kind
      : market === "KR" ? "KRX_SESSION_MONITOR"
        : market === "US" ? "US_SESSION_MONITOR" : null;
    if (!kind) return [];
    return [{
      kind,
      market,
      symbol: optionalString(task.symbol),
      taskDate: typeof task.taskDate === "string"
        ? task.taskDate
        : typeof task.taskName === "string" ? task.taskName.match(/\d{8}$/)?.[0] ?? "" : "",
      state: task.state,
      enabled: task.enabled === true,
      principalLogonType: typeof task.principalLogonType === "string" ? task.principalLogonType : "UNKNOWN",
      requiresInteractiveLogon: optionalBoolean(task.requiresInteractiveLogon),
      wakeToRun: task.wakeToRun === true,
      startWhenAvailable: task.startWhenAvailable === true,
      nextRunTime: optionalString(task.nextRunTime),
      lastRunTime: optionalString(task.lastRunTime),
      lastTaskResult: optionalNumber(task.lastTaskResult),
    }];
  });
}

function schedule(value: unknown, countKeys: string[]): Record<string, unknown> | null {
  const raw = asObject(value);
  if (!raw || typeof raw.state !== "string") return null;
  const result: Record<string, unknown> = {
    state: raw.state,
    registeredCount: optionalNumber(raw.registeredCount) ?? 0,
    tasks: scheduledTasks(raw.tasks),
  };
  for (const key of countKeys) result[key] = optionalNumber(raw[key]) ?? 0;
  return result;
}

async function fetchTrainingReadiness(pythonBase: string, market: string, symbol: string): Promise<unknown | null> {
  const key = `${pythonBase}:${market}:${symbol}`;
  const cached = trainingReadinessCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const pending = trainingReadinessInFlight.get(key);
  if (pending) return pending;
  const request = (async () => {
    try {
      const response = await fetch(
        `${pythonBase}/api/v1/market/training-readiness/${market}/${encodeURIComponent(symbol)}`,
        { cache: "no-store", signal: AbortSignal.timeout(5000) },
      );
      if (!response.ok) return null;
      const value = await response.json();
      trainingReadinessCache.set(key, { expiresAt: Date.now() + TRAINING_READINESS_CACHE_MS, value });
      return value;
    } catch {
      return null;
    } finally {
      trainingReadinessInFlight.delete(key);
    }
  })();
  trainingReadinessInFlight.set(key, request);
  return request;
}

async function readLocalOperationsReport(): Promise<LocalOperationsReport | null> {
  const directories = [
    resolve(process.cwd(), ".local", "reports"),
    resolve(process.cwd(), "..", ".local", "reports"),
  ];
  for (const directory of directories) try {
    const entries = await readdir(/* turbopackIgnore: true */ directory, { withFileTypes: true });
    const candidates = await Promise.all(entries
      .filter((entry) => entry.isFile() && /^operations(?:-run)?-[A-Za-z0-9T._-]+\.json$/i.test(entry.name))
      .map(async (entry) => {
        const file = resolve(directory, entry.name);
        if (!file.startsWith(`${directory}${sep}`)) return null;
        const info = await stat(/* turbopackIgnore: true */ file);
        return info.size <= 5_000_000 ? { file, modifiedAt: info.mtimeMs } : null;
      }));
    const latest = candidates.filter((item): item is { file: string; modifiedAt: number } => item !== null)
      .sort((left, right) => right.modifiedAt - left.modifiedAt)[0];
    if (!latest) continue;

    const reportText = (await readFile(/* turbopackIgnore: true */ latest.file, "utf8")).replace(/^\uFEFF/, "");
    const raw = asObject(JSON.parse(reportText));
    if (!raw || typeof raw.generatedAt !== "string" || !Number.isFinite(Date.parse(raw.generatedAt))) return null;
    const schema = optionalNumber(raw.reportSchemaVersion);
    if (schema === null || schema < 10) return null;

    const stack = asObject(raw.localStackTask);
    const localStackTask = stack && typeof stack.state === "string" ? {
      state: stack.state,
      principalLogonType: typeof stack.principalLogonType === "string" ? stack.principalLogonType : "UNKNOWN",
      requiresInteractiveLogon: optionalBoolean(stack.requiresInteractiveLogon),
      enabled: optionalBoolean(stack.enabled),
      nextRunTime: optionalString(stack.nextRunTime),
      lastRunTime: optionalString(stack.lastRunTime),
      lastTaskResult: optionalNumber(stack.lastTaskResult),
    } : null;
    const rawMonitor = asObject(raw.domesticSessionMonitor);
    const domesticSessionMonitor = rawMonitor && typeof rawMonitor.state === "string" ? {
      state: rawMonitor.state,
      recordedAt: optionalString(rawMonitor.recordedAt),
      sessionDate: optionalString(rawMonitor.sessionDate),
      validationScope: optionalString(rawMonitor.validationScope),
      sessionCloseReached: optionalBoolean(rawMonitor.sessionCloseReached),
      sessionQualityStatus: optionalString(rawMonitor.sessionQualityStatus),
      ticksDelta: optionalNumber(rawMonitor.ticksDelta),
      barsDelta: optionalNumber(rawMonitor.barsDelta),
      orderbookFramesDelta: optionalNumber(rawMonitor.orderbookFramesDelta),
      orderbookUpdatesDelta: optionalNumber(rawMonitor.orderbookUpdatesDelta),
      maxNoTickSeconds: optionalNumber(rawMonitor.maxNoTickSeconds),
      failureStage: optionalString(rawMonitor.failureStage),
      failureCode: optionalString(rawMonitor.failureCode),
    } : null;

    return {
      generatedAt: raw.generatedAt,
      reportSchemaVersion: schema,
      localStackTask,
      sessionMonitorSchedule: schedule(raw.sessionMonitorSchedule, ["krRegisteredCount", "usRegisteredCount"]) as LocalOperationsReport["sessionMonitorSchedule"],
      domesticSessionMonitor,
      validationTaskSchedule: schedule(raw.validationTaskSchedule, ["paperLifecycleCount", "modelTrainingCount", "modelHoldoutCount"]) as LocalOperationsReport["validationTaskSchedule"],
    };
  } catch {
    continue;
  }
  return null;
}

export async function GET() {
  const springBase = process.env.SPRING_API_BASE_URL ?? "http://127.0.0.1:18080";
  const pythonBase = process.env.PYTHON_API_BASE_URL ?? "http://127.0.0.1:18000";
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
  const localOperationsReport = await readLocalOperationsReport();
  const trainingTasks = (localOperationsReport?.validationTaskSchedule?.tasks ?? [])
    .filter((task) => task.kind === "P4_MODEL_TRAIN" && task.market === "KR"
      && task.enabled && task.symbol);
  const trainingPairs = [...new Map(trainingTasks.map((task) => [`${task.market}:${task.symbol}`, task])).values()];
  const trainingReadinessRows = await Promise.all(trainingPairs.map(async (task) => {
    const key = `${task.market}:${task.symbol}`;
    return [key, await fetchTrainingReadiness(pythonBase, task.market ?? "", task.symbol ?? "")] as const;
  }));
  const decisions = paperSessionResult && typeof paperSessionResult === "object"
    ? (paperSessionResult as { decisions?: Record<string, { market?: string; symbol?: string }> }).decisions ?? {}
    : {};
  const qualityByMarket: Record<string, unknown> = { KR: null, US: null };
  for (const market of ["KR", "US"] as const) {
    const symbols = [...new Set(Object.values(decisions)
      .filter((item): item is { market?: string; symbol: string } =>
        item.market === market && typeof item.symbol === "string")
      .map((item) => item.symbol.trim().toUpperCase())
      .filter(Boolean))];
    if (!symbols.length || symbols.length > 50) continue;
    const query = new URLSearchParams({ latest_closed: "true" });
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
    localOperationsReport,
    trainingReadiness: Object.fromEntries(trainingReadinessRows),
  }, { headers: { "Cache-Control": "no-store" } });
}
