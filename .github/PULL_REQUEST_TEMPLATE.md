## Summary

- What changed:
- Why:

## Change Type

- [ ] Docs only
- [ ] Python scripts
- [ ] Installer or LaunchAgent
- [ ] Overview or panel
- [ ] Memory policy or host context
- [ ] Package surface
- [ ] Release or site
- [ ] Other:

## Data And Privacy

- Reads from:
- Writes to:
- Public/package surface changed: yes / no
- Host context changed: yes / no
- Connector permissions changed: yes / no

Confirm:

- [ ] No secrets, tokens, cookies, account identifiers, raw host history, private logs, or real user state are included.
- [ ] No personal home paths or internal-only URLs are included.
- [ ] Any examples, screenshots, fixtures, or docs are synthetic or sanitized.
- [ ] Host-owned memory is preserved outside OpenRelix-managed blocks.

## Verification

Common:

```bash
python3 scripts/check_personal_info.py
git diff --check
```

Focused checks run:

```bash
# paste commands here
```

Skipped checks and why:

- 

## Docs

- [ ] README/docs updated when behavior or workflow changed.
- [ ] Data contracts updated when state shape changed.
- [ ] Privacy docs updated when storage, connector, package, or host-context boundary changed.
- [ ] Changelog or release notes updated when this is release-facing.

## Reviewer Notes

- Risks:
- Follow-ups:
