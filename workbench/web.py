from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def main() -> None:
    print(f"Starting Web3 Bug Bounty Workbench at {URL}")
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("workbench.app:app", host=HOST, port=PORT, reload=False)


def open_browser() -> None:
    time.sleep(0.8)
    webbrowser.open(URL)


if __name__ == "__main__":
    main()
