"""
Watchdog — runs every 2 minutes, restarts the bot if it's not running.
Registered as a separate scheduled task via the registry Run key.
"""
import subprocess
import sys
import os
import time

SCRIPT = os.path.join(os.path.dirname(__file__), "main.py")
PYTHON = sys.executable
LOG = os.path.join(os.path.dirname(__file__), "scanner.log")


def bot_is_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )
    return "pythonw.exe" in result.stdout


def start_bot():
    pythonw = PYTHON.replace("python.exe", "pythonw.exe")
    with open(LOG, "a") as log:
        subprocess.Popen(
            [pythonw, "-u", SCRIPT],
            stdout=log, stderr=log,
            cwd=os.path.dirname(__file__),
            creationflags=0x00000008,  # DETACHED_PROCESS
        )


if __name__ == "__main__":
    while True:
        if not bot_is_running():
            with open(LOG, "a") as log:
                log.write("[watchdog] Bot not running — restarting...\n")
            start_bot()
        time.sleep(120)
