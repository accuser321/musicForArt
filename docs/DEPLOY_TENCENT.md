# 腾讯云部署说明（MVP+LLM）

## 1. 准备

- Linux 服务器（建议 2C4G）
- Python 3.10+
- MySQL 8.x

## 2. 配置数据库

创建库：
```sql
CREATE DATABASE music_for_art DEFAULT CHARACTER SET utf8mb4;
```

配置 `backend/.env`：
```env
DATABASE_URL=mysql+pymysql://<user>:<password>@<host>:3306/music_for_art?charset=utf8mb4
```

## 3. 配置 LLM（可选但强烈建议）

OpenAI:
```env
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=<your_key>
LLM_MODEL=gpt-4o-mini
```

DeepSeek:
```env
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=<your_key>
LLM_MODEL=deepseek-chat
```

## 4. 启动

```bash
cd musicForArt/scripts
./run_backend.sh
```

首次启动会自动建表。

## 5. 前端验证

```bash
cd musicForArt/scripts
./run_frontend.sh
```

浏览器访问：`http://<服务器IP>:5178`
