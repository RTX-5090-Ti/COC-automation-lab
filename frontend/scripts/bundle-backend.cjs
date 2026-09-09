const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const frontendRoot = path.resolve(__dirname, "..");
const projectRoot = path.resolve(frontendRoot, "..");
const outputRoot = path.join(projectRoot, "build", "backend-runtime");
const workRoot = path.join(projectRoot, "build", "pyinstaller-work");
const specRoot = path.join(projectRoot, "build", "pyinstaller-spec");
const portableConfigRoot = path.join(projectRoot, "build", "portable-config");
const pythonCandidates = [
  process.env.COC_PYTHON_PATH,
  path.join(projectRoot, ".venv", "Scripts", "python.exe"),
  "python",
].filter(Boolean);
const python = pythonCandidates.find((candidate) => candidate === "python" || fs.existsSync(candidate));

for (const requiredPath of [
  path.join(projectRoot, "desktop_backend.py"),
  path.join(projectRoot, "templates"),
  path.join(projectRoot, "config", "bot_config.json"),
  path.join(frontendRoot, "dist", "index.html"),
]) {
  if (!fs.existsSync(requiredPath)) throw new Error(`Required backend bundle asset is missing: ${requiredPath}`);
}

if (path.dirname(outputRoot) !== path.join(projectRoot, "build")) {
  throw new Error(`Refusing to remove unexpected backend output path: ${outputRoot}`);
}
fs.rmSync(outputRoot, { recursive: true, force: true });
fs.mkdirSync(outputRoot, { recursive: true });

// Keep developer settings intact; every new portable profile starts in dry-run.
const portableConfig = JSON.parse(fs.readFileSync(path.join(projectRoot, "config", "bot_config.json"), "utf8"));
portableConfig.dryRun = true;
fs.mkdirSync(portableConfigRoot, { recursive: true });
const portableConfigPath = path.join(portableConfigRoot, "bot_config.json");
fs.writeFileSync(portableConfigPath, `${JSON.stringify(portableConfig, null, 2)}\n`);

const addData = (source, destination) => `${source}${path.delimiter}${destination}`;
const args = [
  "-m", "PyInstaller",
  "--noconfirm", "--clean", "--onedir", "--name", "desktop_backend",
  "--distpath", outputRoot,
  "--workpath", workRoot,
  "--specpath", specRoot,
  "--paths", projectRoot,
  "--add-data", addData(path.join(projectRoot, "templates"), "templates"),
  "--add-data", addData(portableConfigPath, "config"),
  "--add-data", addData(path.join(frontendRoot, "dist"), path.join("frontend", "dist")),
  "--collect-all", "fastapi",
  "--collect-all", "uvicorn",
  "--collect-all", "cv2",
  "--collect-all", "numpy",
  "--collect-all", "PIL",
  "--collect-all", "pytesseract",
  "--hidden-import", "uvicorn.logging",
  "--hidden-import", "uvicorn.loops.auto",
  "--hidden-import", "uvicorn.protocols.http.auto",
  "--hidden-import", "uvicorn.protocols.websockets.auto",
  "--hidden-import", "uvicorn.lifespan.on",
  path.join(projectRoot, "desktop_backend.py"),
];

console.log(`Bundling Python backend with: ${python}`);
const result = spawnSync(python, args, { cwd: projectRoot, stdio: "inherit", shell: false });
if (result.error) throw result.error;
if (result.status !== 0) process.exit(result.status ?? 1);

const executable = path.join(outputRoot, "desktop_backend", "desktop_backend.exe");
if (!fs.existsSync(executable)) throw new Error(`PyInstaller did not produce backend executable: ${executable}`);
const bundledConfig = JSON.parse(fs.readFileSync(path.join(path.dirname(executable), "_internal", "config", "bot_config.json"), "utf8"));
if (bundledConfig.dryRun !== true) throw new Error("Portable backend must ship with dryRun: true.");
console.log(`Bundled backend ready: ${path.dirname(executable)}`);
