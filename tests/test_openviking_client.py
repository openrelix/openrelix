#!/usr/bin/env python3

import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asset_runtime  # noqa: E402
import openrelix_openviking  # noqa: E402


def runtime_paths_for_state(state_root):
    base = asset_runtime.get_runtime_paths()
    state_root = Path(state_root)
    return replace(
        base,
        state_root=state_root,
        raw_dir=state_root / "raw",
        raw_daily_dir=state_root / "raw" / "daily",
        raw_windows_dir=state_root / "raw" / "windows",
        registry_dir=state_root / "registry",
        reviews_dir=state_root / "reviews",
        reports_dir=state_root / "reports",
        consolidated_dir=state_root / "consolidated",
        consolidated_daily_dir=state_root / "consolidated" / "daily",
        runtime_dir=state_root / "runtime",
        nightly_runner_dir=state_root / "runtime" / "nightly-runner",
        nightly_codex_home=state_root / "runtime" / "codex-nightly-home",
        nightly_claude_home=state_root / "runtime" / "claude-nightly-home",
        log_dir=state_root / "log",
    )


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class FakeOpenVikingHandler(BaseHTTPRequestHandler):
    server_version = "FakeOpenViking/1"

    def log_message(self, *_args):
        return

    def _json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=None):
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
        )

    def do_GET(self):
        self._record()
        if self.path == "/health":
            self._send({"status": "ok", "healthy": True, "auth_mode": "api_key"})
            return
        if self.path == "/api/v1/tasks/task-1":
            self._send(
                {
                    "status": "ok",
                    "result": {
                        "task_id": "task-1",
                        "status": "completed",
                        "result": {
                            "archive_uri": "viking://session/orx-test/archives/archive_001",
                            "memories_extracted": 2,
                        },
                    },
                }
            )
            return
        if self.path.endswith("/archives/archive_001"):
            self._send(
                {
                    "status": "ok",
                    "result": {
                        "archive_id": "archive_001",
                        "uri": "viking://session/orx-test/archives/archive_001",
                        "abstract": "OpenViking extracted OpenRelix implementation memories.",
                        "overview": "OpenViking summarized route drift fixes and service-link setup.",
                    },
                }
            )
            return
        self._send({"status": "error", "error": {"message": self.path}}, status=404)

    def do_POST(self):
        body = self._json_body()
        self._record(body)
        if self.path == "/api/v1/sessions":
            self._send({"status": "ok", "result": {"session_id": body.get("session_id")}})
            return
        if self.path.endswith("/messages/batch"):
            self._send(
                {
                    "status": "ok",
                    "result": {
                        "session_id": self.path.split("/")[4],
                        "message_count": len(body.get("messages") or []),
                        "added": len(body.get("messages") or []),
                    },
                }
            )
            return
        if self.path.endswith("/commit"):
            self._send(
                {
                    "status": "ok",
                    "result": {
                        "status": "accepted",
                        "task_id": "task-1",
                        "archived": True,
                        "archive_uri": "viking://session/orx-test/archives/archive_001",
                    },
                }
            )
            return
        self._send({"status": "error", "error": {"message": self.path}}, status=404)


