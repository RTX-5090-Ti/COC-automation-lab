# CoC Vision Automation Lab

Local Python automation learning project for Clash of Clans on Android emulators. The FastAPI server is local-only by default and does not start a bot session on import or startup.

## Prerequisites

- Python 3.11 or newer.
- LDPlayer14 with Clash of Clans installed. ADB and the Tesseract executable are native prerequisites and are not installed by pip.
- Tesseract must be installed and available through its configured executable path or `PATH`; `pytesseract` is only the Python wrapper.

Create and install the virtual environment:

```powershell
py -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

For tests, install development dependencies:

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
```

## ADB And CLI

ADB resolution is: an explicit `--adb-path`, then configured environment variable/PATH, then default emulator paths with LDPlayer preferred before BlueStacks. Device selection can be explicit with `--device-id`; otherwise the controller selects one connected device.

LDPlayer14 example:

```powershell
& "C:\LDPlayer\LDPlayer14\adb.exe" devices -l
& ".\.venv\Scripts\python.exe" main.py --adb-path "C:\LDPlayer\LDPlayer14\adb.exe" --device-id emulator-5554 --debug
```

The standard package is `com.supercell.clashofclans`. Existing CLI test flags remain available through `main.py`.

## Local API

Start the server on its local-only default address:

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload
```

Open Swagger at `http://127.0.0.1:8000/docs`. Do not bind to `0.0.0.0` unless you explicitly understand and accept exposing the server beyond this machine.

Default local API address: `http://127.0.0.1:8000`. The API is local-only by default.

curl examples:

```bash
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/status
curl http://127.0.0.1:8000/api/config
curl -X PUT http://127.0.0.1:8000/api/config -H "Content-Type: application/json" -d '{"dryRun":true}'
curl -X POST http://127.0.0.1:8000/api/session/start
curl -X POST http://127.0.0.1:8000/api/session/pause
curl -X POST http://127.0.0.1:8000/api/session/resume
curl -X POST http://127.0.0.1:8000/api/session/stop
curl "http://127.0.0.1:8000/api/logs?limit=100"
curl http://127.0.0.1:8000/api/telemetry
```

PowerShell examples:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/status
Invoke-RestMethod http://127.0.0.1:8000/api/config
Invoke-RestMethod http://127.0.0.1:8000/api/logs?limit=100
Invoke-RestMethod http://127.0.0.1:8000/api/telemetry
Invoke-RestMethod -Method Put http://127.0.0.1:8000/api/config -ContentType 'application/json' -Body '{"dryRun":true}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/session/start
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/session/pause
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/session/resume
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/session/stop
```

`POST /api/session/start` is the only API operation that starts a bot session. Pause and stop are cooperative: an already-sent ADB command cannot be cancelled, but no later game action is sent after the next controller checkpoint.

## Tests

Run automated tests without LDPlayer, ADB, Tesseract, game assets, or real screenshots:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest
```

## React Dashboard

The standalone operator dashboard is in `frontend/`. Start the FastAPI server first, then use a second terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Vite serves the dashboard at `http://127.0.0.1:5173` and it defaults to `http://127.0.0.1:8000/api`. To point it at another compatible local backend, create `frontend/.env.local` with `VITE_API_BASE_URL=http://127.0.0.1:8000/api`.

The production dashboard uses relative `/api` calls. When FastAPI serves the build, it therefore uses the same local port as the desktop backend. `VITE_API_BASE_URL` is only needed to override the Vite development default.

Build the production frontend without requiring LDPlayer or a bot session:

```powershell
cd frontend
npm.cmd run build
```

## Electron Desktop App

The Electron wrapper opens the existing dashboard in a Windows desktop window and manages only the local FastAPI process it starts. It never starts a bot session by itself.

In development and in the portable package, Electron checks `http://127.0.0.1:<port>/api/health` first. A healthy process is reused and remains externally owned; Electron will not stop it when closing. The dashboard displays this state because the config and runtime-data folder Electron prepared do not apply to an external backend.

Electron resolves Python in this order:

1. `COC_PYTHON_PATH`.
2. `.venv\\Scripts\\python.exe` below the backend root.
3. `python` from `PATH`.

`COC_API_PORT` selects the local port and defaults to `8000`. The backend always binds to `127.0.0.1`.

Install frontend and Electron dependencies, then start the desktop app from the project root:

```powershell
cd frontend
npm.cmd ci
npm.cmd run desktop:dev
```

Create the portable Windows x64 executable with:

```powershell
cd frontend
npm.cmd run desktop:dist
```

The output is written under `frontend/release/`.

Packaged resources are read-only. On first Electron launch, the bundled default `config/bot_config.json` is copied to `%APPDATA%\\CoC Field Console\\bot_config.json`; later launches preserve that file. Screenshots and debug artifacts are written below `%APPDATA%\\CoC Field Console\\screenshots\\`. If an existing config becomes invalid after an upgrade, the API reports the validation error and does not overwrite or reset the user file.

### Source Mode

`npm.cmd run desktop:dev` runs the dashboard with the source backend. It resolves Python from `COC_PYTHON_PATH`, the project `.venv`, then `PATH`.

### Packaged Mode

