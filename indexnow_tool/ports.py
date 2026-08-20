from __future__ import annotations

import socket

DEFAULT_HOST = "localhost"


def resolve_bind_addresses(host: str, port: int) -> list[tuple[int, tuple]]:
    """Every address a server must bind for `host` to be reachable.

    `localhost` resolves to both ::1 and 127.0.0.1, and on Windows the IPv6 entry
    usually comes first. Binding only 127.0.0.1 means a browser that picks ::1 gets
    a refused connection, which looks exactly like the server never started.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE)
    seen: set[tuple] = set()
    addresses: list[tuple[int, tuple]] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6) or sockaddr in seen:
            continue
        seen.add(sockaddr)
        addresses.append((family, sockaddr))
    return addresses


def _port_is_free(host: str, port: int) -> bool:
    """True only if every address for `host` can take this port."""
    probes: list[socket.socket] = []
    try:
        for family, sockaddr in resolve_bind_addresses(host, port):
            sock = socket.socket(family, socket.SOCK_STREAM)
            probes.append(sock)
            if family == socket.AF_INET6:
                # Match what the server does, so the v6 probe cannot mask a busy v4 port.
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            # No SO_REUSEADDR: the point of the probe is to fail when the port is taken.
            sock.bind((sockaddr[0], port, *sockaddr[2:]))
        return True
    except OSError:
        return False
    finally:
        for sock in probes:
            sock.close()


def find_free_port(host: str = DEFAULT_HOST, start_port: int = 8787, max_tries: int = 300) -> int:
    for port in range(start_port, start_port + max_tries):
        if _port_is_free(host, port):
            return port
    raise RuntimeError(
        f"Could not find a free port for {host} in "
        f"{start_port}-{start_port + max_tries - 1}."
    )
