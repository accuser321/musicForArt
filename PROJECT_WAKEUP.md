# MusicForArt 项目唤醒与当前状态

最后更新时间：2026-03-17

## 新线程唤醒指令
请先读取并严格按此文件恢复上下文：
`/Users/demo/Documents/New project/musicForArt/PROJECT_WAKEUP.md`

然后先执行：
1. 总结当前用户端、运营端、Neo4j 三条主链路；
2. 检查并启动 Neo4j、后端、前端；
3. 优先继续当前未收尾的核心 bug 与业务功能；
4. 不做无关重构。

## 项目路径与端口
- 项目根目录：`/Users/demo/Documents/New project/musicForArt`
- 后端：`/Users/demo/Documents/New project/musicForArt/backend`
- 前端：`/Users/demo/Documents/New project/musicForArt/frontend`
- 用户端：`http://127.0.0.1:5178`
- 后台：`http://127.0.0.1:5178/admin.html`
- 后端：`http://127.0.0.1:8090`
- Neo4j HTTP：`http://127.0.0.1:7474`
- Neo4j Bolt：`127.0.0.1:7687`

## 当前系统分层

### 用户端
- 手机号 + 验证码模拟登录
- 创建项目
- 人物动作提取
- 动作图谱推荐
- 动作补充单

### 运营端
- 手机号 + 验证码模拟登录
- 动作补充单运营
- 动作图谱 Neo4j
- 父节点浏览器
- 父节点详情：列表视图 / 图谱视图

### 图谱层
- 本地动作图谱 JSON
- Neo4j 同步
- Neo4j 边已显式拆分：
  - `HAS_DIRECT_SFX_CHILD`
  - `HAS_COMPOSITE_SFX_CHILD`

## 关键业务规则

### 命名规则
统一展示名：
- `{音效名}（{类型}-{赛道}）`

例子：
- `吐（组合-玄幻）`
- `手势摩擦声（直达-玄幻）`

### 音效分类
- `直达音效`：如 `摩擦声`、`挥手声`
- `组合音效`：如 `掐诀`、`吐`

### 同一素材可跨赛道复用
- 物理文件不要求重复存两份
- 业务关系允许同时挂到多个赛道
- 用户下载时仍按赛道展示不同 display name

### 状态逻辑
业务状态：
- `pending`：待合并
- `partial`：部分补齐
- `ready_to_notify`：已补齐待通知
- `merged`：全补齐视图

提醒状态：
- `未通知`
- `已通知`

注意：
- `notified` 不是业务状态终点
- 当前 UI 视图要求：
  - 已通知的数据不再出现在“已补齐待通知”视图中

## 当前已完成功能

### 用户端
- 动作图谱推荐支持：
  - 列表视图
  - 图谱视图
- “为什么推荐这些音效”解释区
- 命中素材表只保留表格展示
- `待补子级音效` 使用 `? / ✓` 状态展示
- 人物动作提取表格已去掉“判定原因”

### 运营端
- 动作补充单按父节点分组
- 逐词上传
- 顶部通知目标改为下拉选择（手机号 + 补充单 ID）
- Neo4j 父节点浏览器：
  - 筛选
  - 排序
  - 摘要卡
  - 今日优先补充/通知
  - 列表 / 图谱双视图
- 父节点详情支持：
  - 子级关系
  - 已补 / 待补
  - 补充单状态
  - 节点内通知

### Neo4j
- 本地 Neo4j 已安装并可运行
- ActionNode 已写入业务状态：
  - `supplement_item_count`
  - `covered_count`
  - `pending_count`
  - `completion_ratio`
  - `ready_to_notify_count`
  - `notified_count`

## 当前最关键的已修 bug

### 1. 已通知仍出现在 ready_to_notify
已修：
- `ready_to_notify` 视图只显示：
  - 业务状态为 `ready_to_notify`
  - 且 `notified_at` 为空
- 已通知只出现在：
  - `全部状态`
  - `已通知用户`

### 2. 用户端命中素材但 `待补组合音效` 仍是问号
已修：
- 用户端 `? / ✓` 改为读取全局覆盖结果
- 不再只看当前一次补充单

### 3. 第二次同文复测时历史已补素材失效
已修：
- 核心原因：之前只按当前 supplement item 看素材，未按父节点全局复用
- 现在改成：
  - 按 `赛道 + target_head` 生成父节点 key
  - 从历史 `ActionSupplementAsset` 全局聚合已入库素材
  - 用户端、运营端、Neo4j 三边统一读这套覆盖结果

例子：
- `玄幻::吐` 现在能全局读到历史 `吐.mp3`
- 第二次同文复测不应再把 `吐（组合）` 变回问号

### 4. 同音效词跨父节点复用
已修：
- 如果 `玄幻::飞身` 已补过 `快速移动声`
- 而 `玄幻::躲闪` 也挂了 `快速移动声`
- 那么 `躲闪` 现在也应直接命中这份素材

