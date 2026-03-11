# 存储方案（当前本地 + 未来COS）

## 当前（已实现）
- 元数据与行为日志：MySQL/SQLite（SQLAlchemy）
- 大文件：本地文件系统（`UPLOAD_DIR`）

### 本地归档目录
按以下维度落盘：
- 用户（手机号）
- 年/月/日
- 操作功能

路径示例：
- `UPLOAD_DIR/13800138000/2026/03/11/audio_analysis/project_23.mp3`
- `UPLOAD_DIR/13800138000/2026/03/11/text_narration_analysis/project_23.mp3`

## 用户行为审计
新增表：`user_operation_log`
- 字段：`project_id`、`user_phone`、`action`、`input_json`、`output_json`、`file_refs_json`、`created_at`
- 记录动作：创建项目、音乐分析、文本分析、演绎分析、文本+演绎、执行单

## 运营接口
- `GET /api/ops/user-events`
  - 支持：`phone`、`project_id`、`action`、`date_from`、`date_to`、`limit`
- `GET /api/ops/project/{project_id}/flow-bundle`
  - 返回项目全流程资产路径、分析结果、行为日志
- `GET /api/ops/file?path=...`
  - 下载 `UPLOAD_DIR` 下归档文件（带路径安全校验）

## 未来切换到 COS（建议）
1. 抽象统一存储接口：
   - `StorageBackend.save_bytes(...)`
   - `StorageBackend.open(...)`
   - `StorageBackend.build_download_url(...)`
2. 本地实现：`LocalStorageBackend`
3. COS实现：`CosStorageBackend`
4. 环境变量切换：
   - `STORAGE_BACKEND=local|cos`
   - `COS_BUCKET` / `COS_REGION` / `COS_SECRET_ID` / `COS_SECRET_KEY`
5. 迁移策略：
   - 先双写（local + cos）
   - 稳定后切只写cos
   - 历史文件后台异步搬迁
