# AcompaLLM

个人 AI 对话桌面客户端，内置联网搜索与向量记忆库。

## 功能现状

**已实现**

- 多提供商支持：DeepSeek / OpenAI / Gemini / 通义千问 / Ollama / 自定义
- 流式对话，支持 `reasoning_content` 思考过程展示
- 对话管理：多会话、自动命名、独立系统提示/温度
- 联网搜索（DuckDuckGo，auto / 总是 / 禁用）
- 记忆库：手动向 FAISS 向量索引添加片段，每次对话自动召回
- 桌面原生窗口（pywebview + EdgeChromium，无边框自定义标题栏）
- 对话数据本地持久化（JSON，`data/chats/`）

**进行中 / 计划**

- 知识库：Agentic RAG 模式（检索决策由模型驱动，支持多轮追问与来源引用）
- 记忆库：自动化提取与管理流程（待设计）
- 模型上下文协议（MCP）工具扩展

## 架构

```
desktop_app.py      — 入口，pywebview 窗口 + 内嵌 HTTP 静态服务器 + Python↔JS 桥接
client_core.py      — 核心逻辑：配置、会话存储、上游 API 通信、工具编排
tools_adapter.py    — 外部工具注册表（web_search、calculator，可扩展）
rag_adapter.py      — 向量检索适配器（sentence-transformers + FAISS）
client_ui/          — 前端（纯 HTML/CSS/JS，无框架）
data/               — 本地数据（对话 JSON、FAISS 索引）
```

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填写 API Key
python desktop_app.py
```

## 依赖

- Python 3.10+
- pywebview ≥ 4.0（EdgeChromium 后端）
- httpx、sentence-transformers、faiss-cpu
- duckduckgo-search（联网搜索）