# Neo4j 语义图谱接入

## 1) 安装依赖

```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) 配置 `.env`

```env
SEMANTIC_BACKEND=hybrid
SEMANTIC_NEO4J_DEPTH=2
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

## 3) 初始化图谱

```bash
cd backend
source .venv/bin/activate
python scripts/seed_neo4j_from_lexicon.py
```

## 4) 自检

```bash
python scripts/check_neo4j.py
```

## 5) 接口验证

- `GET /api/semantic/expand?term=脚步声`
- `GET /api/semantic/search-sfx?term=军队冲锋脚步&top_n=5`

## 说明

- `local`: 仅本地词典（稳定，零外部依赖）
- `neo4j`: 仅图谱（需图谱数据完整）
- `hybrid`: 推荐，先本地词典再叠加图谱扩展
