"""Exercise safe_fetch through real HTTPX/httpcore with only DNS and TCP replaced."""
import socket
import ssl

import httpcore
import httpx
import pytest

from src.search.http import HttpClient

# Preserve the real transport before conftest's offline guard replaces it.
REAL_HANDLE = httpx.HTTPTransport.handle_request
PUBLIC = "93.184.216.34"


def install_network(monkeypatch, answers, responses):
    dns, connections, streams = [], [], []

    def resolve(host, *args, **kwargs):
        dns.append(host)
        ips = answers(host, len(dns)) if callable(answers) else answers
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]

    class Stream:
        def __init__(self, data):
            self.data, self.written, self.tls, self.closed = data, b"", None, False

        def read(self, max_bytes, timeout=None):
            data, self.data = self.data[:max_bytes], self.data[max_bytes:]
            return data

        def write(self, buffer, timeout=None):
            self.written += buffer

        def close(self):
            self.closed = True

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            self.tls = (server_hostname, ssl_context.check_hostname, ssl_context.verify_mode)
            return self

        def get_extra_info(self, info):
            return None

    def connect(self, host, port, **kwargs):
        connections.append((host, port))
        if not responses:
            raise AssertionError("Unexpected TCP connection")
        data = responses.pop(0)
        if isinstance(data, Exception):
            raise data
        stream = Stream(data)
        streams.append(stream)
        return stream

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", REAL_HANDLE)
    return dns, connections, streams


def reply(body=b"ok", status="200 OK", extra=b"", content_type=b"text/html"):
    return (b"HTTP/1.1 " + status.encode() + b"\r\nContent-Type: " + content_type +
            b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n" + extra + b"\r\n" + body)


def test_rebinding_cannot_change_validated_connection(monkeypatch):
    dns, connections, streams = install_network(monkeypatch,
        lambda host, call: [PUBLIC] if call == 1 else ["127.0.0.1"], [reply()])
    assert HttpClient().safe_fetch("https://research.example/article") == "ok"
    assert connections == [(PUBLIC, 443)]
    assert dns == ["research.example"]
    assert streams[0].tls == ("research.example", True, ssl.CERT_REQUIRED)
    assert b"Host: research.example\r\n" in streams[0].written
    assert b"GET /article HTTP/1.1" in streams[0].written
    assert streams[0].closed


def test_relative_redirect_uses_original_hostname_and_revalidates(monkeypatch):
    dns, connections, streams = install_network(monkeypatch, [PUBLIC], [
        reply(status="302 Found", extra=b"Location: /next\r\n"), reply(b"next")])
    assert HttpClient().safe_fetch("https://research.example/start") == "next"
    assert dns == ["research.example", "research.example"]
    assert connections == [(PUBLIC, 443), (PUBLIC, 443)]
    assert b"GET /next HTTP/1.1" in streams[1].written
    assert all(s.closed for s in streams)


@pytest.mark.parametrize("target", ["http://127.0.0.1/", "http://169.254.169.254/", "https://private.example/"])
def test_redirect_to_private_address_never_connects(monkeypatch, target):
    dns, connections, _ = install_network(monkeypatch,
        lambda host, call: [PUBLIC] if call == 1 else ["10.0.0.1"],
        [reply(status="302 Found", extra=f"Location: {target}\r\n".encode())])
    assert HttpClient().safe_fetch("https://research.example/") is None
    assert connections == [(PUBLIC, 443)]


@pytest.mark.parametrize("addresses", [[PUBLIC, "10.0.0.1"], [], ["::ffff:127.0.0.1"], ["100.64.0.1"]])
def test_mixed_empty_and_non_global_dns_answers_blocked(monkeypatch, addresses):
    _, connections, _ = install_network(monkeypatch, addresses, [])
    assert HttpClient().safe_fetch("https://research.example/") is None
    assert connections == []


def test_public_ipv6_and_custom_port_keep_host_header(monkeypatch):
    ip = "2606:4700:4700::1111"
    _, connections, streams = install_network(monkeypatch, [ip], [reply()])
    assert HttpClient().safe_fetch("https://research.example:8443/") == "ok"
    assert connections == [(ip, 8443)]
    assert b"Host: research.example:8443\r\n" in streams[0].written
    assert streams[0].tls[0] == "research.example"


def test_proxy_environment_does_not_bypass_pinning(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "")
    _, connections, _ = install_network(monkeypatch, [PUBLIC], [reply()])
    assert HttpClient().safe_fetch("https://research.example/") == "ok"
    assert connections == [(PUBLIC, 443)]


@pytest.mark.parametrize("response", [reply(content_type=b"application/pdf"), reply(status="404 Not Found"),
                                      httpcore.ConnectTimeout("timeout")])
def test_unavailable_and_unsupported_fetch_returns_none(monkeypatch, response):
    install_network(monkeypatch, [PUBLIC], [response])
    assert HttpClient().safe_fetch("https://research.example/") is None


def test_redirect_limit_is_preserved(monkeypatch):
    _, connections, _ = install_network(monkeypatch, [PUBLIC], [
        reply(status="302 Found", extra=b"Location: /again\r\n") for _ in range(2)])
    assert HttpClient().safe_fetch("https://research.example/", max_redirects=1) is None
    assert len(connections) == 2


def test_oversized_fetch_remains_capped(monkeypatch):
    import src.search.http as module
    monkeypatch.setattr(module, "MAX_FETCH_BYTES", 2)
    install_network(monkeypatch, [PUBLIC], [reply(b"large")])
    assert HttpClient().safe_fetch("https://research.example/") == ""


def test_rebinding_on_same_host_redirect_is_blocked(monkeypatch):
    dns, connections, _ = install_network(monkeypatch,
        lambda host, call: [PUBLIC] if call == 1 else ["127.0.0.1"],
        [reply(status="302 Found", extra=b"Location: /private\r\n")])
    assert HttpClient().safe_fetch("https://research.example/") is None
    assert len(dns) == 2 and connections == [(PUBLIC, 443)]


def test_cross_host_redirect_on_shared_ip_gets_own_tls_identity(monkeypatch):
    _, connections, streams = install_network(monkeypatch, [PUBLIC], [
        reply(status="302 Found", extra=b"Location: https://other.example/final\r\n"), reply()])
    assert HttpClient().safe_fetch("https://research.example/") == "ok"
    assert len(connections) == 2
    assert [s.tls[0] for s in streams] == ["research.example", "other.example"]
    assert b"Host: other.example\r\n" in streams[1].written
