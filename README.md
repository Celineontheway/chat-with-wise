# Chat with Wise 与高人聊

> 给一个议题，一群 AI 高人在飞书群里帮你辩明白。

Chat with Wise 让多位 AI 高人以独立身份在飞书群聊中实时辩论你的商业决策。给一个议题，它自动从专家库中选角，分配到飞书 Bot，高人们互相追问、反驳、补充——像 Pixar 的 BrainTrust 一样，给你最诚实的多视角反馈。

灵感来自 [Karpathy 的 llm-council](https://github.com/karpathy/llm-council)，在其三阶段流程基础上增加了实时群聊互动和飞书多 Bot 身份。

## 效果

```
[🏛️ 与高人聊 开始]
  议题：如何切入AI玩具赛道
  专家：Sofman×Vaswani(AI玩具硬件) · Resnick×Pirjanian(教育+AI) · 王宁(潮玩IP) · Lenny(产品增长)

[Sofman × Vaswani · AI玩具实战双视角]
  从 Anki 的经验来看，AI玩具的核心不是"加AI"，而是...

[Resnick × Pirjanian · 儿童创造力+AI陪伴]
  两位都在谈产品形态，但更根本的问题是：这个产品到底服务于孩子的什么需求？

[Resnick × Pirjanian · 儿童创造力+AI陪伴]
  两位都在谈产品形态，但更根本的问题是：这个产品到底服务于孩子的什么需求？

... (专家们互相追问反驳，人类随时插话) ...

[🏅 互评排名]
  每位专家评价其他人的表现，按贡献排名

[📋 与高人聊 总结]
  核心共识 / 关键分歧 / 最佳洞察 / 行动建议
```

## 三阶段流程

| 阶段 | 说明 |
|------|------|
| **Stage 1: 实时辩论** | 自动选角 → 开场观点 → 专家互相追问反驳，人类随时插话 |
| **Stage 2: 互评排名** | 每位专家匿名评价其他人，按贡献价值排名 |
| **Stage 3: 综合决策** | Chairman 综合讨论+互评，输出共识/分歧/最佳洞察/行动建议 |

## 快速开始

### 1. 安装

```bash
pip3 install requests
cp config/council.example.json ~/.openclaw/feishu-council.json
# 编辑配置，填入飞书 Bot 凭证和群聊 ID
```

### 2. 运行

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx

# 实时辩论模式（推荐）
python3 scripts/council.py start --topic "你的决策议题" --mode realtime

# 编排模式（固定轮次，更可控）
python3 scripts/council.py start --topic "你的决策议题"
```

### 3. 作为 Claude Code Skill

放到 `~/.claude/skills/chat-with-wise/`，然后直接说：

```
与高人聊 如何切入AI玩具赛道
让AI讨论一下 出海东南亚的策略
开个评审会 要不要用微服务架构
```

## 核心特性

**自动选角** — 根据议题从专家库中选出最合适的组合。一个 Bot 可以融合多位高人视角（如 Sofman×Vaswani = Anki 教训 + Miko 生存策略）。

**多 Bot 身份** — 每位高人用独立飞书 Bot 发言，有不同颜色和名字。在群里看起来就像多个真人在讨论。

**实时互动** — 高人之间互相追问、反驳、补充。不是轮流念稿，是真正的思维碰撞。

**人类参与** — 群里发消息会被高人看到并回应。发「停止」随时结束。

**互评排名** — 讨论结束后每位高人互评其他人的表现，借鉴 Karpathy llm-council 的匿名评审机制。

**防刷机制** — 15秒冷却 + [SKIP]判断 + 80条上限 + 300秒静默超时，确保对话有质量不失控。

## 配置

编辑 `~/.openclaw/feishu-council.json`：

```json
{
  "group_chat_id": "oc_xxx",
  "bots": [
    { "id": "bot-1", "name": "Architect", "avatar_color": "blue",
      "feishu": { "app_id": "cli_xxx", "app_secret": "xxx" } },
    { "id": "bot-2", "name": "Builder", "avatar_color": "green",
      "feishu": { "app_id": "cli_yyy", "app_secret": "xxx" } }
  ],
  "experts_dir": "~/.claude/skills",
  "rules": {
    "max_rounds": 3,
    "cooldown_sec": 3,
    "llm_model": "claude-opus-4-6",
    "llm_api_key_env": "ANTHROPIC_API_KEY"
  }
}
```

### 飞书 Bot 设置

每个 Bot 需要一个独立的[飞书应用](https://open.feishu.cn)，并加入目标群聊。最后一个 Bot 可以留空 `feishu: {}`，自动使用默认凭证。

### 专家库

Chat with Wise 自动扫描 `experts_dir` 下的 `*-perspective/SKILL.md`。每个高人 Skill 包含完整的思维框架、决策启发式和表达 DNA。用 [女娲 Skill 造人术](https://github.com/alchaincyf/nuwa-skill) 可以快速创建新高人。

## 依赖

- Python 3 + requests
- 飞书开放平台应用
- Anthropic API（或兼容的本地代理）

## 致谢

- [Karpathy/llm-council](https://github.com/karpathy/llm-council) — 三阶段互评排名机制
- [女娲 Skill 造人术](https://github.com/alchaincyf/nuwa-skill) — 专家 Skill 生成

## License

MIT
