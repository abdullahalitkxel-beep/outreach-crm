"""
Outreach CRM desktop launcher (this is what OutreachCRM.exe runs).

- Double-click: starts the CRM and opens it in its own app window. Opening it again while it's
  running just brings up another window.
- Closing the window quits the app, unless "Run automatically" is on in Settings → Safety; then it
  keeps running in the background with a tray icon (Open / Quit) next to the clock.
- `BSO.exe --bot <command>`: runs a bot command (used internally by the CRM's buttons).
- `BSO.exe --hub`: runs the master console (team hub) and opens it; the Start-menu shortcut "BSO Master Console" does this.
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PORT = int(os.getenv("OUTREACH_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"
APP_NAME = "BSO"


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


def shutdown():
    print("BSO: shutting down")
    os._exit(0)


CLOSE_GRACE = 75          # seconds after a window says goodbye before quitting (a hidden window still pings at least once a minute)
FIRST_WINDOW = 180        # how long the first window gets to appear
STALE = 6 * 3600          # no sign of any window for this long (browser crashed): quit anyway


def should_quit(now, started, last_seen, last_bye, keep=False, busy=False):
    """Decide whether the app should quit because its window is gone.

    Windows ping /heartbeat every 10 s while open, but Edge/Chrome slow that to about once a minute
    (or stop it) when the window is minimised, hidden or the laptop sleeps. So silence alone is not a
    reason to quit; a window that is really closed says goodbye (/bye) as it goes."""
    if keep or busy:
        return False
    if last_seen is None:
        return now - started > FIRST_WINDOW and last_bye is None
    if last_bye and last_bye >= last_seen and now - last_bye > CLOSE_GRACE:
        return True
    return now - last_seen > STALE


def watchdog(crm, tray_ref):
    """Quit after the last window is closed, unless automatic runs are switched on."""
    started = time.time()
    while True:
        time.sleep(5)
        try:
            c = crm.cfg()
            keep = bool((c.get("autorun") or {}).get("enabled")) or "--background" in sys.argv
            if should_quit(time.time(), started, crm.LAST_SEEN, crm.LAST_BYE, keep, crm.job_running()) \
                    and not os.getenv("OUTREACH_NO_WINDOW"):
                if tray_ref.get("icon"):
                    tray_ref["icon"].stop()
                shutdown()
        except Exception as e:  # never let the watchdog crash the app
            print("watchdog:", e)


def run_tray(tray_ref):
    """Tray icon with Open / Quit. Returns False if a tray isn't available (then the server runs normally)."""
    if os.getenv("OUTREACH_NO_WINDOW"):
        return False
    try:
        import pystray
        from PIL import Image
        here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        image = Image.open(here / "assets" / "icon.png")
        icon = pystray.Icon("BSO", image, f"{APP_NAME} is running", menu=pystray.Menu(
            pystray.MenuItem(f"Open {APP_NAME}", lambda: open_window(), default=True),
            pystray.MenuItem("Quit", lambda i: (i.stop(), shutdown())),
        ))
        tray_ref["icon"] = icon
        icon.run()
        return True
    except Exception as e:
        print("tray unavailable:", e)
        return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--bot":
        import bot
        if not os.getenv("OUTREACH_JOB_LOG"):
            _ensure_stdio(bot.DATA / "bot.log")
        bot.main(sys.argv[2:])
        return

    if len(sys.argv) > 1 and sys.argv[1] == "--hub":
        import bot
        _ensure_stdio(bot.DATA / "hub.log")
        args = sys.argv[2:]
        port = 8780
        if "--port" in args:
            try:
                port = int(args[args.index("--port") + 1])
            except (IndexError, ValueError):
                pass
        hub_url = f"http://127.0.0.1:{port}"
        with socket.socket() as s:
            s.settimeout(0.5)
            running = s.connect_ex(("127.0.0.1", port)) == 0
        if running or "--reset-owner-password" in args:
            if running:
                open_window(hub_url)     # already running: just open the console again
                return
        import hub
        if "--no-window" not in args and "--reset-owner-password" not in args:
            threading.Timer(2.0, open_window, args=(hub_url,)).start()
        hub.main([a for a in args if a != "--no-window"])
        return

    import bot  # sets up the data folder (%APPDATA%\OutreachCRM) on first run
    _ensure_stdio(bot.DATA / "app.log")

    background = "--background" in sys.argv     # started with Windows: no window, just the icon next to the clock
    if _port_in_use():  # already running: just open another window
        if not background:
            open_window()
        return

    import crm
    import team
    if team.resume():
        print("BSO Bot: still signed in, carrying on")
    tray_ref = {}
    threading.Thread(target=crm._autorun_loop, daemon=True).start()
    threading.Thread(target=crm._team_loop, daemon=True).start()
    try:
        import watcher                       # replies are picked up the moment they arrive
        watcher.start(crm.CONFIG_PATH, allowed=lambda: not (crm.team.required() and not crm.team.signed_in()) and crm.terms_accepted())
    except Exception as e:
        print("reply watcher:", e)
    threading.Thread(target=crm._mailbox_loop, daemon=True).start()
    threading.Thread(target=watchdog, args=(crm, tray_ref), daemon=True).start()
    server = threading.Thread(target=lambda: crm.app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False),
                              daemon=True)
    server.start()
    if not background:
        threading.Timer(1.0, open_window).start()
    print(f"BSO running at {URL} (data folder: {bot.DATA})")
    if not run_tray(tray_ref):
        server.join()


if __name__ == "__main__":
    main()