class FakeOpenVikingServer:
    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenVikingHandler)
        self.server.requests = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = "http://{}:{}".format(host, port)
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class OpenVikingClientTests(unittest.TestCase):
    def seed_openrelix_sources(self, paths):
        (paths.registry_dir / "memory_entries.jsonl").write_text(
            json.dumps(
                {
                    "date": "2026-05-26",
                    "source": "nightly_codex",
                    "bucket": "durable",
                    "scope": "project",
                    "injection_policy": "project_context",
                    "project_key": "openrelix",
                    "title": "OpenViking route",
                    "priority": "high",
                    "value_note": "Keep OpenViking summary docs separate from knowledge docs.",
                    "keywords": ["openviking", "summary"],
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        daily_dir = paths.consolidated_daily_dir / "2026-05-26"
        daily_dir.mkdir(parents=True)
        (daily_dir / "summary.json").write_text(
            json.dumps(
                {
                    "date": "2026-05-26",
                    "stage": "final",
                    "day_summary": "Built OpenViking summary sidecar.",
                    "window_summaries": [
                        {
                            "window_id": "w1",
                            "question_summary": "How to link OpenViking?",
                            "main_takeaway": "Use session commit and archive fetch.",
                            "project_keys": ["openrelix"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_summarize_pushes_openrelix_material_and_builds_summary_docs(self):
        with TemporaryDirectory() as tmpdir, FakeOpenVikingServer() as fake:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            self.seed_openrelix_sources(paths)
            connection = openrelix_openviking.OpenVikingConnection(
                url=fake.url,
                api_key="test-key",
                account="acct",
                user="user-1",
                agent_id="openrelix-test",
                timeout=5,
            )

            result = openrelix_openviking.summarize_openrelix_memory(
                paths=paths,
                connection=connection,
                date_from="2026-05-26",
                date_to="2026-05-26",
                project="openrelix",
                session_id="orx-test",
                task_timeout=5,
                poll_interval=0.01,
            )

            batch_request = next(item for item in fake.server.requests if item["path"].endswith("/messages/batch"))
            headers = {key.lower(): value for key, value in batch_request["headers"].items()}
            self.assertEqual(headers["x-api-key"], "test-key")
            self.assertEqual(headers["x-openviking-account"], "acct")
            self.assertEqual(headers["x-openviking-user"], "user-1")
            self.assertEqual(headers["x-openviking-agent"], "openrelix-test")
            self.assertGreaterEqual(len(batch_request["body"]["messages"]), 3)

            self.assertFalse(result["dry_run"])
            self.assertEqual(result["archive_id"], "archive_001")
            exports = read_jsonl(paths.registry_dir / "openviking_memory_exports.jsonl")
            self.assertEqual(len(exports), 1)
            self.assertEqual(exports[0]["task_id"], "task-1")
            self.assertEqual(exports[0]["metadata"]["memory_row_count"], 1)

            docs = read_jsonl(paths.registry_dir / "openviking_summary_docs.jsonl")
            self.assertEqual(len(docs), 1)
            body_text = (paths.state_root / docs[0]["body_path"]).read_text(encoding="utf-8")
            self.assertIn("OpenViking summarized route drift fixes", body_text)

    def test_summarize_falls_back_to_ov_add_memory_when_batch_api_is_missing(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            self.seed_openrelix_sources(paths)
            connection = openrelix_openviking.OpenVikingConnection(url="http://127.0.0.1:1933", timeout=5)

            def fake_request(self, method, path, body=None, query=None):
                if path == "/api/v1/sessions":
                    return {"session_id": body.get("session_id")}
                if path.endswith("/messages/batch"):
                    raise openrelix_openviking.OpenVikingError("OpenViking HTTP 404 for {}: Not Found".format(path))
                raise AssertionError("unexpected HTTP request: {} {}".format(method, path))

            def fake_run(command, **_kwargs):
                if command[1:3] == ["--agent-id", "openrelix"] and command[3:5] == ["session", "add-message"]:
                    return subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout=json.dumps({"ok": True, "result": {"session_id": "orx-test", "message_count": 1}}),
                        stderr="",
                    )
                if command[1:3] == ["--agent-id", "openrelix"] and command[3:5] == ["session", "commit"]:
                    return subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "ok": True,
                                "result": {
                                    "session_id": "orx-test",
                                    "status": "accepted",
                                    "task_id": "task-fallback",
                                    "archive_uri": "viking://session/orx-test/history/archive_001",
                                    "archived": True,
                                },
                            }
                        ),
                        stderr="",
                    )
                if command[1:3] == ["--agent-id", "openrelix"] and command[3:5] == ["session", "get-session-archive"]:
                    return subprocess.CompletedProcess(
                        args=command,
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "ok": True,
                                "result": {
                                    "archive_id": "archive_001",
                                    "uri": "viking://session/orx-test/history/archive_001",
                                    "overview": "OpenViking CLI fallback summarized Codex windows.",
                                    "abstract": "CLI fallback summary.",
                                },
                            },
                            ensure_ascii=False,
                        ),
                        stderr="",
                    )
                raise AssertionError("unexpected ov command: {}".format(command))

            with mock.patch.object(openrelix_openviking.OpenVikingHTTPClient, "_request", fake_request), mock.patch.object(
                openrelix_openviking.shutil,
                "which",
                return_value="/usr/local/bin/ov",
            ), mock.patch.object(openrelix_openviking.subprocess, "run", side_effect=fake_run) as run:
                result = openrelix_openviking.summarize_openrelix_memory(
                    paths=paths,
                    connection=connection,
                    date_from="2026-05-26",
                    date_to="2026-05-26",
                    project="openrelix",
                    session_id="orx-test",
                    task_timeout=5,
                    poll_interval=0.01,
                )

            self.assertEqual(result["add_result"]["transport"], "ov_cli")
            self.assertEqual(result["archive_id"], "archive_001")
            self.assertIn("--agent-id", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][0], "/usr/local/bin/ov")
            exports = read_jsonl(paths.registry_dir / "openviking_memory_exports.jsonl")
            self.assertEqual(exports[0]["uri"], "viking://session/orx-test/history/archive_001")
            docs = read_jsonl(paths.registry_dir / "openviking_summary_docs.jsonl")
            body_text = (paths.state_root / docs[0]["body_path"]).read_text(encoding="utf-8")
            self.assertIn("OpenViking CLI fallback summarized Codex windows", body_text)

    def test_setup_defaults_configures_and_summarizes_when_service_ready(self):
        with TemporaryDirectory() as tmpdir, FakeOpenVikingServer() as fake:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)
            self.seed_openrelix_sources(paths)

            result = openrelix_openviking.setup_openviking_defaults(
                paths=paths,
                url=fake.url,
                api_key="test-key",
                account="acct",
                user="user-1",
                agent_id="openrelix-test",
                write_ovcli=False,
                install_mode="never",
                run_backfill=False,
                date="2026-05-26",
                project="openrelix",
                task_timeout=5,
                poll_interval=0.01,
            )

            statuses = {step["name"]: step["status"] for step in result["steps"]}
            self.assertEqual(statuses["config"], "ok")
            self.assertEqual(statuses["install"], "skipped")
            self.assertEqual(statuses["backfill"], "skipped")
            self.assertEqual(statuses["health"], "ok")
            self.assertEqual(statuses["summarize"], "ok")
            docs = read_jsonl(paths.registry_dir / "openviking_summary_docs.jsonl")
            self.assertEqual(len(docs), 1)
            config = json.loads((paths.runtime_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["openviking_url"], fake.url)
            self.assertEqual(config["openviking_agent_id"], "openrelix-test")

    def test_setup_dry_run_fuses_backfill_command_without_writing_config(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)

            result = openrelix_openviking.setup_openviking_defaults(
                paths=paths,
                url="http://127.0.0.1:1933",
                install_mode="never",
                run_backfill=True,
                date_to="2026-05-26",
                days=3,
                project="openrelix",
                dry_run=True,
            )

            self.assertFalse((paths.runtime_dir / "config.json").exists())
            self.assertEqual(result["date_from"], "2026-05-24")
            backfill_step = next(step for step in result["steps"] if step["name"] == "backfill")
            self.assertEqual(backfill_step["status"], "dry_run")
            command = " ".join(backfill_step["command"])
            self.assertIn("openrelix.py backfill", command)
            self.assertIn("--from 2026-05-24 --to 2026-05-26", command)
            summarize_step = next(step for step in result["steps"] if step["name"] == "summarize")
            self.assertEqual(summarize_step["status"], "skipped")
            self.assertEqual(summarize_step["reason"], "no_source_material")

    def test_config_redacts_secret_in_return_payload(self):
        with TemporaryDirectory() as tmpdir:
            paths = runtime_paths_for_state(Path(tmpdir) / "state")
            asset_runtime.ensure_state_layout(paths)

            payload = openrelix_openviking.write_openviking_config(
                paths=paths,
                url="http://127.0.0.1:1933",
                api_key="test-key",
                account="acct",
                user="user-1",
                agent_id="agent-1",
                timeout=12,
            )

            config = json.loads((paths.runtime_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["openviking_api_key"], "test-key")
            self.assertEqual(payload["connection"]["api_key"], "[set]")
            self.assertEqual(payload["connection"]["agent_id"], "agent-1")

    def test_install_openviking_runs_pip_command(self):
        completed = subprocess.CompletedProcess(args=["python"], returncode=0)
        with mock.patch("openrelix_openviking.subprocess.run", return_value=completed) as run:
            payload = openrelix_openviking.install_openviking(
                package="openviking==1.2.3",
                python_bin="/usr/bin/python3",
                force_reinstall=False,
            )

        run.assert_called_once_with(
            ["/usr/bin/python3", "-m", "pip", "install", "openviking==1.2.3", "--upgrade"],
            check=False,
        )
        self.assertTrue(payload["ok"])


if __name__ == "__main__":
    raise SystemExit(unittest.main())
