from __future__ import annotations

import socket


def find_free_port(host: str = "127.0.0.1", start_port: int = 8787, max_tries: int = 300) -> int:
    port = start_port
    attempts = 0
    while attempts < max_tries:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                port += 1
                attempts += 1
                continue
    raise RuntimeError(f"Could not find a free port after {max_tries} attempts from {start_port}.")
