# 中文语义匹配说明（当前实现）

## 目标

在你只提供“关键词级音效命名”的前提下，提高自动命中率。

## 已实现

1. 中文轻量分词（无需额外依赖）
- 连续中文块 + 双字切分 + 去重
- 用于文本分析和音效匹配

2. 语义扩展后端（可插拔）
- `SEMANTIC_BACKEND=local`：本地词典
- `SEMANTIC_BACKEND=neo4j`：仅 Neo4j 图谱
- `SEMANTIC_BACKEND=hybrid`：本地词典 + Neo4j

3. Top-N 匹配
- 对每个推荐音效，返回候选列表并选最高分
- 评分依据：精确匹配 + 词项重叠 + 字符2-gram余弦相似

4. 词典热更新 API（立即生效）
- `GET /api/semantic/lexicon`
- `PUT /api/semantic/lexicon`（整体替换）
- `PATCH /api/semantic/lexicon`（增量合并）

5. 调试接口
- `GET /api/semantic/expand?term=脚步声`
- `GET /api/semantic/search-sfx?term=军队冲锋脚步&top_n=5`

## 词典格式

```json
{
  "脚步声": ["脚步", "足音", "跑步声"],
  "坍塌": ["崩塌", "塌陷"]
}
```

## Neo4j 图谱结构建议

节点：`(:Concept {name})`
关系：`[:SYNONYM]`, `[:RELATED_TO]`, `[:IS_A]`
