# MusicForArt 项目唤醒与当前状态

最后更新时间：2026-03-19

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
- 场景搭建（新核心方向，待独立开发）
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

### 双核心模块方向
当前系统后续不再只围绕“动作分析”扩展，而是明确转向两个并列核心模块：

1. 动作分析
2. 场景搭建

规则：

1. 场景搭建不是动作图谱推荐的附属区块
2. 场景搭建必须作为用户端独立模块存在
3. 两个模块后续可以在结果页做联动，但不混模块
4. 当动作分析与场景搭建都稳定后，后续再进入第三阶段：
   - 导演思维融合
   - 近景 / 中景 / 远景
   - 混响 / 延迟 / 空间调度等创作建议

注意：

当前阶段，场景搭建不承担导演思维分析，不替用户做创作决策，只针对文本内容做场景识别、场景构成、图谱命中、素材召回与下载支持。

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
- 已去掉“报告模式”入口，用户端不再区分该模式
- “为什么推荐这些音效”解释区
- 命中素材表只保留表格展示
- `待补子级音效` 使用 `? / ✓` 状态展示
- 人物动作提取表格已去掉“判定原因”

### 运营端
- 动作补充单按父节点分组
- 逐词上传
- 动作图谱维护：
  - 两级下拉选择
  - 通用层 / 赛道层维护
  - 通用层继承屏蔽 / 恢复继承
  - 继承屏蔽池 / 屏蔽后命中复核
  - 节点级清理
  - 通用层与赛道层双向迁移
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

## 新增：场景搭建模块的大框架

### 一、产品定位
场景搭建与动作分析同级，都是用户端核心功能，目标是解决有声书行业中的“声音世界搭建”痛点。

动作分析解决：

1. 人物做了什么
2. 需要哪些动作音效

场景搭建解决：

1. 这一段文本属于什么场景
2. 场景由哪些可听见的元素构成
3. 场景应该命中哪些场景音效
4. 当前有哪些素材可直接下载
5. 还缺哪些场景音效素材

### 二、明确边界
场景搭建当前只做：

1. 文本场景识别
2. 场景构成拆解
3. 场景图谱命中
4. 场景音效召回与下载
5. 场景补充缺口生成

场景搭建当前不做：

1. 导演思维分析
2. 镜头语言分析
3. 近景 / 中景 / 远景的创作决策
4. 混响、延迟、空间设计等创作者能力替代

这些属于后续“第三模块：导演思维融合”阶段再做。

### 三、系统主链路
场景搭建建议沿用当前动作分析的主架构：

1. 用户上传文本
2. LLM 产出场景结论
3. 系统基于结论命中 Neo4j / 场景图谱
4. 命中场景关联词
5. 命中场景音效素材
6. 输出可下载结果与缺口
7. 缺口进入后续运营补充链路

### 四、场景搭建模块的核心输出
用户端建议最少包含：

1. 场景主识别
2. 场景构成要素
3. 场景关联词
4. 场景音效来源（可下载）
5. 待补场景音效

### 五、最重要的设计原则：场景可复制性
场景搭建与动作分析不同，它天然更强调“可复用的大场景集合”。

原因：

1. 场景文本写法会变化
2. 但声音制作上会收敛到稳定的大场景类型
3. 所以系统后续必须逐步沉淀可复用场景库

例如：

1. 深夜山林
2. 雨夜街巷
3. 客栈大堂
4. 宫廷长廊
5. 战场边缘

这意味着场景搭建模块后续必须建设：

1. 通用场景集合
2. 赛道场景集合
3. 场景构成词集合
4. 场景音效大集合

目标：

1. 新文本优先命中已有大场景
2. 不要每次从零生成全新场景
3. 只有明显不属于现有集合时，才新增候选

### 六、Neo4j / 图谱层建议方向
场景搭建图谱后续建议包含：

1. 场景主节点
2. 场景构成词
3. 直达场景音效
4. 组合场景音效
5. 场景模板 / 场景集合层

场景模板 / 场景集合层是关键，它决定系统的复用性与稳定性。

### 七、提示词来源
场景搭建模块的 LLM 提示词，不由当前开发阶段拍脑袋编写。

后续以用户提供的“有声书老师教学逐字稿”为主输入，由系统从逐字稿中提炼：

1. 场景识别提示词
2. 场景构成拆解提示词
3. 场景图谱命中所需结构化字段

### 八、开发优先级
当前优先级是：

1. 先沉淀模块框架与数据结构
2. 再从老师逐字稿提炼提示词
3. 再开始场景搭建模块开发

不要直接跳过框架设计开始写代码。

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
- 场景搭建策略文档：
  - `/Users/demo/Documents/New project/musicForArt/docs/SCENE_BUILDING_MODULE_STRATEGY.md`
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
