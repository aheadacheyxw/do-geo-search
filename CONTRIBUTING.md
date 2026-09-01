# 贡献指南

[简体中文](CONTRIBUTING.md) | [English](CONTRIBUTING.en.md)

## 开发流程

1. 从 `main` 创建短生命周期分支。
2. 保持 Step 1–7 的职责边界，不在上游步骤提前生成下游结论。
3. 修改数据契约时同步更新 `references/contracts.md` 和相关测试。
4. 平台适配器变更必须记录 adapter 版本，并保留旧证据的可回放能力。
5. 提交前运行完整测试与隐私扫描。

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q geo_monitoring tests scripts
node --check scripts/browser_evidence_collector.mjs
```

## Pull Request 要求

- 说明问题、变更范围和验证方式。
- 标注是否影响数据契约、兼容性、平台 selector 或报告口径。
- 不提交真实客户数据、运行证据、账号信息或本地绝对路径。
- 新行为应附最小回归测试。

## Commit 风格

推荐使用 Conventional Commits：`feat:`、`fix:`、`docs:`、`test:`、`refactor:`、`chore:`。
