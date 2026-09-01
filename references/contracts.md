# 数据契约与质量门

## 项目身份

`project_id`、`profile_version`、`project_profile_sha256`、标准品牌名、别名、官方域、产品/服务、市场语言、平台、竞品目录版本和确认凭据必须可追溯。

## 问题身份

保存 `question_id`、`question_revision_id`、精确文本、主意图、产品/受众/市场、重要性、组合角色和证据引用。实际发送文本与可见问题文本不得覆盖冻结原题。

## 观察身份

保存 `run_id`、`observation_id`、问题/档案版本与 hash、平台、测量上下文、发送/完成时间、重试 lineage、原始件 hash、`comparable` 与原因。

## 独立信号

- `brand_mention`：仅回答正文中的目标品牌 span。
- `sentiment`：`positive | neutral | mixed | negative | unavailable`，附 span 与规则版本；不从排名或引用推导。
- `explicit_recommendation`：仅无品牌提示的服务商推荐题中进入候选集合；不等同 Top1。
- `formal_rank`：仅明确整体推荐列表/表格；局部列表另记 section rank。
- `citation_status`：`verified | partial | unavailable | rejected`；只有 verified 进入引用指标。
- `factual_risk`：断言先标待核验；没有已验证反证时不直接写“错误”。

## 对比兼容

项目、问题集、竞品目录、平台、采集模式、地区语言、会话类别、联网模式、推理模式和信号定义均兼容才进入同一分母。不同条件保留记录但标记 `not_comparable`。
