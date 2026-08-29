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
  return app.isPackaged ? path.join(process.resourcesPath, "backend") : path.resolve(__dirname, "../..");
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
    fs.copyFileSync(path.join(root, "config", "bot_config.json"), configPath);
  }
  return { runtimeDataDir, configPath };
}

async function startOrReuseBackend() {
  const root = backendRoot();
  const { runtimeDataDir, configPath } = prepareRuntimeData(root);
  if (await checkHealth()) return true;

  const childEnvironment = {
    ...process.env,
    COC_CONFIG_PATH: configPath,
    COC_RUNTIME_DATA_DIR: runtimeDataDir,
  };
  backendProcess = spawn(resolvePython(root), ["-m", "uvicorn", "api.app:app", "--host", "127.0.0.1", "--port", String(apiPort())], {
    cwd: root,
    env: childEnvironment,
    shell: false,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backendOwnership = "electron";
  backendProcess.stdout.on("data", (data) => console.log(`[backend] ${data}`));
  backendProcess.stderr.on("data", (data) => console.error(`[backend] ${data}`));
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
      "The local FastAPI service did not become healthy. Install Python and packages from requirements.txt, or set COC_PYTHON_PATH to the intended Python executable. Tesseract and ADB are still required only for game automation."
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
