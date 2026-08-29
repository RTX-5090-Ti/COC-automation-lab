const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

let mainWindow = null;
let backendProcess = null;
let backendOwnership = "external";

app.setName("CoC Field Console");

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
}

app.on("second-instance", () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
});

function backendRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, "backend-runtime") : path.resolve(__dirname, "../..");
}

function apiPort() {
  const parsed = Number.parseInt(process.env.COC_API_PORT ?? "8000", 10);
  return Number.isInteger(parsed) && parsed > 0 && parsed < 65536 ? parsed : 8000;
}

function apiOrigin() {
  return `http://127.0.0.1:${apiPort()}`;
}

function checkHealth(timeoutMs = 1000) {
  return new Promise((resolve) => {
    const request = http.get(`${apiOrigin()}/api/health`, { timeout: timeoutMs }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve(false));
  });
}

function waitForHealth(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const probe = async () => {
      if (await checkHealth()) return resolve(true);
      if (Date.now() >= deadline) return resolve(false);
      setTimeout(probe, 250);
    };
    void probe();
  });
}

function resolvePython(root) {
  const portableProjectVenv = process.env.PORTABLE_EXECUTABLE_DIR
    ? path.resolve(process.env.PORTABLE_EXECUTABLE_DIR, "..", "..", ".venv", "Scripts", "python.exe")
    : null;
  const candidates = [
    process.env.COC_PYTHON_PATH,
    path.join(root, ".venv", "Scripts", "python.exe"),
    portableProjectVenv,
    // A portable executable launched from frontend/release can use this project's venv.
    path.resolve(process.cwd(), "..", "..", ".venv", "Scripts", "python.exe"),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? "python";
}

function prepareRuntimeData(root) {
  const runtimeDataDir = app.getPath("userData");
  const configPath = path.join(runtimeDataDir, "bot_config.json");
  fs.mkdirSync(path.join(runtimeDataDir, "screenshots", "current"), { recursive: true });
  fs.mkdirSync(path.join(runtimeDataDir, "screenshots", "debug"), { recursive: true });
  if (!fs.existsSync(configPath)) {
    const defaultConfigPath = app.isPackaged
      ? path.join(root, "_internal", "config", "bot_config.json")
      : path.join(root, "config", "bot_config.json");
    fs.copyFileSync(defaultConfigPath, configPath);
  }
  return { runtimeDataDir, configPath };
}

function appendBackendDiagnostic(runtimeDataDir, message) {
  const timestamp = new Date().toISOString();
  fs.appendFileSync(path.join(runtimeDataDir, "desktop-backend.log"), `${timestamp} ${message}\n`);
}

async function startOrReuseBackend() {
  const root = backendRoot();
  const { runtimeDataDir, configPath } = prepareRuntimeData(root);
  if (await checkHealth()) return true;

  const bundledExecutable = path.join(root, "desktop_backend.exe");
  const backendExecutable = app.isPackaged ? bundledExecutable : resolvePython(root);
  const backendArguments = app.isPackaged
    ? []
    : ["-m", "uvicorn", "api.app:app", "--host", "127.0.0.1", "--port", String(apiPort())];
  if (app.isPackaged && !fs.existsSync(bundledExecutable)) {
    appendBackendDiagnostic(runtimeDataDir, `Bundled backend executable is missing: ${bundledExecutable}`);
    return false;
  }
  appendBackendDiagnostic(
    runtimeDataDir,
    `Launching backend: executable=${backendExecutable} root=${root} packaged=${app.isPackaged}`
  );
  const childEnvironment = {
    ...process.env,
    COC_CONFIG_PATH: configPath,
    COC_RUNTIME_DATA_DIR: runtimeDataDir,
  };
  backendProcess = spawn(backendExecutable, backendArguments, {
    cwd: root,
    env: childEnvironment,
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backendOwnership = "electron";
  backendProcess.stdout.on("data", (data) => {
    const message = data.toString();
    console.log(`[backend] ${message}`);
    appendBackendDiagnostic(runtimeDataDir, `[stdout] ${message.trim()}`);
  });
  backendProcess.stderr.on("data", (data) => {
    const message = data.toString();
    console.error(`[backend] ${message}`);
    appendBackendDiagnostic(runtimeDataDir, `[stderr] ${message.trim()}`);
  });
  backendProcess.on("error", (error) => appendBackendDiagnostic(runtimeDataDir, `[spawn error] ${error.message}`));
  return waitForHealth();
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 960,
    minHeight: 680,
    backgroundColor: "#f4efe4",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (new URL(url).origin !== apiOrigin()) event.preventDefault();
  });
  if (process.env.COC_ELECTRON_DEVTOOLS === "1") mainWindow.webContents.openDevTools({ mode: "detach" });
  mainWindow.loadURL(`${apiOrigin()}/?backendOwnership=${backendOwnership === "external" ? "external" : "electron"}`);
}

app.whenReady().then(async () => {
  const started = await startOrReuseBackend();
  if (!started) {
    dialog.showErrorBox(
      "CoC Field Console could not start",
      "The local FastAPI service did not become healthy. See desktop-backend.log in the app data folder for details. Packaged builds include Python; source builds need Python and requirements.txt. Tesseract and ADB are still required only for game automation."
    );
    app.quit();
    return;
  }
  createWindow();
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", () => {
  if (backendOwnership === "electron" && backendProcess && !backendProcess.killed) {
    backendProcess.kill();
  }
});
