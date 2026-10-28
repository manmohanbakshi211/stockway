"""Start the Bank Nifty Radar app and open it in your browser."""
import socket
import threading
import time
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8765


def _port_in_use() -> bool:
    with socket.socket() as s:
        return s.connect_ex((HOST, PORT)) == 0


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    if _port_in_use():
        print(f"Radar is already running. Opening {url}")
        webbrowser.open(url)
    else:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
        print(f"Bank Nifty Radar running at {url}  (close this window to stop it)")
        uvicorn.run("webapp.server:app", host=HOST, port=PORT, log_level="warning")