### 5. 无明确音效词的动作自动生成保底组合音效
已修：
- 对 `骑`、`挥`、`操纵` 这类前台已展示但图谱里暂无 `sfx_terms` 的动作
- 系统自动生成保底组合音效：
  - `骑（组合）`
  - `挥（组合）`
  - `操纵（组合）`
- 这样用户端有可下载/可匹配路径，运营端也有上传入口

### 6. 通知下拉框独立于主列表状态
已修：
- 顶部通知下拉框不再跟随 `pending / merged / 全部状态` 变化
- 只展示：
  - `ready_to_notify`
  - 且 `未通知`
  的单条补充单
- 同时保留表格勾选批量通知

## 当前需要继续重点盯的链路
如果用户继续反馈“第二次复测仍失效”，优先检查：
1. `action_sfx_graph.py`
   - `load_action_node_coverage`
   - `build_action_sfx_recommendation`
2. `main.py`
   - `ops_action_supplements`
   - `ops_action_supplements_merge`
   - `analyze_action_graph_draft_api`
3. `action_graph_neo4j.py`
   - `_business_stats_by_node_key`

## 关键文件
- 后端主入口：
  - `/Users/demo/Documents/New project/musicForArt/backend/app/main.py`
- 动作图谱推荐：
  - `/Users/demo/Documents/New project/musicForArt/backend/app/services/action_sfx_graph.py`
- Neo4j 图谱服务：
  - `/Users/demo/Documents/New project/musicForArt/backend/app/services/action_graph_neo4j.py`
- 用户端页面：
  - `/Users/demo/Documents/New project/musicForArt/frontend/index.html`
- 运营后台页面：
  - `/Users/demo/Documents/New project/musicForArt/frontend/admin.html`
- 动作图谱数据：
  - `/Users/demo/Documents/New project/musicForArt/backend/assets/sfx/action_graph.json`
- 数据库：
  - `/Users/demo/Documents/New project/musicForArt/backend/music_for_art.db`

## 本地文件存储
当前开发环境音效统一存储目录：
- `/Users/demo/Documents/New project/musicForArt/backend/assets/sfx/`

当前测试数据清理策略：
- 保留 `user_account`
- 清空业务表：
  - `projects`
  - `audio_analysis`
  - `text_analysis`
  - `narration_analysis`
  - `fusion_plan`
  - `action_supplement_task`
  - `action_supplement_asset`
  - `user_operation_log`
  - `auth_code`
  - `auth_session`
- 清理测试音效文件，仅保留：
  - `README.md`
  - `semantic_lexicon.json`
  - `action_graph.json`

上线建议：
- 音效文件：腾讯云 COS
- 业务元数据：MySQL
- 图谱关系：Neo4j
- 本地磁盘：仅临时缓存

## Neo4j 本地运行
Neo4j 目录：
- `/Users/demo/Documents/New project/musicForArt/.local/neo4j/neo4j-install/neo4j/2026.02.2/libexec`

JRE 目录：
- `/Users/demo/Documents/New project/musicForArt/.local/jdks/zulu21-jre/zulu-21.jre/Contents/Home`

启动：
```bash
cd "/Users/demo/Documents/New project/musicForArt/.local/neo4j/neo4j-install/neo4j/2026.02.2/libexec"
export JAVA_HOME="/Users/demo/Documents/New project/musicForArt/.local/jdks/zulu21-jre/zulu-21.jre/Contents/Home"
./bin/neo4j start
./bin/neo4j status
```

## 标准启动命令

### 启动后端
```bash
cd "/Users/demo/Documents/New project/musicForArt/backend"
source .venv/bin/activate
pkill -f "python -m app.main" || true
nohup python -m app.main >/tmp/musicforart-backend.log 2>&1 &
sleep 3
curl -sS http://127.0.0.1:8090/health
```

### 启动前端
```bash
cd "/Users/demo/Documents/New project/musicForArt/frontend"
pkill -f "python3 -m http.server 5178" || true
nohup python3 -m http.server 5178 --bind 127.0.0.1 >/tmp/musicforart-frontend.log 2>&1 &
sleep 2
curl -sS http://127.0.0.1:5178/ | head -n 5
curl -sS http://127.0.0.1:5178/admin.html | head -n 5
```

### 验证 Neo4j
```bash
curl -sS http://127.0.0.1:8090/api/action-graph/neo4j-status
```

期待：
- `neo4j_configured: true`
- `neo4j_connected: true`

## 排障命令
```bash
tail -n 120 /tmp/musicforart-backend.log
tail -n 120 /tmp/musicforart-frontend.log
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:5178 -sTCP:LISTEN
```

## 当前协作方式
- 用户通常会批量测试后统一反馈
- 回答时优先：
  1. 明确是否已修
  2. 说明根因
  3. 给最短部署命令
  4. 给复测步骤和期待值
