"""Local HTTP integration fixture: real API/worker/decoder/storage, fake embedding only."""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from fakes import ColorEmbedder

from semantic_video_lake.server.app import Settings, create_app
from semantic_video_lake.server.worker import Worker

directory = Path(sys.argv[1])
token = "integration-token-" + "x" * 32
ready = threading.Event()
stop = threading.Event()


def run_worker():
    worker = Worker(directory, ColorEmbedder())
    ready.set()
    try:
        while not stop.is_set():
            if not worker.tick():
                time.sleep(0.02)
    finally:
        worker.close()


thread = threading.Thread(target=run_worker, daemon=True)
thread.start()
if not ready.wait(20):
    raise RuntimeError("Worker did not start")
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(json.dumps({"url": f"http://127.0.0.1:{sock.getsockname()[1]}", "token": token}), flush=True)
app = create_app(Settings(directory, token))
server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
try:
    server.run(sockets=[sock])
finally:
    stop.set()
    thread.join(5)
