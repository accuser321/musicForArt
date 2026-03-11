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

说明：
- 需要先手机号登录（`Authorization: Bearer <token>`）
- 未授权用户与授权用户策略：
  - 授权用户：不限调用次数
  - 未授权用户：全系统核心功能每日最多3次（跨功能合并计数）

## 1.1 手机号认证

- `POST /auth/request-code` 发送验证码（MVP测试版直接返回code）
- `POST /auth/login` 手机号+验证码登录，返回 token
- `GET /auth/me` 获取当前登录用户信息
- `POST /auth/logout` 退出登录

请求头：
- `Authorization: Bearer <token>`
- `X-User-Phone: <手机号>`（可选，便于行为日志归属）

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
- GET /ops/lexicon-review
- POST /ops/lexicon-review
- POST /ops/lexicon-draft/apply
- GET /ops/user-events
- GET /ops/project/{project_id}/flow-bundle
- GET /ops/file?path=...

说明：
- 运营接口需要管理员账号（`is_admin=true`）

## 7.0 管理员用户授权接口

- `GET /admin/users` 查看用户列表
- `POST /admin/users` 新增/更新用户授权

`POST /admin/users` 请求示例：
```json
{
  "phone": "13800138000",
  "is_authorized": true,
  "is_admin": false,
  "daily_limit": 3
}
```

### 7.1 用户行为审计查询

`GET /ops/user-events`

查询参数：
- `phone`（可选）
- `project_id`（可选）
- `action`（可选）
- `date_from`（可选，ISO 时间）
- `date_to`（可选，ISO 时间）
- `limit`（可选，默认200，最大1000）

说明：
- 支持请求头 `X-User-Phone` 或请求参数 `user_phone` 写入行为日志手机号。

### 7.2 项目全流程打包查询

`GET /ops/project/{project_id}/flow-bundle`

返回：
- 项目基础信息
- 资产路径（音乐、演绎）
- 分析文本（音乐/文本/演绎/执行单）
- 该项目关联行为日志（含输入摘要、输出摘要、文件引用）

### 7.3 本地归档文件下载

`GET /ops/file?path=<绝对路径或UPLOAD_DIR下相对路径>`

说明：
- 仅允许下载 `UPLOAD_DIR` 目录下文件，接口内含路径安全校验。

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

### 9.3 词典审核状态持久化

- `GET /ops/lexicon-review`
  - 查看候选词审核记录（pending/approved/rejected）
- `POST /ops/lexicon-review`
  - 批量写入审核结论

请求示例：
```json
{
  "items": [
    {
      "candidate": "盔甲拖地摩擦",
      "target_head": "金属",
      "status": "rejected",
      "note": "manual_reject"
    }
  ]
}
```

说明：
- 已标记为 `approved` 或 `rejected` 的候选，后续草稿生成会自动跳过，避免重复出现。
