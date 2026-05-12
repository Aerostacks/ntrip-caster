from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import socket
import socketserver
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("ntrip_caster")


@dataclass(slots=True)
class MountpointConfig:
    source_password: str
    client_username: str | None = None
    client_password: str | None = None

    @property
    def requires_client_auth(self) -> bool:
        return self.client_username is not None and self.client_password is not None


class MountpointState:
    def __init__(self, config: MountpointConfig) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.source: socket.socket | None = None
        self.clients: set[socket.socket] = set()

    def set_source(self, sock: socket.socket) -> None:
        with self.lock:
            if self.source is not None and self.source is not sock:
                with contextlib.suppress(OSError):
                    self.source.close()
            self.source = sock

    def clear_source(self, sock: socket.socket) -> None:
        with self.lock:
            if self.source is sock:
                self.source = None

    def add_client(self, sock: socket.socket) -> None:
        with self.lock:
            self.clients.add(sock)

    def remove_client(self, sock: socket.socket) -> None:
        with self.lock:
            self.clients.discard(sock)

    def broadcast(self, payload: bytes) -> None:
        with self.lock:
            clients = list(self.clients)
        dead: list[socket.socket] = []
        for client in clients:
            try:
                client.sendall(payload)
            except OSError:
                dead.append(client)
        if dead:
            with self.lock:
                for client in dead:
                    self.clients.discard(client)
                    with contextlib.suppress(OSError):
                        client.close()


class MountpointRegistry:
    def __init__(self, mountpoints: dict[str, MountpointConfig]) -> None:
        self._mountpoints = {
            name: MountpointState(config) for name, config in mountpoints.items()
        }

    def require(self, name: str) -> MountpointState:
        state = self._mountpoints.get(name)
        if state is None:
            raise KeyError(name)
        return state

    @property
    def sourcetable(self) -> str:
        lines = []
        for name in sorted(self._mountpoints):
            str_line = (
                f"STR;{name};{name};RTCM 3;"
                f"1004(1),1005(10),1077(1),1087(1);"
                f"2;GPS+GLO;NONE;0;0;Anchor;0;0;N;N;0;"
            )
            lines.append(str_line)
        lines.append("ENDSOURCETABLE")
        return "\r\n".join(lines) + "\r\n"


def load_mountpoints(config_path: Path) -> dict[str, MountpointConfig]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ValueError("Mountpoint config must be a non-empty JSON object")

    mountpoints: dict[str, MountpointConfig] = {}
    for mount_name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Mountpoint {mount_name!r} must map to an object")
        source_password = entry.get("source_password")
        if not isinstance(source_password, str) or not source_password:
            raise ValueError(f"Mountpoint {mount_name!r} requires source_password")
        client_username = entry.get("client_username")
        client_password = entry.get("client_password")
        if client_username is not None and not isinstance(client_username, str):
            raise ValueError(
                f"Mountpoint {mount_name!r} client_username must be a string"
            )
        if client_password is not None and not isinstance(client_password, str):
            raise ValueError(
                f"Mountpoint {mount_name!r} client_password must be a string"
            )
        if (client_username is None) != (client_password is None):
            raise ValueError(
                f"Mountpoint {mount_name!r} must set both"
                f" client_username and client_password or neither"
            )
        mountpoints[mount_name] = MountpointConfig(
            source_password=source_password,
            client_username=client_username,
            client_password=client_password,
        )
    return mountpoints


def parse_headers(header_block: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in header_block.split("\r\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def parse_basic_auth(header_value: str | None) -> tuple[str, str] | None:
    if not header_value:
        return None
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "basic" or not token:
        return None
    try:
        decoded = base64.b64decode(token).decode("utf-8")
    except Exception:
        return None
    username, _, password = decoded.partition(":")
    return username, password


class NtripHandler(socketserver.BaseRequestHandler):
    registry: MountpointRegistry

    def handle(self) -> None:
        try:
            request_line, headers = self._read_handshake()
            if request_line.startswith("SOURCE "):
                self._handle_source(request_line)
                return
            if request_line.startswith("GET "):
                self._handle_client(request_line, headers)
                return
            self.request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        except ConnectionError:
            return
        except Exception:
            logger.exception("Failed to handle connection from %s", self.client_address)
            with contextlib.suppress(OSError):
                self.request.sendall(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")

    def _read_handshake(self) -> tuple[str, dict[str, str]]:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(4096)
            if not chunk:
                raise ConnectionError("Connection closed during handshake")
            data += chunk
            if len(data) > 16384:
                raise ValueError("Handshake too large")
        header_block = data.split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="replace")
        lines = header_block.split("\r\n")
        request_line = lines[0]
        headers = parse_headers("\r\n".join(lines[1:]))
        return request_line, headers

    def _handle_source(self, request_line: str) -> None:
        parts = request_line.split(" ")
        if len(parts) < 3:
            self.request.sendall(b"ERROR - Bad Request\r\n\r\n")
            return
        _, password, mount_raw = parts[:3]
        mount_name = mount_raw.lstrip("/")
        try:
            state = self.registry.require(mount_name)
        except KeyError:
            self.request.sendall(b"ERROR - Unknown Mountpoint\r\n\r\n")
            return
        if password != state.config.source_password:
            self.request.sendall(b"ERROR - Bad Password\r\n\r\n")
            return

        state.set_source(self.request)
        self.request.sendall(b"ICY 200 OK\r\n\r\n")
        logger.info(
            "Source connected for mountpoint %s from %s",
            mount_name,
            self.client_address,
        )
        try:
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                state.broadcast(chunk)
        finally:
            state.clear_source(self.request)
            logger.info("Source disconnected for mountpoint %s", mount_name)

    def _handle_client(self, request_line: str, headers: dict[str, str]) -> None:
        parts = request_line.split(" ")
        if len(parts) < 2:
            self.request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        path = parts[1]
        if path == "/" or path == "":
            body = self.registry.sourcetable.encode("utf-8")
            self.request.sendall(
                b"SOURCETABLE 200 OK\r\nContent-Type: text/plain\r\n\r\n" + body
            )
            return
        mount_name = path.lstrip("/")
        try:
            state = self.registry.require(mount_name)
        except KeyError:
            self.request.sendall(b"HTTP/1.1 404 Not Found\r\n\r\n")
            return

        if state.config.requires_client_auth:
            provided = parse_basic_auth(headers.get("authorization"))
            if provided != (state.config.client_username, state.config.client_password):
                self.request.sendall(
                    b"HTTP/1.1 401 Unauthorized\r\n"
                    b'WWW-Authenticate: Basic realm="NTRIP"\r\n\r\n'
                )
                return

        state.add_client(self.request)
        self.request.sendall(b"ICY 200 OK\r\n\r\n")
        logger.info(
            "Client connected for mountpoint %s from %s",
            mount_name,
            self.client_address,
        )
        try:
            while True:
                chunk = self.request.recv(1)
                if not chunk:
                    return
        finally:
            state.remove_client(self.request)
            logger.info("Client disconnected for mountpoint %s", mount_name)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("NTRIP_HOST", "0.0.0.0")
    port = int(os.environ.get("NTRIP_PORT", "2101"))
    config_path = Path(
        os.environ.get("MOUNTPOINT_CONFIG", "/app/config/mountpoints.json")
    )
    registry = MountpointRegistry(load_mountpoints(config_path))
    NtripHandler.registry = registry

    with ThreadedTCPServer((host, port), NtripHandler) as server:
        logger.info("NTRIP caster listening on %s:%s", host, port)
        logger.info(
            "Configured mountpoints: %s",
            ", ".join(sorted(load_mountpoints(config_path))),
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
