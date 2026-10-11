"""
schedule_export.py  --  set up (or remove) the nightly Day One auto-export

Registers a scheduled job that runs `dayone_web_export.py nightly` once a night,
so your journal exports and ingests by itself while you sleep. Works on Windows
(Task Scheduler) and macOS (launchd).

USAGE
-----
    python schedule_export.py            Install the nightly job (default 3 AM).
    python schedule_export.py --time 02:30
                                         Install at a specific HH:MM (24-hour).
    python schedule_export.py --remove   Remove the nightly job.

BEFORE YOU SCHEDULE IT
----------------------
Run `python dayone_web_export.py` once, interactively, and log into Day One so
the saved profile has a valid session. The nightly job can't log in for you --
if the session ever expires it fails fast and pops a desktop notification
telling you to do one more interactive login.

REQUIREMENTS
------------
The computer must be on or asleep at the scheduled time (a fully shut-down PC
can't run it). On Windows this task is set to wake the machine from sleep; on a
Mac, closed-lid sleep won't wake for it (see the note printed after install).
"""

import os
import sys
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXPORT_SCRIPT = os.path.join(_HERE, "dayone_web_export.py")

WIN_TASK_NAME = "MiniAI DayOne Auto-Export"
MAC_LABEL = "com.miniai.dayone-export"


def _parse_time(argv):
    hh, mm = 3, 0
    if "--time" in argv:
        i = argv.index("--time")
        try:
            hh, mm = (int(x) for x in argv[i + 1].split(":"))
        except Exception:
            print("Couldn't read --time; using 03:00.")
            hh, mm = 3, 0
    return hh, mm


def _windows_pythonw():
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return pythonw if os.path.exists(pythonw) else exe


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #

def _win_task_xml(hh, mm):
    launch = _windows_pythonw()
    # StartBoundary date is arbitrary in the past; only the time-of-day matters
    # for a daily trigger.
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Nightly Day One journal export for Mini AI for Day One and Diarium.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{hh:02d}:{mm:02d}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT3H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{launch}</Command>
      <Arguments>"{_EXPORT_SCRIPT}" nightly</Arguments>
      <WorkingDirectory>{_HERE}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _win_install(hh, mm):
    xml = _win_task_xml(hh, mm)
    # schtasks wants the XML as a file; UTF-16 matches the declared encoding.
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    with open(path, "w", encoding="utf-16") as f:
        f.write(xml)

    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", WIN_TASK_NAME, "/XML", path, "/F"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f'Installed nightly export task "{WIN_TASK_NAME}" '
                  f"at {hh:02d}:{mm:02d}, set to wake the PC if asleep.")
            print("Test it now without waiting: open Task Scheduler Library, "
                  "right-click the task, choose Run.")
            return True

        # Permissions block -> retry elevated with a UAC prompt.
        print("Standard registration was refused; asking for administrator "
              "access (approve the Windows prompt)...")
        import ctypes
        params = f'/Create /TN "{WIN_TASK_NAME}" /XML "{path}" /F'
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "schtasks.exe", params, None, 0
        )
        if int(rc) <= 32:
            print("Administrator access was declined, so the nightly job "
                  "wasn't scheduled. Run this again and choose Yes on the prompt.")
            return False
        print(f'Installed nightly export task "{WIN_TASK_NAME}" with '
              f"administrator access, at {hh:02d}:{mm:02d}.")
        return True
    finally:
        try:
            os.remove(path)
        except Exception:
            pass


def _win_remove():
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", WIN_TASK_NAME, "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f'Removed "{WIN_TASK_NAME}".')
    else:
        print(f'Could not remove it (maybe it wasn\'t installed): '
              f"{result.stderr.strip() or result.stdout.strip()}")


# --------------------------------------------------------------------------- #
# macOS
# --------------------------------------------------------------------------- #

def _mac_plist_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/{MAC_LABEL}.plist")


def _mac_install(hh, mm):
    log_path = os.path.join(_HERE, "export.log")
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{MAC_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{_EXPORT_SCRIPT}</string>
        <string>nightly</string>
    </array>
    <key>WorkingDirectory</key><string>{_HERE}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{hh}</integer>
        <key>Minute</key><integer>{mm}</integer>
    </dict>
    <key>StandardOutPath</key><string>{log_path}</string>
    <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""
    path = _mac_plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(plist)

    subprocess.run(["launchctl", "unload", path], capture_output=True)
    result = subprocess.run(["launchctl", "load", "-w", path], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"Installed nightly export LaunchAgent at {hh:02d}:{mm:02d}.")
        print("Note: macOS won't wake a sleeping Mac (closed lid) to run this. "
              "If the Mac is asleep at the scheduled time, launchd runs the job "
              "the next time it's awake. To run even while asleep, schedule a "
              "wake with: sudo pmset repeat wakeorpoweron MTWRFSU "
              f"{hh:02d}:{mm:02d}:00")
        return True
    print(f"Couldn't load the LaunchAgent: {result.stderr.strip()}")
    return False


def _mac_remove():
    path = _mac_plist_path()
    subprocess.run(["launchctl", "unload", path], capture_output=True)
    if os.path.exists(path):
        os.remove(path)
        print(f"Removed {MAC_LABEL}.")
    else:
        print("Nothing to remove (LaunchAgent wasn't installed).")


# --------------------------------------------------------------------------- #

def main():
    argv = sys.argv[1:]
    remove = "--remove" in argv
    hh, mm = _parse_time(argv)

    if not os.path.exists(_EXPORT_SCRIPT):
        print(f"Can't find dayone_web_export.py next to this script "
              f"(looked in {_HERE}).")
        sys.exit(1)

    if sys.platform == "win32":
        _win_remove() if remove else _win_install(hh, mm)
    elif sys.platform == "darwin":
        _mac_remove() if remove else _mac_install(hh, mm)
    else:
        print("This scheduler covers Windows and macOS. On Linux, add a cron "
              "line yourself, e.g.:  0 3 * * *  cd "
              f"{_HERE} && {sys.executable} dayone_web_export.py nightly")


if __name__ == "__main__":
    main()
