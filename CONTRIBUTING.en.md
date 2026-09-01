# Contributing Guide

[简体中文](CONTRIBUTING.md) | [English](CONTRIBUTING.en.md)

## Development workflow

1. Create a short-lived branch from `main`.
2. Preserve the responsibility boundaries between Steps 1–7. Do not generate downstream conclusions in upstream steps.
3. When changing a data contract, update `references/contracts.md` and the related tests together.
4. Platform-adapter changes must update the adapter version and preserve replayability for existing evidence.
5. Run the full test suite and privacy scan before submitting a change.

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q geo_monitoring tests scripts
node --check scripts/browser_evidence_collector.mjs
```

## Pull request requirements

- Explain the problem, scope of the change, and validation performed.
- State whether the change affects data contracts, compatibility, platform selectors, or reporting definitions.
- Never commit real customer data, monitoring evidence, account information, or local absolute paths.
- Add a minimal regression test for new behavior.

## Commit style

Conventional Commits are recommended: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, and `chore:`.
