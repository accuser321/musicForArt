# MusicForArt 项目唤醒与状态记忆

最后更新时间：2026-03-12

## 唤醒指令（新线程直接粘贴）
请读取并严格按此文件恢复上下文：`/Users/demo/Documents/New project/musicForArt/PROJECT_WAKEUP.md`。  
先执行以下动作：  
1) 总结当前架构与核心链路；  
2) 列出最近一次回滚点（tag/commit）；  
3) 检查并启动前后端服务（8090/5178）；  
4) 继续未完成的核心功能，不做无关重构。

## 当前核心架构
- 项目路径：`/Users/demo/Documents/New project/musicForArt`
- 后端：Flask（`backend`）
- 前端：静态页面（`frontend/index.html` + `python3 -m http.server`）
- API 前缀：`/api`
- 关键端口：
  - 后端：`127.0.0.1:8090`
  - 前端：`127.0.0.1:5178`

## LLM 提示词策略（已文件化，避免硬编码）
- 主提示词 + 重试提示词（按 evidence kind 链式调用）：
  - 音乐分析：
    - `backend/app/prompts/V3-music_analysis_task.txt`
    - `backend/app/prompts/V3-music_analysis_task_retry.txt`
  - 文本分析：
    - `backend/app/prompts/V3-text_analysis_task.txt`
    - `backend/app/prompts/V3-text_analysis_task_retry.txt`
  - 执行单：
    - `backend/app/prompts/V3-production_analysis_task.txt`
    - `backend/app/prompts/V3-production_analysis_task_retry.txt`
  - 导演汇总：
    - `backend/app/prompts/director_final_task.txt`
    - `backend/app/prompts/director_final_task_retry.txt`

## 当前关键行为约束
- 文本分析链路中，`tokens` 已从证据中移除（减少上下文膨胀）。
- `LLM 调试窗口（Prompt I/O）` 保留，用于审查调用输入输出。
- “Raw Response 用户观测区（仅核心字段）”在当前页面已隐藏（保留底层能力，不删除功能）。
- 音乐分析前端已做中文化可视化阅读（非纯 JSON）。

## 启动命令（推荐）
```bash
cd "/Users/demo/Documents/New project/musicForArt"
pkill -f "python -m app.main" || true
pkill -f "python3 -m http.server 5178" || true
sleep 1

cd "/Users/demo/Documents/New project/musicForArt/backend"
source .venv/bin/activate
nohup python -m app.main >/tmp/musicforart-backend.log 2>&1 &
sleep 2

cd "/Users/demo/Documents/New project/musicForArt/frontend"
nohup python3 -m http.server 5178 --bind 127.0.0.1 >/tmp/musicforart-frontend.log 2>&1 &
sleep 2

curl -sS http://127.0.0.1:8090/health
curl -sS http://127.0.0.1:5178/ | head -n 5
```

## 排障命令
```bash
tail -n 120 /tmp/musicforart-backend.log
tail -n 120 /tmp/musicforart-frontend.log
lsof -nP -iTCP:8090 -sTCP:LISTEN
lsof -nP -iTCP:5178 -sTCP:LISTEN
```

## 运营与数据方向（已明确）
- 需要保留用户行为数据：上传音频、文本、演绎音频、LLM I/O、执行单输入输出。
- 后续可迁移云存储（当前本地文件系统）。
- 后台管理（手机号授权/限额）作为后续迭代，不阻塞核心链路。
