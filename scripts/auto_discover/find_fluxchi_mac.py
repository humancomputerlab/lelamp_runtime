#!/usr/bin/env python3
"""
自动发现 FluxChi backend on LAN. 打印 Mac IP 到 stdout，失败 exit 1.
优先级:
  1) ~/.cache/fluxchi/mac_ip 里的缓存 IP
  2) Pi 自己 /24（同子段最快命中）
  3) Pi /20 内其它 /24 段
  4) Pi 所在 /16 的其它 /20 段（覆盖跨 /20 分配的网络）

探测条件: GET http://IP:8000/api/v1/pulse 返回 {"ok": true}
"""
import asyncio
import json
import os
import socket
import sys
from pathlib import Path

PORT = int(os.environ.get("FLUXCHI_PORT", "8000"))
TIMEOUT = float(os.environ.get("PROBE_TIMEOUT", "1.5"))
CACHE = Path.home() / ".cache" / "fluxchi" / "mac_ip"


async def probe(ip: str, sem: asyncio.Semaphore) -> str | None:
    async with sem:
        try:
            fut = asyncio.open_connection(ip, PORT)
            reader, writer = await asyncio.wait_for(fut, timeout=TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return None
        try:
            req = (
                f"GET /api/v1/pulse HTTP/1.0\r\n"
                f"Host: {ip}\r\nConnection: close\r\n\r\n"
            ).encode()
            writer.write(req)
            await writer.drain()
            data = await asyncio.wait_for(reader.read(2048), timeout=TIMEOUT)
            if b'"ok":true' in data and b"stamina" in data:
                return ip
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        return None


def get_my_ip() -> str | None:
    for ifname in ("wlan0", "eth0"):
        try:
            import subprocess
            out = subprocess.run(
                ["ip", "-4", "addr", "show", ifname],
                capture_output=True, text=True, timeout=3,
            ).stdout
            for line in out.split("\n"):
                line = line.strip()
                if line.startswith("inet "):
                    return line.split()[1].split("/")[0]
        except Exception:
            continue
    # fallback: connect to a public IP to learn local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


async def scan_range(ips: list[str], concurrency: int = 512) -> str | None:
    sem = asyncio.Semaphore(concurrency)
    tasks = [asyncio.create_task(probe(ip, sem)) for ip in ips]
    for done in asyncio.as_completed(tasks):
        result = await done
        if result:
            # cancel rest
            for t in tasks:
                t.cancel()
            return result
    return None


async def main():
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    # 1) cache
    if CACHE.exists():
        cached = CACHE.read_text().strip()
        if cached:
            sem = asyncio.Semaphore(1)
            print(f"[resolve] trying cache: {cached}", file=sys.stderr)
            if await probe(cached, sem):
                print(cached)
                return 0

    my_ip = get_my_ip()
    if not my_ip:
        print("[resolve] no local IP", file=sys.stderr)
        return 1
    print(f"[resolve] my IP: {my_ip}", file=sys.stderr)

    parts = my_ip.split(".")
    prefix16 = ".".join(parts[:2])      # e.g. 10.161
    prefix24 = ".".join(parts[:3])      # e.g. 10.161.139
    oct3 = int(parts[2])
    block20 = oct3 & 0xF0               # /20 start

    # 2) Pi 自己 /24
    print(f"[resolve] scanning own /24 {prefix24}.0/24", file=sys.stderr)
    ips = [f"{prefix24}.{i}" for i in range(1, 255)]
    found = await scan_range(ips)
    if found:
        CACHE.write_text(found)
        print(found)
        return 0

    # 3) Pi /20（剩余 /24 段）
    print(f"[resolve] scanning own /20 {prefix16}.{block20}.0 - .{block20 + 15}.255", file=sys.stderr)
    ips = []
    for o3 in range(block20, block20 + 16):
        if o3 == oct3:
            continue
        ips.extend(f"{prefix16}.{o3}.{i}" for i in range(1, 255))
    found = await scan_range(ips)
    if found:
        CACHE.write_text(found)
        print(found)
        return 0

    # 4) Pi /16（其它 /20 段）—— 4096 IPs/段 * 15 段 = 61440 IPs
    print(f"[resolve] scanning rest of /16 {prefix16}.0.0/16 (slow)", file=sys.stderr)
    for block_start in range(0, 256, 16):
        if block_start == block20:
            continue
        print(f"[resolve]   /20 block {prefix16}.{block_start}.x - .{block_start + 15}.x", file=sys.stderr)
        ips = []
        for o3 in range(block_start, block_start + 16):
            ips.extend(f"{prefix16}.{o3}.{i}" for i in range(1, 255))
        found = await scan_range(ips, concurrency=384)
        if found:
            CACHE.write_text(found)
            print(found)
            return 0

    print("[resolve] no FluxChi backend found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
