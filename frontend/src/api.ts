export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL
  ?? (import.meta.env.DEV ? "http://127.0.0.1:8000/api" : "/api");

export type RuntimeState = "IDLE" | "STARTING" | "RUNNING" | "PAUSED" | "STOPPING" | "STOPPED" | "ERROR";
export type TerminalResult = "completed" | "stopped" | "failed" | null;
export type JsonRecord = Record<string, unknown>;

export interface Telemetry {
  phase: string;
  gameScreen: string;
  screenConfidence: number | null;
  screenDetails: JsonRecord | null;
  screenshotPath: string | null;
  debugArtifactPaths: string[];
  gold: number | null;
  elixir: number | null;
  darkElixir: number | null;
  decision: string | null;
  decisionReasons: string[];
  basesChecked: number;
  maxBases: number;
  nextTaps: number;
  ocrAttempts: number;
  unknownStateRetries: number;
  attackPlan: JsonRecord | null;
  strategy: string | null;
  dryRun: boolean | null;
  lastError: string | null;
  terminalResult: TerminalResult;
  terminalMessage: string | null;
  startedAt: string | null;
  updatedAt: string;
}

export interface Status extends Telemetry {
  runtimeState: RuntimeState;
}

export interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
}

export interface LogsResponse {
  entries: LogEntry[];
}

export type ConfigResponse = Record<string, string | number | boolean | string[]>;

export interface ConfigPatch {
  minimumGold?: number;
  minimumElixir?: number;
  minimumDarkElixir?: number;
  requireAllResources?: boolean;
  maxBasesToCheck?: number;
  maxRuntimeSeconds?: number;
  maxOcrAttemptsPerBase?: number;
  strategy?: "sneaky_goblin";
  dryRun?: boolean;
}

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init.headers },
      signal: controller.signal,
    });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : response.statusText;
      throw new ApiError(detail || "The backend rejected the request.", response.status);
    }
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("The backend request timed out.");
    }
    throw new ApiError("Backend unavailable. Check that the local FastAPI service is running.");
  } finally {
    window.clearTimeout(timeout);
  }
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  status: () => request<Status>("/status"),
  telemetry: () => request<Telemetry>("/telemetry"),
  logs: () => request<LogsResponse>("/logs?limit=100"),
  config: () => request<ConfigResponse>("/config"),
  updateConfig: (patch: ConfigPatch) => request<ConfigResponse>("/config", { method: "PUT", body: JSON.stringify(patch) }),
  start: () => request<JsonRecord>("/session/start", { method: "POST" }),
  pause: () => request<JsonRecord>("/session/pause", { method: "POST" }),
  resume: () => request<JsonRecord>("/session/resume", { method: "POST" }),
  stop: () => request<JsonRecord>("/session/stop", { method: "POST" }),
};

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "Awaiting signal";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