The portable package bundles a one-folder PyInstaller backend runtime with Python, FastAPI, Uvicorn, OpenCV, NumPy, Pillow, and pytesseract. In packaged mode Electron runs only `resources/backend-runtime/desktop_backend.exe`; it does not search for system Python or `COC_PYTHON_PATH`.

Build dependencies are separate from runtime dependencies:

```powershell
& ".\.venv\Scripts\python.exe" -m pip install -r requirements-build.txt
cd frontend
npm.cmd run backend:bundle
npm.cmd run desktop:dist
```

`desktop:dist` always builds React first, bundles the backend after `frontend/dist` exists, then creates the Windows portable executable.

The bundle contains Python and Python packages only. Target machines must still install LDPlayer14 with Clash of Clans, use the existing LDPlayer-first ADB discovery or configure `ADB_PATH`, and install Tesseract or configure `TESSERACT_PATH`. Opening the desktop app never starts a bot session. Missing ADB, game package, or Tesseract produces a clear session error only when a session is started.

Electron writes backend launch stdout/stderr to `%APPDATA%\CoC Field Console\desktop-backend.log`. Config and generated screenshots stay in the same writable app-data directory. If a config becomes invalid after an upgrade, it is preserved and the API/dashboard reports the validation error instead of resetting it.

For a clean package verification, use a port other than an already-running development server:

```powershell
$env:COC_API_PORT = "8011"
& ".\release\CoC Field Console 0.1.0.exe"
```

The package is self-contained for Python dependencies, but it intentionally does not bundle LDPlayer, ADB, Clash of Clans, or Tesseract.

## Preflight Diagnostics

Use the **Run checks** button in the desktop dashboard before automation when you want to verify the local setup. Preflight is read-only: it never starts a session, launches Clash of Clans, sends taps, or deploys troops. It may take one ADB screenshot and stores it only at `%APPDATA%\CoC Field Console\screenshots\debug\preflight_latest.png`.

Preflight checks writable app data, the existing config file, bundled templates/dashboard assets, ADB and connected LDPlayer device, Clash of Clans installation/foreground state, external Tesseract, and the required `1920x1080` screenshot resolution.

- **Ready**: core checks passed.
- **Warning**: the core runtime is usable, but Clash of Clans is not running or not foreground.
- **Blocked**: repair a required item such as config, assets, ADB/device, Clash of Clans package, Tesseract, screenshot capture, or resolution before starting a session.

Start Session remains available before the first Preflight run for compatibility. Once a completed report is **Blocked**, the dashboard disables Start Session and lists the failed checks. Fix the item, then run checks again. Preflight never resets a missing or invalid user config.

For ADB failures, start LDPlayer14 and confirm that its emulator is online; set `ADB_PATH` if it is installed outside the default locations. For Tesseract failures, install the native Tesseract executable and add it to `PATH` or set `TESSERACT_PATH`. Detailed bundled backend output remains available in `%APPDATA%\CoC Field Console\desktop-backend.log`.

## Session History

Completed, stopped, and failed sessions are recorded locally in `%APPDATA%\CoC Field Console\history\session_history.sqlite3`. History never starts a bot session or runs game automation when the app opens or when records are read.

Each record keeps the terminal result, duration, mode, strategy, counters, final OCR/decision telemetry, attack-plan summary, a snapshot of the Preflight report available when the session began, selected device when available, and important lifecycle events. Screenshot and debug artifacts are stored only as text paths; image bytes are never embedded in the database.

The app keeps the newest 200 sessions and at most 100 important events for each session. The dashboard **Session History** section lists the newest records first. Select a row to inspect its final telemetry, Preflight snapshot, paths, errors, and event timeline. History export and deletion are intentionally not included.

## Reliability And Calibration

`dryRun: true` suppresses gameplay-changing ADB input at the ADB layer: taps, swipes, and `shell input` commands are not sent. Read-only device checks, screenshots, detection, OCR, Preflight, telemetry, and history remain available.

Before state-based actions, the runtime captures a fresh screenshot, verifies the selected device and Clash of Clans foreground app, then requires the expected screen. Unknown or unexpected screens never trigger recovery taps: the only recovery is bounded recapture/detection retry, followed by a clean stop with diagnostics.

Common failure codes include `ADB_DISCONNECTED`, `ADB_COMMAND_FAILED`, `GAME_NOT_FOREGROUND`, `UNEXPECTED_SCREEN_STATE`, `UNKNOWN_SCREEN_EXHAUSTED`, `SCREEN_TIMEOUT`, and `SCREENSHOT_FAILED`. Terminal diagnostics include expected/observed states and a screenshot path when capture succeeded. Diagnostic screenshots are written under `%APPDATA%\CoC Field Console\screenshots\debug\`.

Run the read-only calibration utility before a controlled test:

```powershell
& ".\.venv\Scripts\python.exe" calibrate.py --adb-path "C:\LDPlayer\LDPlayer14\adb.exe" --device-id emulator-5554
```

It captures one screenshot, prints dimensions and screen-template detection, runs OCR, checks template files, and prints artifact paths. It sends no gameplay input. Recommended flow: run Preflight, run calibration, run dry-run diagnostics, review Session History/artifacts, then perform a controlled live deployment-point test. Full attack-plan execution remains intentionally deferred.
