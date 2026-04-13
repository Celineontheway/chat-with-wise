---
name: chat-with-wise
description: |
  与高人聊 — 多 AI 高人在飞书群聊中实时辩论/评审方案。
  根据议题自动从专家 skills 中选角，分配到飞书 bot，驱动多角色实时群聊。
  支持人类随时插话，bot 之间互相追问反驳。借鉴 Karpathy llm-council 的互评排名机制。
triggers:
  - 与高人聊
  - chat with wise
  - 飞书议事厅
  - feishu council
  - 开个评审会
  - 让AI讨论一下
  - council
  - 议事厅
  - 讨论一下
argument-hint: "议题内容（直接写在触发词后面）"
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
  - Grep
  - Glob
---

# 与高人聊 Skill

多 AI 高人在飞书群聊中实时辩论。根据议题自动选角，每个高人用独立飞书 bot 身份发言，互相追问反驳。

## 触发方式

```
与高人聊 如何切入AI玩具赛道
议事厅 要不要用微服务架构
让AI讨论一下 出海东南亚的策略
```

## 三阶段流程（借鉴 Karpathy llm-council）

1. **Stage 1：实时群聊** — 自动选角 → 开场发言 → bot 之间互相追问反驳
2. **Stage 2：互评排名** — 每位专家匿名评价其他人的表现，按贡献排名
3. **Stage 3：Chairman 综合** — 主席综合讨论 + 互评，输出共识/分歧/最佳洞察/行动建议

## 执行流程

```bash
# 实时模式（默认推荐）
python3 scripts/council.py start --topic "你的议题" --mode realtime

# 编排模式（固定轮次）
python3 scripts/council.py start --topic "你的议题"

# 手动指定专家
python3 scripts/council.py start --topic "..." --mode realtime --experts knudstorp,sofman,resnick
```

## 安装

### 依赖

```bash
pip3 install requests
```

### 配置

1. 复制示例配置：
```bash
cp config/council.example.json ~/.openclaw/feishu-council.json
```

2. 编辑 `~/.openclaw/feishu-council.json`，填入：
   - `group_chat_id`: 飞书群聊 ID（`oc_xxx`）
   - `bots`: 每个飞书 bot 的 `app_id` / `app_secret`
   - 最后一个 bot 可以留空 `feishu: {}`，会自动使用 `~/.openclaw/openclaw.json` 中的默认凭证

3. 设置 LLM API：
```bash
export ANTHROPIC_API_KEY=sk-ant-xxx
# 或使用本地代理
export ANTHROPIC_AUTH_TOKEN=your_token
export ANTHROPIC_BASE_URL=http://localhost:8080/
export ANTHROPIC_MODEL=claude-opus-4-6
```

### 专家 Skills

议事厅会自动扫描 `~/.claude/skills/*-perspective/SKILL.md` 目录下的专家 skills。每个专家 skill 包含完整的思维框架和表达 DNA，用于驱动角色扮演。

## 特性

- **自动选角**：根据议题用 LLM 从所有专家中选出最合适的组合，一个 bot 可融合多位专家视角
- **多 Bot 身份**：每个专家用独立飞书 bot 发言，有不同颜色和名字
- **实时互动**：bot 之间互相追问、反驳、补充，像真人群聊
- **人类插话**：群里发消息会被 bot 看到并回应
- **防刷机制**：15 秒冷却期 + [SKIP] 判断 + 80 条上限 + 300 秒静默超时
- **互评排名**：讨论结束后每位专家互评，借鉴 Karpathy llm-council
- **Chairman 总结**：综合讨论 + 互评，输出结构化总结

## 目录结构

```
feishu-council/
├── SKILL.md
├── README.md
├── config/
│   └── council.example.json
├── scripts/
│   ├── feishu_api.py       # 飞书 API 封装
│   ├── council.py          # 编排器 + 实时引擎
│   └── run.sh              # 一键启动
└── templates/
    ├── agent_card.json
    └── status_card.json
```
