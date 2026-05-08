"""Tests for the NTRIP caster."""
from __future__ import annotations

import base64
import json
import socket
import threading
import time

import pytest

from app import (
    MountpointConfig,
    MountpointRegistry,
    MountpointState,
    NtripHandler,
    ThreadedTCPServer,
    load_mountpoints,
    parse_basic_auth,
    parse_headers,
)

# --- Unit tests for helpers ---


class TestParseHeaders:
    def test_basic(self):
        raw = "Content-Type: text/plain\r\nHost: localhost"
        result = parse_headers(raw)
        assert result == {"content-type": "text/plain", "host": "localhost"}

    def test_empty(self):
        assert parse_headers("") == {}

    def test_no_colon_lines_skipped(self):
        raw = "GET / HTTP/1.1\r\nHost: localhost"
        result = parse_headers(raw)
        assert result == {"host": "localhost"}


class TestParseBasicAuth:
    def test_valid(self):
        token = base64.b64encode(b"user:pass").decode()
        assert parse_basic_auth(f"Basic {token}") == ("user", "pass")

    def test_none_input(self):
        assert parse_basic_auth(None) is None

    def test_wrong_scheme(self):
        assert parse_basic_auth("Bearer token123") is None

    def test_empty_token(self):
        assert parse_basic_auth("Basic ") is None

    def test_no_password(self):
        token = base64.b64encode(b"user:").decode()
        assert parse_basic_auth(f"Basic {token}") == ("user", "")


class TestLoadMountpoints:
    def test_valid_config(self, tmp_path):
        config = {
            "BASE1": {"source_password": "secret123"},
            "BASE2": {
                "source_password": "pw",
                "client_username": "rover",
                "client_password": "roverpass",
            },
        }
        path = tmp_path / "mountpoints.json"
        path.write_text(json.dumps(config))

        result = load_mountpoints(path)
        assert "BASE1" in result
        assert result["BASE1"].source_password == "secret123"
        assert result["BASE1"].requires_client_auth is False
        assert result["BASE2"].requires_client_auth is True

    def test_empty_config_raises(self, tmp_path):
        path = tmp_path / "mountpoints.json"
        path.write_text("{}")
        with pytest.raises(ValueError, match="non-empty"):
            load_mountpoints(path)

    def test_missing_source_password_raises(self, tmp_path):
        path = tmp_path / "mountpoints.json"
        path.write_text(json.dumps({"BASE1": {}}))
        with pytest.raises(ValueError, match="source_password"):
            load_mountpoints(path)

    def test_partial_client_auth_raises(self, tmp_path):
        path = tmp_path / "mountpoints.json"
        path.write_text(
            json.dumps({"BASE1": {"source_password": "x", "client_username": "u"}})
        )
        with pytest.raises(ValueError, match="both"):
            load_mountpoints(path)


class TestMountpointState:
    def test_broadcast_to_clients(self):
        config = MountpointConfig(source_password="pw")
        state = MountpointState(config)

        # Create a socket pair to simulate a client
        server_sock, client_sock = socket.socketpair()
        try:
            state.add_client(server_sock)
            state.broadcast(b"RTCM_DATA")
            data = client_sock.recv(1024)
            assert data == b"RTCM_DATA"
        finally:
            server_sock.close()
            client_sock.close()

    def test_broadcast_removes_dead_clients(self):
        config = MountpointConfig(source_password="pw")
        state = MountpointState(config)

        server_sock, client_sock = socket.socketpair()
        client_sock.close()  # Close receiving end to make send fail
        state.add_client(server_sock)

        # Should not raise, just remove the dead client
        state.broadcast(b"DATA")
        assert server_sock not in state.clients
        server_sock.close()

    def test_set_and_clear_source(self):
        config = MountpointConfig(source_password="pw")
        state = MountpointState(config)

        sock = socket.socket()
        state.set_source(sock)
        assert state.source is sock
        state.clear_source(sock)
        assert state.source is None
        sock.close()


class TestMountpointRegistry:
    def test_require_existing(self):
        registry = MountpointRegistry({"BASE1": MountpointConfig("pw")})
        state = registry.require("BASE1")
        assert isinstance(state, MountpointState)

    def test_require_missing_raises(self):
        registry = MountpointRegistry({"BASE1": MountpointConfig("pw")})
        with pytest.raises(KeyError):
            registry.require("NONEXISTENT")

    def test_sourcetable(self):
        registry = MountpointRegistry({"BASE1": MountpointConfig("pw")})
        table = registry.sourcetable
        assert "STR;BASE1;" in table
        assert "ENDSOURCETABLE" in table


