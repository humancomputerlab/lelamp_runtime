"""Reachable local URL helpers for the dashboard."""

from __future__ import annotations

import socket


def build_reachable_urls(host: str, port: int, *, ip_list: list[str] | None = None) -> list[str]:
    urls: list[str] = []

    if host == "0.0.0.0":
        urls.append(f"http://127.0.0.1:{port}")
        addresses = _local_ipv4_addresses() if ip_list is None else ip_list
        for address in addresses:
            urls.append(f"http://{_format_host(address)}:{port}")
    elif host == "::":
        urls.append(f"http://[::1]:{port}")
        addresses = _local_ipv4_addresses() if ip_list is None else ip_list
        for address in addresses:
            urls.append(f"http://{_format_host(address)}:{port}")
    else:
        urls.append(f"http://{_format_host(host)}:{port}")

    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _local_ipv4_addresses() -> list[str]:
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        return []

    return [
        address
        for address in addresses
        if "." in address and not address.startswith("127.")
    ]


def _format_host(address: str) -> str:
    if ":" in address and not address.startswith("["):
        return f"[{address}]"
    return address
