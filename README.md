# GEO AI Search Monitoring

一个品牌无关、行业无关的 GEO 监测技能与本地工具仓库。它把项目与问题集治理、真实 AI Web UI 证据、竞品差距、引用来源、内容机会和相邻周期对比分开处理。

## 能力

- 4 次确认内完成首次建项；
- 自动发现同品牌/官方域历史，并在执行前询问是否生成对比报告；
- Step 1–7 的版本化、本地、可审计运行包；
- 不把不可用证据写成零，不把第三方来源当竞品，不使用综合黑箱分；
- 不包含任何原项目品牌、域名、账号、运行结果或历史报告。

## 快速开始

```bash
python3 scripts/geo_project.py init --answers templates/project_answers.example.json --output demo-project
python3 scripts/geo_project.py validate --project demo-project
python3 scripts/geo_project.py discover-history --profile demo-project/step1/project_profile.json --search-root .
python3 scripts/geo_project.py prepare-run --project demo-project --run-id example-run-001 --output runs
python3 scripts/geo_project.py report --run-dir runs/example-run-001 --profile demo-project/step1/project_profile.json
python3 -m unittest discover -s tests -p 'test_*.py'
```

Codex 使用时直接调用 `$geo-ai-search-monitoring`。详细流程由 [SKILL.md](SKILL.md) 路由。
