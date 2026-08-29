import { useEffect, useRef, useState } from "react";
import { api, ApiError, formatValue, type ConfigPatch, type ConfigResponse, type HistorySessionDetail, type HistorySessionSummary, type LogEntry, type PreflightCheckStatus, type PreflightReport, type Status, type Telemetry } from "./api";

const EDITABLE_FIELDS: Array<{ key: keyof ConfigPatch; label: string; type: "number" | "boolean" | "select"; hint: string }> = [
  { key: "minimumGold", label: "Minimum Gold", type: "number", hint: "Required Gold before ATTACK." },
  { key: "minimumElixir", label: "Minimum Elixir", type: "number", hint: "Required Elixir before ATTACK." },
  { key: "minimumDarkElixir", label: "Minimum Dark Elixir", type: "number", hint: "Required Dark Elixir before ATTACK." },
  { key: "requireAllResources", label: "Require All Resources", type: "boolean", hint: "All resource thresholds must pass." },
  { key: "maxBasesToCheck", label: "Max Bases", type: "number", hint: "Bounded search limit." },
  { key: "maxRuntimeSeconds", label: "Max Runtime (seconds)", type: "number", hint: "Hard session time limit." },
  { key: "battlesPerSession", label: "Battles per Session", type: "select", hint: "Choose 1 for a quick test, or 5/10 consecutive attacks." },
  { key: "maxOcrAttemptsPerBase", label: "OCR Attempts / Base", type: "number", hint: "Retry limit for an unreadable base." },
  { key: "strategy", label: "Strategy", type: "select", hint: "Current supported strategy." },
  { key: "dryRun", label: "Dry Run", type: "boolean", hint: "When false, the bot can send real ADB taps." },
];

const ACTIVE_STATES = new Set(["STARTING", "RUNNING", "PAUSED", "STOPPING"]);

