#!/usr/bin/env python3

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_node(script):
    return subprocess.run(
        ["node", "--input-type=module", "-e", textwrap.dedent(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


class PostHogWorkerTests(unittest.TestCase):
    def test_valid_event_is_sanitized_and_forwarded_to_posthog_batch(self):
        result = run_node(
            """
            import assert from "node:assert/strict";
            import worker from "./analytics/posthog-worker/worker.mjs";

            const env = {
              OPENRELIX_ANALYTICS_INGEST_TOKEN: "ingest-test-token",
              POSTHOG_API_KEY: "phc_test_project_token",
              POSTHOG_HOST: "https://us.i.posthog.com",
            };
            let captured = null;
            globalThis.fetch = async (url, options) => {
              captured = { url: String(url), body: JSON.parse(options.body) };
              return new Response(JSON.stringify({ ok: true }), { status: 200 });
            };

            const sourceEvent = {
              schema_version: 1,
              app: "openrelix_macos",
              event: "module_hidden",
              install_id: "12345678-1234-1234-1234-123456789abc",
              session_id: "abcdef12-3456-7890-abcd-ef1234567890",
              ts: "2026-06-05T00:00:00.000Z",
              app_version: "1.2.3",
              os: "macOS",
              os_version: "15.5",
              properties: {
                module_id: "personal_asset_memory",
                dwell_ms: 1234,
                reason: "page_hidden",
                prompt: "must be dropped",
                file_path: "/private/tmp/must-not-forward",
              },
            };
            const response = await worker.fetch(new Request("https://collector.example/events", {
              method: "POST",
              headers: {
                authorization: "Bearer ingest-test-token",
                "content-type": "application/json",
              },
              body: JSON.stringify({ events: [sourceEvent] }),
            }), env);
            const payload = await response.json();

            assert.equal(response.status, 200);
            assert.deepEqual(payload, { ok: true, accepted: 1, rejected: 0 });
            assert.equal(captured.url, "https://us.i.posthog.com/batch/");
            assert.equal(captured.body.api_key, "phc_test_project_token");
            assert.equal(captured.body.historical_migration, false);
            assert.equal(captured.body.batch.length, 1);

            const posthogEvent = captured.body.batch[0];
            assert.equal(posthogEvent.event, "openrelix_module_hidden");
            assert.equal(posthogEvent.timestamp, "2026-06-05T00:00:00.000Z");
            assert.equal(posthogEvent.properties.distinct_id, sourceEvent.install_id);
            assert.equal(posthogEvent.properties.$process_person_profile, false);
            assert.equal(posthogEvent.properties.$lib, "openrelix-posthog-worker");
            assert.equal(posthogEvent.properties.openrelix_app, "openrelix_macos");
            assert.equal(posthogEvent.properties.openrelix_session_id, sourceEvent.session_id);
            assert.equal(posthogEvent.properties.openrelix_app_version, "1.2.3");
            assert.equal(posthogEvent.properties.openrelix_os, "macOS");
            assert.equal(posthogEvent.properties.openrelix_os_version, "15.5");
            assert.equal(posthogEvent.properties.module_id, "personal_asset_memory");
            assert.equal(posthogEvent.properties.dwell_ms, 1234);
            assert.equal(posthogEvent.properties.reason, "page_hidden");
            assert.equal(posthogEvent.properties.prompt, undefined);
            assert.equal(posthogEvent.properties.file_path, undefined);
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_event_is_rejected_without_forwarding(self):
        result = run_node(
            """
            import assert from "node:assert/strict";
            import worker from "./analytics/posthog-worker/worker.mjs";

            const env = {
              OPENRELIX_ANALYTICS_INGEST_TOKEN: "ingest-test-token",
              POSTHOG_API_KEY: "phc_test_project_token",
            };
            let forwarded = false;
            globalThis.fetch = async () => {
              forwarded = true;
              return new Response("{}", { status: 200 });
            };

            const response = await worker.fetch(new Request("https://collector.example/events", {
              method: "POST",
              headers: { authorization: "Bearer ingest-test-token" },
              body: JSON.stringify({
                events: [{
                  app: "openrelix_macos",
                  event: "raw_prompt",
                  install_id: "12345678-1234-1234-1234-123456789abc",
                  session_id: "abcdef12-3456-7890-abcd-ef1234567890",
                  properties: { prompt: "must not leave the client" },
                }],
              }),
            }), env);
            const payload = await response.json();

            assert.equal(response.status, 422);
            assert.equal(payload.ok, false);
            assert.equal(payload.accepted, 0);
            assert.equal(payload.rejected, 1);
            assert.equal(payload.error, "no_valid_events");
            assert.equal(forwarded, false);
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unauthorized_request_is_rejected_without_forwarding(self):
        result = run_node(
            """
            import assert from "node:assert/strict";
            import worker from "./analytics/posthog-worker/worker.mjs";

            const env = {
              OPENRELIX_ANALYTICS_INGEST_TOKEN: "ingest-test-token",
              POSTHOG_API_KEY: "phc_test_project_token",
            };
            let forwarded = false;
            globalThis.fetch = async () => {
              forwarded = true;
              return new Response("{}", { status: 200 });
            };

            const response = await worker.fetch(new Request("https://collector.example/events", {
              method: "POST",
              headers: { authorization: "Bearer wrong-token" },
              body: JSON.stringify({ events: [] }),
            }), env);
            const payload = await response.json();

            assert.equal(response.status, 401);
            assert.equal(payload.error, "unauthorized");
            assert.equal(forwarded, false);
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
