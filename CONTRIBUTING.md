# Contributing

Thanks for helping improve OpenRelix.

## Scope

This project keeps reusable capabilities in the repository and user-specific state outside the repository.

Good contribution targets:

- installer and setup scripts
- reusable skills
- templates and documentation
- state-root path handling
- macOS LaunchAgent templates
- tests for consolidation, overview, and installer helper behavior

Avoid contributing:

- raw Codex history
- local logs or generated reports
- personal registry entries or reviews
- secrets, tokens, credentials, cookies, or account data
- private internal code or unsanitized proprietary logs

## Development

Run focused validation before sending a change:

```bash
python3 -m unittest discover -s tests
zsh -n install/install.sh
```

If your change touches JSON metadata, validate it with:

```bash
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
for f in plugins/*/.codex-plugin/plugin.json; do python3 -m json.tool "$f" >/dev/null; done
```

## Pull Requests

Keep pull requests small and explain:

- what changed
- how it was validated
- whether it touches repo capabilities, user state, or both
- any migration needed for existing local installs

Use sanitized examples in issues and pull requests.
