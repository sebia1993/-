"""Serve the actual web app on loopback with isolated, disposable documentation data.

Use a browser manually. This tool never starts a TCP agent, enrolls a device,
discovers LAN interfaces, or changes the repository's config/data directories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as application
from app_version import APP_VERSION
from werkzeug.serving import make_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory for public synthetic fixtures and provenance, outside app storage.")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged port from 1024 through 65535")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sample = output / "sample-diagnostic.txt"
    sample.write_text("Synthetic documentation diagnostic. No company information.\n", encoding="utf-8")
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {"source_sha": source_sha, "product_version": APP_VERSION,
                "tool": "tools/serve_docs_demo.py", "os": platform.platform(),
                "bind": "127.0.0.1", "port": args.port, "synthetic_upload": True,
                "tcp_agent_enabled": False, "device_connections": False,
                "sample_sha256": hashlib.sha256(sample.read_bytes()).hexdigest()}
    (output / "demo-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="transfer-docs-") as directory:
        config = Path(directory) / "config.ini"
        config.write_text(f"[app]\nCONFIG_VERSION=3\nHOST=127.0.0.1\nPORT={args.port}\nSTORAGE_ROOT=uploads\n[network_probe]\nENABLED=false\n", encoding="utf-8")
        application.detect_lan_ip = lambda: "127.0.0.1"
        flask_app = application.create_app(config)
        server = make_server("127.0.0.1", args.port, flask_app, threaded=True)
        print(f"Documentation UI: http://127.0.0.1:{args.port} | synthetic file: {sample.name} | Ctrl+C stops and clears app data", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            flask_app.extensions["shutdown_network_measurements"]()
            server.server_close()


if __name__ == "__main__":
    main()
