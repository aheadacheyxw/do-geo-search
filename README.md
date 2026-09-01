# Do GEO Search

[![CI](https://github.com/aheadacheyxw/do-geo-search/actions/workflows/ci.yml/badge.svg)](https://github.com/aheadacheyxw/do-geo-search/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg)](https://www.python.org/)
[![GEO](https://img.shields.io/badge/GEO-evidence--first-315EFB.svg)](#核心原则)

一个品牌无关、行业无关、证据优先的 GEO / AI 搜索监测 Skill 与本地工具包。它将项目治理、问题集、真实 Web UI 证据、品牌与竞品信号、内容机会和周期对比分层处理，避免把不可评估数据写成零或用黑箱总分掩盖问题。

> 本仓库不包含任何真实品牌、账号、登录信息、历史运行包或客户数据。`templates/` 中的数据均为虚构示例。

## 功能概览

- 在 4 次确认内完成首次建项；信息冲突时最多增加 1 次集中修正。
- 自动发现相同品牌或官方域的历史监测，提醒是否生成相邻周期对比报告。
- 提供 Step 1–7 的版本化、本地、可审计运行包。
- 保存回答正文、截图、受控 DOM、展开来源卡和引用候选审计。
- 分别计算品牌提及、明确推荐、正式位次、可见引用、情感倾向和事实风险。
- 输出竞品事实、已验证来源拓扑、逐题内容机会、内容骨架、单期总报告和周期对比报告。
- 不绕过登录、验证码、限流或平台规则。

## 工作流

| Step | 目的 | 主要产物 |
|---|---|---|
| 1 | 定义品牌、范围、边界和成功信号 | `project_profile`、人工确认凭据 |
| 2 | 冻结版本化监测问题集 | `question_catalog`、覆盖与来源清单 |
| 3 | 从真实 AI Web UI 采集可回放证据 | 原始回答、截图、DOM、来源卡、manifest |
| 4 | 清洗观察并分别判定各类信号 | 规范化观察、信号判定、质量与排除表 |
| 5 | 分析目标品牌与已登记竞品差距 | 竞品事实、来源拓扑、内容机会、复核队列 |
| 6 | 为每个有效机会生成原创内容骨架 | 主标题、H2、发布平台、行动建议 |
| 7 | 与最近一期兼容历史进行严格对比 | 周期对比数据、明细和 HTML 报告 |

完整边界见 [Step 1–7 工作流](references/workflow.md)，数据字段见 [数据契约](references/contracts.md)。

## 快速开始

### 环境

- Python 3.11+
- Node.js 18+（仅浏览器采集脚本需要）
- 已登录且获得用户授权的 AI 平台 Web 页面

### 本地安装

```bash
git clone https://github.com/aheadacheyxw/do-geo-search.git
cd do-geo-search
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

### 创建首个项目

先复制并修改虚构模板：

```bash
cp templates/project_answers.example.json answers.json
geo-monitor init --answers answers.json --output projects/my-brand
geo-monitor validate --project projects/my-brand
```

首次建项请按 [四阶段引导](references/onboarding.md) 完成。不要把模板中的示例品牌直接用于正式监测。

### 发现历史并决定是否对比

```bash
geo-monitor discover-history \
  --profile projects/my-brand/step1/project_profile.json \
  --search-root runs \
  --output projects/my-brand/history-discovery.json
```

如果结果中的 `prompt_user_for_comparison=true`，执行者必须在最终确认中询问用户是否生成对比报告。产品名称相同不能单独证明是同一品牌，详细规则见 [对比模式](references/comparison-mode.md)。

### 创建运行包

```bash
geo-monitor prepare-run \
  --project projects/my-brand \
  --run-id my-brand-2026-09-01 \
  --output runs
```

真实 UI 采集及中断恢复见 [采集与恢复](references/collection.md)。

### 处理和报告

```bash
geo-monitor step4 --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json
geo-monitor step5 --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json
geo-monitor capture-sources --run-dir runs/<run-id>
geo-monitor step6 --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json --step2-manifest projects/my-brand/step2/question_set_manifest.json
geo-monitor report --run-dir runs/<run-id> --profile projects/my-brand/step1/project_profile.json
```

生成相邻周期对比：

```bash
geo-monitor compare \
  --profile projects/my-brand/step1/project_profile.json \
  --previous-run runs/<previous-run-id> \
  --current-run runs/<current-run-id> \
  --output runs/<current-run-id>/comparison
```

## 核心原则

1. **证据优先**：派生信号必须回指回答 span、可见 URL、来源卡或原始件。
2. **指标独立**：提及不等于推荐，推荐不等于 Top1，引用不等于正面评价。
3. **不可评估不填零**：`partial`、`unavailable`、拒答和上下文不可比必须进入限制或排除项。
4. **显式竞品边界**：只有人工登记的品牌进入竞品指标，新品牌先进入候选复核表。
5. **对比不做因果归因**：周期报告只描述相同条件下发生了什么变化。

## 项目结构

```text
.
├── .github/                  # CI、Issue 与 PR 模板
├── agents/                   # Codex Skill 展示元数据
├── docs/                     # 架构与维护文档
├── geo_monitoring/           # Step 1–7 核心 Python 包
├── references/               # Skill 按需加载的规则与数据契约
├── scripts/                  # CLI 入口与浏览器证据采集器
├── templates/                # 品牌无关的虚构输入模板
├── tests/                    # 单元与回归测试
├── SKILL.md                  # Codex Skill 主入口
└── pyproject.toml            # Python 包与命令行配置
```

系统分层与数据流见 [架构说明](docs/architecture.md)。

## 开发与测试

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q geo_monitoring tests scripts
node --check scripts/browser_evidence_collector.mjs
```

提交前请阅读 [贡献指南](CONTRIBUTING.md) 和 [安全策略](SECURITY.md)。

## Codex Skill

仓库根目录符合 Codex Skill 结构。将仓库放入可发现的 skills 目录后，可通过 `$geo-ai-search-monitoring` 调用。Skill 会按照 [SKILL.md](SKILL.md) 进行引导、按需读取参考文档，并调用仓库内脚本完成本地处理。

## 当前限制

- 浏览器采集器内置 DeepSeek、豆包、千问、Kimi 和腾讯元宝的示例适配器；平台 UI 改版后需要维护 selector。
- 浏览器登录、验证码和限流必须由用户或平台正常流程处理。
- 内容结构是人工内容规划输入，不是可直接发布的品牌事实或成稿。
- 周期对比要求项目、问题集、平台和测量条件兼容。

## 版本记录

参见 [CHANGELOG.md](CHANGELOG.md)。