# --- Integration tests with a real TCP server ---


@pytest.fixture()
def caster_server(tmp_path):
    """Start a real NTRIP caster on a random port."""
    config = {
        "BASE1": {"source_password": "secret"},
        "SECURE": {
            "source_password": "srcpw",
            "client_username": "user",
            "client_password": "pass",
        },
    }
    config_path = tmp_path / "mountpoints.json"
    config_path.write_text(json.dumps(config))

    registry = MountpointRegistry(load_mountpoints(config_path))
    NtripHandler.registry = registry

    server = ThreadedTCPServer(("127.0.0.1", 0), NtripHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


def _connect(port: int) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.settimeout(3)
    return sock


class TestCasterIntegration:
    def test_sourcetable(self, caster_server):
        sock = _connect(caster_server)
        sock.sendall(b"GET / HTTP/1.1\r\n\r\n")
        data = sock.recv(4096).decode()
        sock.close()
        assert "SOURCETABLE 200 OK" in data
        assert "STR;BASE1;" in data
        assert "ENDSOURCETABLE" in data

    def test_source_upload_and_client_receive(self, caster_server):
        # Connect source
        source = _connect(caster_server)
        source.sendall(b"SOURCE secret /BASE1\r\n\r\n")
        resp = source.recv(1024)
        assert b"ICY 200 OK" in resp

        # Connect client
        client = _connect(caster_server)
        client.sendall(b"GET /BASE1 HTTP/1.1\r\n\r\n")
        resp = client.recv(1024)
        assert b"ICY 200 OK" in resp

        # Source sends data, client receives it
        time.sleep(0.05)
        source.sendall(b"RTCM_CORRECTION_BYTES")
        data = client.recv(1024)
        assert data == b"RTCM_CORRECTION_BYTES"

        source.close()
        client.close()

    def test_wrong_source_password(self, caster_server):
        sock = _connect(caster_server)
        sock.sendall(b"SOURCE wrongpw /BASE1\r\n\r\n")
        resp = sock.recv(1024)
        assert b"ERROR - Bad Password" in resp
        sock.close()

    def test_unknown_mountpoint_source(self, caster_server):
        sock = _connect(caster_server)
        sock.sendall(b"SOURCE secret /NONEXIST\r\n\r\n")
        resp = sock.recv(1024)
        assert b"ERROR - Unknown Mountpoint" in resp
        sock.close()

    def test_unknown_mountpoint_client(self, caster_server):
        sock = _connect(caster_server)
        sock.sendall(b"GET /NONEXIST HTTP/1.1\r\n\r\n")
        resp = sock.recv(1024)
        assert b"404" in resp
        sock.close()

    def test_client_auth_required(self, caster_server):
        # No auth
        sock = _connect(caster_server)
        sock.sendall(b"GET /SECURE HTTP/1.1\r\n\r\n")
        resp = sock.recv(1024)
        assert b"401" in resp
        sock.close()

        # Wrong auth
        bad_token = base64.b64encode(b"user:wrong").decode()
        sock = _connect(caster_server)
        sock.sendall(
            f"GET /SECURE HTTP/1.1\r\nAuthorization: Basic {bad_token}\r\n\r\n".encode()
        )
        resp = sock.recv(1024)
        assert b"401" in resp
        sock.close()

        # Correct auth
        good_token = base64.b64encode(b"user:pass").decode()
        sock = _connect(caster_server)
        msg = f"GET /SECURE HTTP/1.1\r\nAuthorization: Basic {good_token}\r\n\r\n"
        sock.sendall(msg.encode())
        resp = sock.recv(1024)
        assert b"ICY 200 OK" in resp
        sock.close()

    def test_multiple_clients_receive_broadcast(self, caster_server):
        # Source
        source = _connect(caster_server)
        source.sendall(b"SOURCE secret /BASE1\r\n\r\n")
        assert b"ICY 200 OK" in source.recv(1024)

        # Two clients
        c1 = _connect(caster_server)
        c1.sendall(b"GET /BASE1 HTTP/1.1\r\n\r\n")
        assert b"ICY 200 OK" in c1.recv(1024)

        c2 = _connect(caster_server)
        c2.sendall(b"GET /BASE1 HTTP/1.1\r\n\r\n")
        assert b"ICY 200 OK" in c2.recv(1024)

        time.sleep(0.05)
        source.sendall(b"BROADCAST_DATA")

        assert c1.recv(1024) == b"BROADCAST_DATA"
        assert c2.recv(1024) == b"BROADCAST_DATA"

        source.close()
        c1.close()
        c2.close()
