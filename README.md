# Music For Art (Audiobook AI Studio)

一个完全隔离于其他项目的有声书创作辅助系统原型。  
功能聚焦：上传音乐后输出结构化分析；上传文本后输出动作/音效建议；最终生成可执行的后期 cue sheet。

## 目录结构

- `backend/`: Flask + SQLAlchemy 后端
- `frontend/`: 验证用静态页面
- `docs/`: 产品与接口文档
- `scripts/`: 启动脚本

## DeepSeek（推荐）配置

编辑 `backend/.env`：

```env
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=你的DeepSeekKey
LLM_MODEL=deepseek-chat
REPORT_MODE_DEFAULT=production
```

验证：
- 请求 `GET /health`
- 返回 `llm_enabled=true` 即可

## 模式选择

通过 `report_mode` 控制输出风格：
- `teaching`: 教学版（你给的九大模块思路）
- `production`: 有声书制作版（默认）
- `concise`: 简版

系统具备自动降级：`teaching -> production -> concise -> rules-only`。

## 中文语义匹配

支持 local/neo4j/hybrid 三种语义后端，Neo4j接入见 docs/NEO4J_SETUP.md

新增知识图谱推理接口：docs/GRAPH_REASONING.md

新增能力：推理缓存、质量日志、灰度发布开关（local/hybrid/neo4j）
新增能力：自动词典补全草稿（基于线上日志自动推荐词典扩展）
新增能力：草稿审核状态持久化（approved/rejected，自动去重）

已提供线上验证命令，见 docs/GRAPH_REASONING.md


新增接口：
- /api/semantic/expand
- /api/semantic/search-sfx
- /api/semantic/lexicon (GET/PUT/PATCH)


已支持中文分词+语义扩展+Top-N匹配，详见 docs/SEMANTIC_MATCH.md

自动词典补全草稿接口见 `docs/API.md`：  
- `GET /api/ops/lexicon-draft`  
- `POST /api/ops/lexicon-draft/apply`
- `GET /api/ops/lexicon-review`
- `POST /api/ops/lexicon-review`

## 导出能力

- `cue_csv`: 导出融合执行单 CSV
- `sfx_zip`: 导出音效 ZIP（按融合结果自动匹配 `backend/assets/sfx`）

把你后续提供的无版权音效放入：
- `backend/assets/sfx`

建议命名与推荐音效名称一致（例如 `群体脚步+战吼.mp3`）。

## MySQL 切换

`backend/.env` 中配置：

```env
DATABASE_URL=mysql+pymysql://user:password@host:3306/music_for_art?charset=utf8mb4
```

首次建表由后端启动时自动创建（MVP阶段）。
