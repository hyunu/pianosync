"""``python -m pianosync.web`` entry point."""

from __future__ import annotations

import argparse
import os
import socket
from typing import List


def _lan_base_urls(port: int) -> List[str]:
    """Best-effort LAN URLs for phones on the same Wi‑Fi."""
    urls: List[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                urls.append(f"http://{ip}:{port}")
    except OSError:
        pass
    return urls


def _print_listen_hints(host: str, port: int) -> None:
    print(f"Piano Sync Switcher 웹 서버: http://{host}:{port}")
    lan = _lan_base_urls(port)
    if host in ("0.0.0.0", "::"):
        if lan:
            print("  · 같은 Wi‑Fi의 iPhone/Android (Safari/Chrome):")
            for url in lan:
                print(f"      {url}")
            print("  · iPhone: Safari → 공유 → 홈 화면에 추가  |  Android: Chrome → 앱 설치")
        else:
            print("  · 휴대폰: 이 Mac/PC의 LAN IP와 포트로 접속 (--host 0.0.0.0)")
    elif host in ("127.0.0.1", "localhost") and lan:
        print(f"  · 휴대폰에서 쓰려면: python -m pianosync.web --host 0.0.0.0 --port {port}")
        print(f"    (예상 LAN 주소: {lan[0]})")
    print("  · 자세한 설명: docs/mobile-app.md")


def main() -> int:
    parser = argparse.ArgumentParser(description="Piano Sync Switcher (web)")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (0.0.0.0 = same Wi‑Fi phones can connect)",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    os.environ.setdefault("PIANOSYNC_WEB_ROOT", os.path.join(os.path.expanduser("~"), ".pianosync", "web"))
    _print_listen_hints(args.host, args.port)
    if args.reload:
        uvicorn.run("pianosync.web.server:app", host=args.host, port=args.port, reload=True)
    else:
        from .server import app

        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
