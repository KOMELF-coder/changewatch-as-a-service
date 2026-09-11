"""Run the real Actor SDK in separate processes against a controlled HTTP server."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def test_sdk_persistence(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        page = b"<p>Monthly plan price $10</p>"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.page)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    storage = tmp_path / "storage"
    input_path = storage / "key_value_stores" / "default" / "INPUT.json"
    input_path.parent.mkdir(parents=True)
    input_path.write_text(
        json.dumps(
            {
                "client_id": "smoke",
                "competitors": [
                    {"name": "Fixture", "url": f"http://127.0.0.1:{server.server_port}/pricing"}
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("APIFY_", "ACTOR_", "CRAWLEE_"))
    }
    env.update(APIFY_LOCAL_STORAGE_DIR=str(storage), APIFY_PURGE_ON_START="false")
    try:
        for expected, page in [
            ("initialized", b"<p>Monthly plan price $10</p>"),
            ("unchanged", b"<p>Monthly plan price $10</p>"),
            ("changed", b"<p>Monthly plan price $20</p>"),
        ]:
            Handler.page = page
            result = subprocess.run(
                [sys.executable, "-m", "src"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=40,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            records = sorted((storage / "datasets" / "default").glob("[0-9]*.json"))
            assert records, result.stdout + result.stderr
            assert json.loads(records[-1].read_text(encoding="utf-8"))["status"] == expected
            if expected == "changed":
                alert = json.loads(records[-1].read_text(encoding="utf-8"))
                assert alert["change_type"] == "price_change"
                assert alert["old_price"] == 10
                assert alert["new_price"] == 20
                assert alert["price_change_percent"] == 100
                assert alert["importance_score"] >= 90
                assert alert["alert_triggered"]
                assert not alert["alert_sent"]
                assert alert["alert_body"]
                assert '<html lang="en">' in alert["alert_html"]
                assert alert["email_error"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
