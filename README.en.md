# Do GEO Search

[简体中文](README.md) | [English](README.en.md)

[![CI](https://github.com/aheadacheyxw/do-geo-search/actions/workflows/ci.yml/badge.svg)](https://github.com/aheadacheyxw/do-geo-search/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![GEO](https://img.shields.io/badge/GEO-evidence--first-315EFB.svg)](#core-principles)

Do GEO Search is a brand-neutral, industry-neutral, evidence-first GEO / AI search monitoring skill and local toolkit. It separates project governance, question sets, real Web UI evidence, brand and competitor signals, content opportunities, and period-over-period comparison. It does not turn unavailable evidence into zero or hide independent signals behind a composite score.

> This repository contains no real brand, account, login, historical run, or customer data. Everything under `templates/` is fictional.

## Features

- Complete first-time setup in four confirmation rounds, with at most one consolidated correction round when inputs conflict.
- Discover historical monitoring for the same brand or official domain and prompt the user to opt into a comparison report.
- Produce versioned, local, auditable Step 1–7 run packages.
- Preserve answer text, screenshots, controlled DOM, expanded source cards, and citation-candidate audits.
- Measure brand mentions, explicit recommendations, formal rank, visible citations, sentiment, and factual risk independently.
- Generate competitor facts, verified-source topology, per-question content opportunities, content outlines, a single-period report, and an adjacent-period comparison report.
- Respect authentication, CAPTCHAs, rate limits, and platform rules.

## Workflow

| Step | Purpose | Main outputs |
|---|---|---|
| 1 | Define the brand, scope, boundaries, and success signals | `project_profile`, human-confirmation receipt |
| 2 | Freeze a versioned monitoring question set | `question_catalog`, coverage and source manifests |
| 3 | Collect replayable evidence from real AI Web UIs | Raw answers, screenshots, DOM, source cards, manifest |
| 4 | Normalize observations and decide each signal separately | Normalized observations, signal decisions, exclusions |
| 5 | Analyze target-brand and registered-competitor gaps | Competitor facts, source topology, opportunities, review queue |
| 6 | Create an original outline for every valid opportunity | Title, H2 outline, channels, action recommendation |
| 7 | Compare with the nearest compatible historical snapshot | Comparison data, detail rows, and HTML report |

See the [Step 1–7 workflow](references/workflow.md) and [data contracts](references/contracts.md) for the complete boundaries.

## Quick start

### Requirements

- Python 3.11+
- Node.js 18+ for the browser evidence collector
- User-authorized, signed-in AI platform Web sessions

### Install

```bash
git clone https://github.com/aheadacheyxw/do-geo-search.git
cd do-geo-search
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

### Initialize a project

Copy and edit the fictional example first:

```bash
cp templates/project_answers.example.json answers.json
geo-monitor init --answers answers.json --output projects/my-brand
geo-monitor validate --project projects/my-brand
```

Follow the [four-stage onboarding guide](references/onboarding.md). Never use the fictional example brand as a real monitoring profile.

### Discover history and offer comparison mode

```bash
geo-monitor discover-history \
  --profile projects/my-brand/step1/project_profile.json \
  --search-root runs \
  --output projects/my-brand/history-discovery.json
```

If `prompt_user_for_comparison=true`, the operator must ask the user whether to create a comparison report during final confirmation. Product-name overlap alone is not proof of the same brand. See [comparison mode](references/comparison-mode.md).

### Prepare a run

```bash
geo-monitor prepare-run \
  --project projects/my-brand \
  --run-id my-brand-2026-09-01 \
  --output runs
```

See [collection and recovery](references/collection.md) before starting real Web UI collection.

### Process and report

```bash
geo-monitor step4 --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json
geo-monitor step5 --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json
geo-monitor capture-sources --run-dir runs/<run-id>
geo-monitor step6 --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json --step2-manifest projects/my-brand/step2/question_set_manifest.json
geo-monitor report --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json
```

Compare adjacent compatible runs:

```bash
geo-monitor compare \
  --profile projects/my-brand/step1/project_profile.json \
  --previous-run runs/<previous-run-id> \
  --current-run runs/<current-run-id> \
  --output runs/<current-run-id>/comparison
```

## Core principles

1. **Evidence first:** every derived signal must link back to an answer span, visible URL, source card, or raw artifact.
2. **Independent metrics:** a mention is not a recommendation, a recommendation is not Top 1, and a citation is not positive sentiment.
3. **Unavailable is not zero:** partial evidence, unavailable sources, refusals, and non-comparable context belong in limitations or exclusions.
4. **Explicit competitor boundary:** only human-registered brands enter competitor metrics; new brands go to a review queue first.
5. **No causal claims from comparisons:** period reports describe what changed under matched conditions, not why it changed.

## Repository structure

```text
.
├── .github/                  # CI, issue forms, and PR template
├── agents/                   # Codex Skill display metadata
├── docs/                     # Architecture and maintenance docs
├── geo_monitoring/           # Step 1–7 Python package
├── references/               # Skill-loaded rules and data contracts
├── scripts/                  # CLI entry point and browser collector
├── templates/                # Brand-neutral fictional inputs
├── tests/                    # Unit and regression tests
├── SKILL.md                  # Codex Skill entry point
└── pyproject.toml            # Package and CLI configuration
```

See the [architecture overview](docs/architecture.md) for data flow and module ownership.

## Development and testing

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q geo_monitoring tests scripts
node --check scripts/browser_evidence_collector.mjs
```

Read the [contributing guide](CONTRIBUTING.en.md) and [security policy](SECURITY.en.md) before submitting changes.

## Codex Skill

The repository root follows the Codex Skill structure. Once placed in a discoverable skills directory, invoke it as `$geo-ai-search-monitoring`. The skill follows [SKILL.md](SKILL.md), loads the relevant references on demand, and calls the local tools in this repository.

## Current limitations

- The browser collector includes example adapters for DeepSeek, Doubao, Qianwen, Kimi, and Tencent Yuanbao. Platform UI changes require selector maintenance.
- Authentication, CAPTCHAs, and rate limits must be handled through the normal user or platform flow.
- Content outlines are inputs for human planning, not verified brand facts or publish-ready copy.
- Period comparison requires compatible projects, question sets, platforms, and measurement context.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
