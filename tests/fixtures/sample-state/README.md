# Sample OpenRelix State Fixture

This fixture is synthetic. It exists so contributors can test data contracts without reading one maintainer's real OpenRelix state root.

Rules:

- Keep every id, prompt, conclusion, path, and timestamp artificial.
- Use generic paths such as `/tmp/openrelix-demo`.
- Do not add host auth data, raw private transcripts, generated reports, screenshots, accounts, tokens, cookies, internal URLs, or proprietary snippets.
- Keep the fixture small enough for focused tests and docs examples.

Current coverage:

- `raw/daily/2026-04-28.json`
- `raw/windows/2026-04-28/w-demo-codex.json`
- `consolidated/daily/2026-04-28/summary.json`
- `registry/assets.jsonl`
- `registry/usage_events.jsonl`
- `registry/memory_entries.jsonl`

Validate with:

```bash
python3 -m unittest tests/test_sample_state_fixture.py
```
