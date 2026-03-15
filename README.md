# AcompaLLM

AcompaLLM 是一个本地优先的桌面 AI 对话客户端（pywebview），支持多模型接入、工具调用、知识库检索、对话记忆和流式思考展示。

## 主要特性

### 对话与模型

- 多提供商：DeepSeek / OpenAI / Gemini / 通义千问 / Ollama / 自定义 OpenAI 兼容接口
- 流式输出：支持正文与 `reasoning_content` 分离渲染
- 对话管理：多会话、自动命名、删除/重生成、版本切换
- 模型列表缓存：普通模型与嵌入模型均支持按 provider 缓存

### 工具与检索

- Agentic 工具链：`memory_search` / `kb_search` / `web_search`
- 工具调用可视化：每个工具块独立显示执行状态
- 工具后反思：每个工具块单独反思，不与深度思考混用
- 检索查询关键词化：工具查询优先使用关键词组而非整句

### 记忆与稳定性

- 记忆库：按轮次保存 `[用户]` + `[AI]` 合并块
- 删除联动：删除会话消息时可同步删除关联记忆向量块
- 错误隔离：上游明显错误回复不写入记忆，避免污染检索
- 清理工具：提供坏记忆清理脚本 `scripts/cleanup_memory_errors.py`

## 项目结构

```text
desktop_app.py            # 桌面入口（pywebview + 本地静态服务 + JS Bridge）
APIAgent.py               # 兼容入口（转发到 app/APIAgent.py）
app/
	APIAgent.py             # FastAPI 网关（OpenAI 兼容）
	client_core.py          # 核心对话编排与流式逻辑
	core_logic.py           # 公共策略与触发器逻辑
	tools_adapter.py        # 工具调用适配
	rag_adapter.py          # RAG 适配
client/                   # 前端静态资源（HTML/CSS/JS）
kb/                       # 知识库模块
memory/                   # 记忆库模块
data/                     # 本地持久化数据（chats/chroma/memory 等）
scripts/
	cleanup_memory_errors.py# 记忆污染块清理脚本
utils/
	imports.py              # 安全导入工具
```

## 运行方式

### 桌面应用

```bash
python desktop_app.py
```

### 网关（可选）

```bash
uvicorn app.APIAgent:app --reload --port 8000
```

## 常用维护命令

### 清理记忆库中的错误污染块

```bash
python scripts/cleanup_memory_errors.py
```

## 说明

- 当前仓库采用 `app/` 作为核心后端代码目录。
- 根目录 `APIAgent.py` 为兼容入口，避免旧启动命令失效。
