# API 文档 (MVP+LLM)

Base URL: `http://127.0.0.1:8090/api`

## 0) 健康检查

`GET /health`

## 报告模式 report_mode

可选值：
- `teaching`：教学版（9大模块）
- `production`：制作版（导演/后期执行）
- `concise`：简版（快速阅读）

默认：`REPORT_MODE_DEFAULT`（建议 production）

## 1) 创建项目

`POST /projects`

## 2) 上传音频并分析

`POST /analysis/{project_id}/audio?report_mode=teaching`

`multipart/form-data` 字段：`file`

返回关键字段：
- `analysis_mode`: `llm+features` 或 `rules-only`
- `llm_structured`: 是否拿到结构化 JSON
- `report_mode`: 请求模式
- `effective_report_mode`: 实际生效模式
- `llm_fallback_applied`: 是否发生降级
- `llm_attempted_modes`: 尝试链路
- `report_json`: 结构化输出（若成功）

## 3) 文本分析

`POST /analysis/{project_id}/text`

请求示例：
```json
{"text":"城门震动，士兵冲锋，将军怒吼。","report_mode":"production"}
```

## 4) 生成融合计划

`POST /analysis/{project_id}/fusion?report_mode=production`

## 5) 获取完整报告

`GET /analysis/{project_id}/report`

## 6) 导出资源

## 7) 运营接口

- GET /ops/funnel?days=7
- GET /ops/recommendations?days=7
- GET /ops/lexicon-draft?days=7
- POST /ops/lexicon-draft/apply

## 8) 导出资源

### 6.1 导出 Cue Sheet CSV

`GET /analysis/{project_id}/export?type=cue_csv`

前置条件：已生成 fusion。

### 6.2 导出 SFX ZIP

语义匹配后端可选 local/neo4j/hybrid（见 docs/NEO4J_SETUP.md）。

匹配策略：中文分词 + 语义词典扩展 + 向量近似排序（Top-N）。

`GET /analysis/{project_id}/export?type=sfx_zip`

ZIP内容：
- `manifest.json`
- `README.txt`
- 已匹配到的音效文件（来自 `backend/assets/sfx`）

说明：音效文件按融合结果中的 `recommended_sfx` 名称匹配同名 `.mp3/.wav/.flac`。

## 9) 自动词典补全草稿

### 9.1 生成草稿

`GET /ops/lexicon-draft?days=7`

根据近 N 天日志自动汇总：
- 搜索零命中的中文词（search_sfx）
- 导出阶段缺失音效词（export_download.missing_sfx）

返回：
- `draft_lexicon`: 可直接合并到语义词典的草稿
- `draft_items`: 每个候选词的来源、频次、置信度、建议归并头词

### 9.2 应用草稿

`POST /ops/lexicon-draft/apply`

请求示例：
```json
{
  "draft_lexicon": {
    "脚步声": ["冲锋脚步", "盔甲脚步"]
  },
  "mode": "merge",
  "dry_run": false
}
```

参数说明：
- `mode`:
  - `merge`: 合并到现有词典（推荐）
  - `replace`: 用草稿整体替换词典
- `dry_run`:
  - `true`: 只预览，不写入
  - `false`: 实际写入 `backend/assets/sfx/semantic_lexicon.json`
- `draft_items`（可选）:
  - 可替代 `draft_lexicon` 直接提交候选列表
  - 支持字段：`candidate`、`target_head`、`confidence`、`selected`
- `min_confidence`（可选）:
  - 仅应用置信度不低于阈值的候选（0~1）
- `only_selected`（可选）:
  - `true` 时只应用 `selected=true` 的候选，适合人工审核后提交
