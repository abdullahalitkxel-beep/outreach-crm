"""
Outreach CRM desktop launcher (this is what OutreachCRM.exe runs).

- Double-click: starts the CRM in the background and opens it in its own app window.
  Opening it again while it's running just brings up another window.
- `OutreachCRM.exe --bot <command>`: runs a bot command (used internally by the CRM's buttons).
"""
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

PORT = int(os.getenv("OUTREACH_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"


def _ensure_stdio(log_path):
    """A windowed .exe has no console: send output to a log file instead of crashing."""
    if sys.stdout is None or sys.stderr is None:
        f = open(log_path, "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def _port_in_use():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def open_window(url=URL):
    """Open the CRM in an app-style window (Edge/Chrome --app), falling back to the default browser."""
    if os.getenv("OUTREACH_NO_WINDOW"):
        return
    candidates = []
    if sys.platform == "win32":
        for base in (os.getenv("ProgramFiles(x86)"), os.getenv("ProgramFiles"), os.getenv("LOCALAPPDATA")):
            if base:
                candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                               Path(base) / "Google/Chrome/Application/chrome.exe"]
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                       Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    for exe in candidates:
        if exe.exists():
            try:
                flags = getattr(subprocess, "DETACHED_PROCESS", 0)
                subprocess.Popen([str(exe), f"--app={url}", "--window-size=1400,900"], creationflags=flags)
                return
            except Exception:
                pass
    webbrowser.open(url)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--bot":
        import bot
        if not os.getenv("OUTREACH_JOB_LOG"):
            _ensure_stdio(bot.DATA / "bot.log")
        bot.main(sys.argv[2:])
        return

    import bot  # sets up the data folder (%APPDATA%\OutreachCRM) on first run
    _ensure_stdio(bot.DATA / "app.log")

    if _port_in_use():  # already running: just open another window
        open_window()
        return

    import crm
    threading.Thread(target=crm._autorun_loop, daemon=True).start()
    threading.Timer(1.0, open_window).start()
    print(f"Outreach CRM running at {URL} (data folder: {bot.DATA})")
    crm.app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
