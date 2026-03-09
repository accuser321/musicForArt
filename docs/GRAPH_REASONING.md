# 知识图谱推理接口

## 1) 图谱状态

`GET /api/graph/status`

返回：
- `backend`
- `neo4j_configured`
- `neo4j_connected`
- `concept_nodes`
- `synonym_edges`

## 2) 写入图谱关系（Neo4j）

`POST /api/graph/edge`

请求示例：
```json
{
  "head": "脚步声",
  "relation": "SYNONYM",
  "tail": "足音",
  "bidirectional": true
}
```

## 3) 推理查询（含缓存）

`GET /api/graph/reason?term=脚步声&limit=12&max_hops=2&project_id=1001`

返回新增：
- `cache_hit`
- `effective_backend`

## 4) 推理质量指标

`GET /api/graph/metrics?days=7`

输出不同 `event_type/backend` 的调用计数，便于观察灰度效果。

## 5) 灰度开关

`.env`:
```env
SEMANTIC_ROLLOUT_MODE=fixed
SEMANTIC_ROLLOUT_PERCENT=20
SEMANTIC_HYBRID_PROJECT_IDS=1001,1002
```

模式：
- `fixed`: 固定使用 `SEMANTIC_BACKEND`
- `by_project`: 指定项目走 hybrid，其余 local
- `by_hash`: 按哈希百分比放量到 hybrid
- `force_neo4j`: 全量 neo4j
- `force_hybrid`: 全量 hybrid

## 6) 线上验证建议命令

```bash
# 1) 看后端状态
curl -sS http://127.0.0.1:8090/health

# 2) 推理第一次（预期 cache_hit=false）
curl -sS --get 'http://127.0.0.1:8090/api/graph/reason' \
  --data-urlencode 'term=脚步声' \
  --data-urlencode 'project_id=2001'

# 3) 推理第二次（预期 cache_hit=true）
curl -sS --get 'http://127.0.0.1:8090/api/graph/reason' \
  --data-urlencode 'term=脚步声' \
  --data-urlencode 'project_id=2001'

# 4) 看统计
curl -sS 'http://127.0.0.1:8090/api/graph/metrics?days=7'
```

## 7) 核心运营指标接口（成本与效果）

### 7.1 漏斗

`GET /api/ops/funnel?days=7`

输出：
- `graph_reason` -> `search_sfx` -> `fusion_build` -> `export_download`
- 每一步项目数与相对首步转化率

### 7.2 优化建议

`GET /api/ops/recommendations?days=7`

输出：
- `expand_lexicon_for_terms`: 低命中词（建议补词典）
- `add_sfx_assets_for_terms`: 频繁缺失音效词（建议补素材）

## 8) 发布后关注指标（建议）

1. `graph_reason` 的 `avg_duration_ms` 是否在可控范围。
2. `search_sfx` 的 `success_rate` 与 `match_count` 是否提升。
3. `export_download` 项目数是否持续增长（代表制作闭环在跑）。
