#!/usr/bin/env python3
"""Tiny line-oriented TCP echo server for Microsandbox port diagnostics."""

from __future__ import annotations

import argparse
import socket
import sys
import threading


def handle(conn: socket.socket) -> None:
    with conn:
        print("raw client connected", file=sys.stderr, flush=True)
        file = conn.makefile("rb")
        for line in file:
            conn.sendall(b"ECHO:" + line)
        print("raw client disconnected", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(32)
    print(f"raw echo listening on {args.host}:{args.port}", file=sys.stderr, flush=True)
    while True:
        conn, _addr = server.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
