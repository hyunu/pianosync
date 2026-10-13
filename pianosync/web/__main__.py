"""``python -m pianosync.web`` entry point."""

from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser(description="Piano Sync Switcher (web)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    import uvicorn

    os.environ.setdefault("PIANOSYNC_WEB_ROOT", os.path.join(os.path.expanduser("~"), ".pianosync", "web"))
    print(f"Piano Sync Switcher 웹 서버: http://{args.host}:{args.port}")
    if args.reload:
        uvicorn.run("pianosync.web.server:app", host=args.host, port=args.port, reload=True)
    else:
        from .server import app

        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
