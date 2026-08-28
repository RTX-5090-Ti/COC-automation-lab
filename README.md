# CoC Vision Automation Lab

Milestone 1 focuses only on ADB connectivity, Clash of Clans process checks, and screenshot capture from an Android emulator.

## Run

Activate the virtual environment, then run:

```powershell
python main.py
```

Optional arguments:

```powershell
python main.py --debug
python main.py --adb-path "C:\Program Files\BlueStacks_nxt\HD-Adb.exe"
python main.py --device-id emulator-5554
```

For LDPlayer, connect its ADB endpoint first, then pass the LDPlayer ADB executable and device id:

```powershell
& "C:\LDPlayer\LDPlayer9\adb.exe" connect 127.0.0.1:5555
& "C:\LDPlayer\LDPlayer9\adb.exe" devices
python main.py --adb-path "C:\LDPlayer\LDPlayer9\adb.exe" --device-id 127.0.0.1:5555
```

The game package remains `com.supercell.clashofclans` for the standard Clash of Clans installation. If LDPlayer uses another ADB port, use the serial shown by `adb devices`.
