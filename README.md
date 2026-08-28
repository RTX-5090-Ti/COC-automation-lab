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
