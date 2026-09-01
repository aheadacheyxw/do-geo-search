---
name: geo-ai-search-monitoring
description: 为任意品牌建立或运行证据优先的 GEO/AI 搜索监测，覆盖项目与问题集治理、真实 Web UI 证据采集、品牌与竞品分析、内容机会、报告以及相邻周期对比。适用于首次建项、周度复测、竞品差距和引用来源分析；不用于普通 SEO 排名抓取或无证据的品牌建议。
---

# 通用 GEO AI 搜索监测

目标是生成可复跑、可审计的 Step 1–7 运行包。品牌提及、明确推荐、正式推荐位次、可见引用、情感倾向和事实风险必须分别记录，不得压成黑箱总分。

## 首次引导

严格按 [四阶段引导](references/onboarding.md) 进行。把相关选择合并提问，正常情况下最多完成 4 次用户确认；只有资料自相矛盾或缺关键授权时，才增加 1 次集中修正。

在冻结运行计划前必须执行历史发现：

```bash
python3 scripts/geo_project.py discover-history \
  --profile <project_profile.json> \
  --search-root <用户授权的工作区>
```

发现同品牌或同官方域的有效历史快照时，在第 4 次确认中一并询问是否生成对比报告。用户拒绝不阻塞本期监测；用户同意时只选择最近一期兼容快照，其他历史列为不可比较或备选。

## 执行边界

- Step 1 未批准或 Step 2 未冻结时，不开始真实采集。
- 真实平台采集只通过用户授权且已登录的 Web UI；不绕过登录、验证码、限流或平台限制。
- 每题新会话，不追问、不 regenerate。保存回答正文、初始截图与受控 DOM、展开可见来源后的截图与受控 DOM、来源卡和引用候选审计。
- 仅登记品牌及其官网域名进入竞品指标。AI 回答中新品牌进入候选竞品复核表，不自动升级为竞品。
- `partial`、`unavailable`、`rejected`、拒答或上下文不可比不能写成 0、未出现或低排名。
- 内容机会只生成原创标题、H2 骨架和人工行动建议；不复制来源页、不生成未经审核的品牌事实、不自动发布。
- 周期对比只比较上下文和信号定义都兼容的相邻快照，披露各自分子、分母和排除原因，不声称因果。

按任务读取对应参考：

- 建项与确认： [四阶段引导](references/onboarding.md)
- Step 1–7： [工作流](references/workflow.md)
- 数据与质量门： [数据契约](references/contracts.md)
- 历史与对比： [对比模式](references/comparison-mode.md)
- 浏览器采集： [采集与恢复](references/collection.md)

## 本地工具

```bash
# 创建通用项目骨架
python3 scripts/geo_project.py init --answers answers.json --output projects/<project-slug>

# 校验档案与问题集
python3 scripts/geo_project.py validate --project projects/<project-slug>

# 生成已冻结运行包
python3 scripts/geo_project.py prepare-run --project projects/<project-slug> --run-id <run-id> --output runs

# 发现历史快照
python3 scripts/geo_project.py discover-history --profile projects/<project-slug>/step1/project_profile.json --search-root <workspace>

# 生成相邻周期对比
python3 scripts/geo_project.py compare --profile <profile.json> --previous-run <run-a> --current-run <run-b> --output <comparison-dir>

# 生成单期总报告（含明确推荐率、平台表现与已验证来源）
python3 scripts/geo_project.py report --run-dir <run-dir> --profile <profile.json>
```

所有命令仅处理本地结构化文件；真实 AI 平台访问必须在用户单独授权后进行。