function formatTime(value: string | null | undefined): string {
  if (!value) return "Awaiting signal";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function statusTone(state: string): string {
  if (state === "ERROR") return "bad";
  if (state === "RUNNING") return "good";
  if (state === "PAUSED" || state === "STOPPING") return "warn";
  return "quiet";
}

function preflightTone(status: PreflightCheckStatus | "ready" | "blocked"): string {
  if (status === "ready" || status === "pass") return "good";
  if (status === "warning") return "warn";
  return "bad";
}

function resultTone(result: string | null): string {
  if (result === "completed") return "good";
  if (result === "stopped") return "warn";
  return "bad";
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "--";
  return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
}

function valueForField(config: ConfigResponse, key: keyof ConfigPatch): string | boolean {
  const value = config[key];
  return typeof value === "boolean" ? value : String(value ?? "");
}

const RECOMMENDED_RUNTIME_SECONDS: Record<number, number> = { 1: 180, 5: 900, 10: 1800 };

function ArtifactPath({ path }: { path: string }) {
  const [copied, setCopied] = useState(false);
  async function copyPath() {
    await navigator.clipboard?.writeText(path);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }
  return <div className="path-row"><code>{path}</code><button className="text-button" onClick={copyPath}>{copied ? "Copied" : "Copy"}</button></div>;
}

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [telemetry, setTelemetry] = useState<Telemetry | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [preflight, setPreflight] = useState<PreflightReport | null>(null);
  const [preflightRunning, setPreflightRunning] = useState(false);
  const [history, setHistory] = useState<HistorySessionSummary[]>([]);
  const [selectedHistory, setSelectedHistory] = useState<HistorySessionDetail | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [draft, setDraft] = useState<Record<string, string | boolean>>({});
  const [connection, setConnection] = useState<"retrying" | "connected" | "unavailable">("retrying");
  const [notice, setNotice] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [logFilter, setLogFilter] = useState("ALL");
  const polling = useRef(false);
  const previousTerminalResult = useRef<string | null>(null);

  async function refreshConfig() {
    const next = await api.config();
    setConfig(next);
    setDraft(Object.fromEntries(EDITABLE_FIELDS.map(({ key }) => [key, valueForField(next, key)])));
  }

  async function refreshHistory() {
    setHistoryLoading(true);
    try {
      const response = await api.history();
      setHistory(response.items);
      setHistoryError("");
    } catch (requestError) {
      setHistoryError(requestError instanceof ApiError ? requestError.message : "Unable to load session history.");
    } finally {
      setHistoryLoading(false);
    }
  }

  async function selectHistory(sessionId: string) {
    try {
      setSelectedHistory(await api.historySession(sessionId));
      setHistoryError("");
    } catch (requestError) {
      setHistoryError(requestError instanceof ApiError ? requestError.message : "Unable to load this session.");
    }
  }

  async function refresh() {
    if (polling.current) return;
    polling.current = true;
    try {
      const [nextStatus, nextTelemetry, nextLogs, nextPreflight] = await Promise.all([api.status(), api.telemetry(), api.logs(), api.preflight()]);
      setStatus(nextStatus);
      setTelemetry(nextTelemetry);
      setLogs(nextLogs.entries);
      setPreflight(nextPreflight.report);
      const nextTerminal = nextTelemetry.terminalResult;
      if (nextTerminal && nextTerminal !== previousTerminalResult.current) void refreshHistory();
      previousTerminalResult.current = nextTerminal;
      setConnection("connected");
      setError("");
    } catch (requestError) {
      setConnection("unavailable");
      setError(requestError instanceof ApiError ? requestError.message : "Unable to refresh dashboard.");
    } finally {
      polling.current = false;
    }
  }

  useEffect(() => {
    void refresh();
    void refreshHistory();
    void refreshConfig().catch((requestError: unknown) => setError(requestError instanceof ApiError ? requestError.message : "Unable to load configuration."));
    const interval = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(interval);
  }, []);

  async function sessionAction(action: "start" | "pause" | "resume" | "stop") {
    if (action === "start" && !window.confirm("Start a bounded bot session? This may perform real emulator actions when dryRun is false.")) return;
    if (action === "stop" && !window.confirm("Request a safe cooperative stop? An ADB command already sent cannot be cancelled.")) return;
    try {
      await api[action]();
      setNotice(`${action[0].toUpperCase()}${action.slice(1)} request sent.`);
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Session action failed.");
    }
  }

  async function runPreflight() {
    setPreflightRunning(true);
    try {
      const report = await api.runPreflight();
      setPreflight(report);
      setNotice(`Preflight completed: ${report.overallStatus}.`);
      setError("");
      await refresh();
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Preflight failed to run.");
    } finally {
      setPreflightRunning(false);
    }
  }

  async function saveConfig() {
    if (!config) return;
    const patch: Record<string, string | number | boolean> = {};
    for (const field of EDITABLE_FIELDS) {
      const before = valueForField(config, field.key);
      const after = draft[field.key];
      if (after === before) continue;
      if (field.type === "number") patch[field.key] = Number(after);
      else if (field.type === "boolean") patch[field.key] = Boolean(after);
      else if (field.key === "strategy") patch[field.key] = "sneaky_goblin";
      else patch[field.key] = Number(after);
    }
    if (Object.keys(patch).length === 0) {
      setNotice("No configuration changes to save.");
      return;
    }
    try {
      const next = await api.updateConfig(patch as ConfigPatch);
      setConfig(next);
      setDraft(Object.fromEntries(EDITABLE_FIELDS.map(({ key }) => [key, valueForField(next, key)])));
      setNotice("Configuration saved and validated.");
      setError("");
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Configuration save failed.");
    }
  }

  function updateSelectDraft(key: keyof ConfigPatch, value: string) {
    const next = { ...draft, [key]: value };
    if (key === "battlesPerSession") {
      next.maxRuntimeSeconds = String(RECOMMENDED_RUNTIME_SECONDS[Number(value)]);
    }
    setDraft(next);
  }

  const runtimeState = status?.runtimeState ?? "IDLE";
  const isActive = ACTIVE_STATES.has(runtimeState);
  const isLiveMode = config?.dryRun === false || telemetry?.dryRun === false;
  const preflightBlocked = preflight?.overallStatus === "blocked";
  const failedPreflightChecks = preflight?.checks.filter((check) => check.status === "fail").map((check) => check.title).join(", ") ?? "";
  const externallyOwnedBackend = new URLSearchParams(window.location.search).get("backendOwnership") === "external";
  const advancedConfig = config ? Object.entries(config).filter(([key]) => !EDITABLE_FIELDS.some((field) => field.key === key)) : [];
  const shownLogs = logFilter === "ALL" ? logs : logs.filter((entry) => entry.level === logFilter);
  const battlesPlanned = telemetry?.battlesPlanned ?? 0;
  const battlesCompleted = telemetry?.battlesCompleted ?? 0;
  const battlesRemaining = battlesPlanned > 0
    ? Math.max(0, battlesPlanned - battlesCompleted)
    : null;

  return (
    <main className="shell">
      <header className="masthead">
        <div><p className="eyebrow">LOCAL OPERATOR CONSOLE</p><h1>Field Console</h1><p className="subtitle">CoC automation observation and bounded-session control.</p></div>
        <div className={`connection ${connection}`}><span className="signal" />{connection}</div>
      </header>

      {isLiveMode && <div className="live-warning"><strong>LIVE MODE:</strong> dryRun is false. Starting a session may send real ADB taps to LDPlayer.</div>}
      {externallyOwnedBackend && <div className="message success"><strong>Backend ownership: externally owned.</strong> Electron is reusing an existing local FastAPI process. The config and runtime-data folder prepared by Electron do not apply to that process.</div>}
      {error && <div className="message error" role="alert">{error}</div>}
      {notice && <div className="message success" role="status">{notice}</div>}

      <section className="grid primary-grid" aria-label="Session overview">
        <article className="panel session-panel">
          <div className="panel-heading"><p className="eyebrow">SESSION</p><span className={`badge ${statusTone(runtimeState)}`}>{runtimeState}</span></div>
          <h2>{telemetry?.phase ?? "Awaiting backend"}</h2>
          <dl className="definition-list">
            <div><dt>Terminal result</dt><dd>{telemetry?.terminalResult ?? "No result yet"}</dd></div>
            <div><dt>Terminal message</dt><dd>{telemetry?.terminalMessage ?? "Awaiting session"}</dd></div>
            <div><dt>Last error</dt><dd>{telemetry?.lastError ?? "None"}</dd></div>
            <div><dt>Failure code</dt><dd>{telemetry?.failureCode ?? "None"}</dd></div>
            <div><dt>Expected / observed</dt><dd>{telemetry?.expectedStates?.join(", ") || "--"} / {telemetry?.observedState ?? "--"}</dd></div>
          </dl>
          {telemetry?.diagnosticScreenshotPath && <ArtifactPath path={telemetry.diagnosticScreenshotPath} />}
          <div className="actions">
            <button className="primary" disabled={isActive || preflightBlocked} onClick={() => void sessionAction("start")}>Start session</button>
            <button disabled={runtimeState !== "RUNNING"} onClick={() => void sessionAction("pause")}>Pause</button>
            <button disabled={runtimeState !== "PAUSED"} onClick={() => void sessionAction("resume")}>Resume</button>
            <button className="danger" disabled={!isActive} onClick={() => void sessionAction("stop")}>Stop safely</button>
          </div>
          <p className="checkpoint-note">Pause and Stop apply at the next safe controller checkpoint. ADB commands already sent cannot be interrupted.</p>
          {preflightBlocked && <p className="checkpoint-note"><strong>Start blocked:</strong> {failedPreflightChecks}. Run Preflight again after fixing these items.</p>}
        </article>

        <article className="panel telemetry-panel">
          <div className="panel-heading"><p className="eyebrow">LIVE TELEMETRY</p><span className="timestamp">Updated {formatTime(telemetry?.updatedAt)}</span></div>
          <div className="metric-strip"><div><span>SCREEN</span><strong>{telemetry?.gameScreen ?? "Unknown"}</strong></div><div><span>CONFIDENCE</span><strong>{telemetry?.screenConfidence?.toFixed(2) ?? "--"}</strong></div><div><span>DECISION</span><strong>{telemetry?.decision ?? "--"}</strong></div><div><span>BATTLES LEFT</span><strong>{battlesRemaining === null ? "--" : `${battlesRemaining} (${battlesCompleted}/${battlesPlanned})`}</strong></div></div>
          <div className="resources"><div><span>Gold</span><strong>{formatValue(telemetry?.gold)}</strong></div><div><span>Elixir</span><strong>{formatValue(telemetry?.elixir)}</strong></div><div><span>Dark Elixir</span><strong>{formatValue(telemetry?.darkElixir)}</strong></div></div>
          <p className="detail-line">Runtime: {formatDuration(telemetry?.sessionElapsedSeconds ?? null)} elapsed | {formatDuration(telemetry?.sessionRemainingSeconds ?? null)} remaining</p>
          <p className="detail-line">{formatValue(telemetry?.screenDetails)}</p>
          <p className="detail-line">Reasons: {telemetry?.decisionReasons?.join(" | ") || "Awaiting decision"}</p>
        </article>
      </section>

      <section className="panel preflight-panel" aria-label="Preflight diagnostics">
        <div className="panel-heading"><div><p className="eyebrow">PREFLIGHT</p><h2>{preflight ? "Environment diagnostics" : "Not run yet"}</h2></div><div className="actions"><span className={`badge ${preflight ? preflightTone(preflight.overallStatus) : "quiet"}`}>{preflight?.overallStatus ?? "not run"}</span><button className="primary" disabled={preflightRunning || isActive} onClick={() => void runPreflight()}>{preflightRunning ? "Running checks..." : preflight ? "Run again" : "Run checks"}</button></div></div>
        <p className="config-note">Preflight performs read-only ADB checks and may save one diagnostic screenshot. It never starts a bot session or sends gameplay taps.</p>
        {preflight && <><p className="timestamp">Checked {formatTime(preflight.checkedAt)}</p><div className="preflight-checks">{preflight.checks.map((check) => <article className={`preflight-check ${check.status}`} key={check.id}><div><span className={`badge ${preflightTone(check.status)}`}>{check.status}</span><strong>{check.title}</strong></div><p>{check.detail}</p>{check.remediation && <small>{check.remediation}</small>}{Object.keys(check.metadata).length > 0 && <code>{formatValue(check.metadata)}</code>}</article>)}</div></>}
      </section>

      <section className="panel history-panel" aria-label="Session history">
        <div className="panel-heading"><div><p className="eyebrow">SESSION HISTORY</p><h2>Stored audit trail</h2></div><button onClick={() => void refreshHistory()} disabled={historyLoading}>{historyLoading ? "Refreshing..." : "Refresh history"}</button></div>
        <p className="config-note">Records are local, newest first. Artifact paths are references only and never open files from the dashboard.</p>
        {historyError && <p className="history-error">{historyError}</p>}
        {!historyLoading && !historyError && history.length === 0 && <p className="empty">No completed, stopped, or failed sessions have been recorded yet.</p>}
        {history.length > 0 && <div className="history-list">{history.map((item) => <button className={`history-row ${selectedHistory?.sessionId === item.sessionId ? "selected" : ""}`} key={item.sessionId} onClick={() => void selectHistory(item.sessionId)}><span className={`badge ${resultTone(item.terminalResult)}`}>{item.terminalResult ?? "unknown"}</span><strong>{formatTime(item.startedAt)}</strong><span>{formatDuration(item.durationSeconds)}</span><span>{item.dryRun ? "dry run" : "live"}</span><span>{item.strategy ?? "no strategy"}</span><span>{item.basesChecked} / {item.maxBases} bases</span><small>{item.terminalMessage ?? "No terminal message"}</small></button>)}</div>}
        {selectedHistory && <article className="history-detail"><div className="panel-heading"><div><p className="eyebrow">SESSION DETAIL</p><h2>{selectedHistory.sessionId}</h2></div><span className={`badge ${resultTone(selectedHistory.terminalResult)}`}>{selectedHistory.terminalResult}</span></div><div className="counter-grid"><div><span>Gold</span><strong>{formatValue(selectedHistory.telemetry?.gold)}</strong></div><div><span>Elixir</span><strong>{formatValue(selectedHistory.telemetry?.elixir)}</strong></div><div><span>Dark Elixir</span><strong>{formatValue(selectedHistory.telemetry?.darkElixir)}</strong></div><div><span>Decision</span><strong>{selectedHistory.telemetry?.decision ?? "--"}</strong></div></div><p className="detail-line">Terminal: {selectedHistory.terminalMessage ?? "None"}</p>{selectedHistory.lastError && <p className="history-error">Error: {selectedHistory.lastError}</p>}<p className="detail-line">Preflight: {selectedHistory.preflight?.overallStatus ?? "not run"}</p><p className="detail-line">Attack plan: {formatValue(selectedHistory.attackPlan)}</p><div className="history-paths">{selectedHistory.artifactPaths.map((path) => <ArtifactPath key={path} path={path} />)}</div><div className="timeline">{selectedHistory.events.map((event) => <div key={`${event.timestamp}-${event.message}`}><time>{formatTime(event.timestamp)}</time><span className={`log-level ${event.level?.toLowerCase() ?? ""}`}>{event.eventType}</span><p>{event.message}</p></div>)}</div></article>}
      </section>

      <section className="grid secondary-grid">
        <article className="panel">
          <div className="panel-heading"><p className="eyebrow">SEARCH READOUT</p><span className="timestamp">{telemetry?.strategy ?? "No strategy"}</span></div>
          <div className="counter-grid"><div><span>Bases</span><strong>{telemetry?.basesChecked ?? 0} / {telemetry?.maxBases ?? 0}</strong></div><div><span>Next taps</span><strong>{telemetry?.nextTaps ?? 0}</strong></div><div><span>OCR attempts</span><strong>{telemetry?.ocrAttempts ?? 0}</strong></div><div><span>Unknown retries</span><strong>{telemetry?.unknownStateRetries ?? 0}</strong></div></div>
          <div className="plan-block"><span>Attack plan</span><strong>{telemetry?.attackPlan ? formatValue(telemetry.attackPlan) : "No plan generated"}</strong></div>
        </article>

        <article className="panel artifacts">
          <div className="panel-heading"><p className="eyebrow">ARTIFACT PATHS</p><span className="timestamp">Text only</span></div>
          <p>Paths belong to the backend machine. This dashboard does not assume browser filesystem access.</p>
          {telemetry?.screenshotPath && <ArtifactPath path={telemetry.screenshotPath} />}
          {telemetry?.debugArtifactPaths?.map((path) => <ArtifactPath key={path} path={path} />)}
          {!telemetry?.screenshotPath && !telemetry?.debugArtifactPaths?.length && <p className="empty">No screenshot or debug artifact reported.</p>}
        </article>
      </section>

      <section className="grid lower-grid">
        <article className="panel config-panel">
          <div className="panel-heading"><div><p className="eyebrow">CONFIGURATION</p><h2>Safe controls</h2></div><button className="primary" disabled={isActive || !config} onClick={() => void saveConfig()}>Save changed fields</button></div>
          <p className="config-note">Only fields accepted by <code>PUT /api/config</code> are editable. Advanced fields remain read-only.</p>
          <div className="config-fields">
            {EDITABLE_FIELDS.map((field) => <label key={field.key} className={field.key === "dryRun" ? "dry-run-field" : ""}><span><strong>{field.label}</strong><small>{field.hint}</small></span>{field.type === "boolean" ? <input type="checkbox" checked={Boolean(draft[field.key])} disabled={isActive} onChange={(event) => setDraft({ ...draft, [field.key]: event.target.checked })} /> : field.type === "select" ? <select value={String(draft[field.key] ?? (field.key === "strategy" ? "sneaky_goblin" : "5"))} disabled={isActive} onChange={(event) => updateSelectDraft(field.key, event.target.value)}>{field.key === "strategy" ? <option value="sneaky_goblin">Sneaky Goblin</option> : <><option value="1">1 battle (test)</option><option value="5">5 battles</option><option value="10">10 battles</option></>}</select> : <input type="number" min="0" value={String(draft[field.key] ?? "")} disabled={isActive} onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })} />}</label>)}
          </div>
          <details><summary>Advanced configuration (read-only)</summary><dl className="readonly-list">{advancedConfig.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{formatValue(value)}</dd></div>)}</dl></details>
        </article>

        <article className="panel logs-panel">
          <div className="panel-heading"><div><p className="eyebrow">EVENT LOG</p><h2>Recent runtime messages</h2></div><select aria-label="Filter logs by level" value={logFilter} onChange={(event) => setLogFilter(event.target.value)}><option>ALL</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></div>
          <div className="log-list">{shownLogs.length === 0 ? <p className="empty">No logs yet. Backend events appear here while a session runs.</p> : shownLogs.slice().reverse().map((entry) => <div className="log-entry" key={`${entry.timestamp}-${entry.message}`}><time>{formatTime(entry.timestamp)}</time><span className={`log-level ${entry.level.toLowerCase()}`}>{entry.level}</span><p>{entry.message}</p></div>)}</div>
        </article>
      </section>
    </main>
  );
}
