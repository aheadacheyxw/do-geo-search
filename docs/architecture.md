# 架构说明

## 设计目标

Do GEO Search 将“采集到的事实”和“基于事实的业务判断”分开保存。任何规范化记录都可以从 append-only 原始证据重新生成，而不会覆盖原始件。

## 数据流

```text
人工确认的项目档案
        ↓
冻结问题集与版本清单
        ↓
真实 Web UI 观察与原始证据
        ↓
规范化观察 + 独立信号判定
        ↓
竞品事实 / 来源拓扑 / 内容机会
        ↓
内容骨架 + 单期报告
        ↓
相邻兼容快照周期对比
```

## 模块职责

- `project_questions.py`：校验项目档案与问题集，冻结问题版本。
- `evidence_package.py`：初始化运行包并追加原始观察。
- `collection_control.py`：记录采集状态、冷却与恢复事件。
- `step4.py`：生成可信观察数据层。
- `competitor.py`：构建已登记竞品差距、来源拓扑和机会候选。
- `source_capture.py`：抓取已验证来源页的受控快照。
- `content_briefs.py`：逐机会生成原创标题、H2 和行动建议。
- `final_report.py`：生成单期监测总报告。
- `history.py`：按品牌与官方域发现历史运行。
- `period_report.py`：生成严格可比的相邻周期报告。

## 数据安全边界

- `runs/`、`projects/`、`.venv/` 和构建产物默认不进入 Git。
- 历史发现只读取 profile、manifest 和 Step 4/5 结构化文件，不读取原始回答正文。
- 账号、Cookie、密码、验证码和身份信息不得写入运行包。
- 浏览器证据可以包含外部页面内容，提交前应单独进行隐私检查。
