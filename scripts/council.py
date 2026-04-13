#!/usr/bin/env python3
"""
council.py — 飞书议事厅编排器

根据议题自动从专家 skills 中选角，分配到飞书 bot，驱动多角色辩论。

Usage:
    python3 council.py start --topic "如何切入AI积木玩具"
    python3 council.py start --topic "API设计评审" --experts knudstorp,sofman,resnick
    python3 council.py history
"""

import argparse
import glob
import json
import os
import random
import re
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip3 install requests", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from feishu_api import FeishuClient, load_openclaw_feishu

CONFIG_PATH = Path(os.path.expanduser("~/.openclaw/feishu-council.json"))
SESSION_DIR = Path("/tmp/feishu-council")

NO_NEW_POINTS = ["本轮无新增意见", "本轮无安全意见", "无新增意见", "无安全意见",
                  "没有新的观点", "暂无补充", "同意以上观点，无补充"]


def load_config():
    if not CONFIG_PATH.exists():
        print(f"ERROR: Config not found: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def call_llm(system_prompt, user_prompt, rules):
    key_env = rules.get("llm_api_key_env", "ANTHROPIC_API_KEY")
    api_key = os.environ.get(key_env) or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        print(f"ERROR: {key_env} or ANTHROPIC_AUTH_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    base_url = (os.environ.get("ANTHROPIC_BASE_URL") or
                rules.get("llm_base_url", "https://api.anthropic.com")).rstrip("/")
    model = os.environ.get("ANTHROPIC_MODEL") or rules.get("llm_model", "claude-opus-4-6")

    resp = requests.post(
        f"{base_url}/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": 1024, "system": system_prompt,
              "messages": [{"role": "user", "content": user_prompt}]},
        timeout=90,
    )
    resp.raise_for_status()
    for block in resp.json().get("content", []):
        if block.get("type") == "text":
            return block["text"]
    return ""


# ── Expert discovery ──────────────────────────────────────────────

def discover_experts(experts_dir):
    """Scan *-perspective skills, return {id: {name, description, skill_path}}."""
    base = Path(os.path.expanduser(experts_dir))
    experts = {}
    for skill_md in sorted(base.glob("*-perspective/SKILL.md")):
        skill_id = skill_md.parent.name.replace("-perspective", "")
        text = skill_md.read_text(encoding="utf-8")
        # Extract name and one-line description from frontmatter
        name_match = re.search(r"^#\s+(.+?)·", text, re.M)
        name = name_match.group(1).strip() if name_match else skill_id
        desc_match = re.search(r"description:\s*\|?\s*\n\s*(.+)", text)
        desc = desc_match.group(1).strip() if desc_match else ""
        experts[skill_id] = {
            "name": name, "description": desc, "skill_path": str(skill_md)
        }
    return experts


def load_expert_prompt(skill_path):
    """Load the full SKILL.md content as the expert's system prompt."""
    text = Path(skill_path).read_text(encoding="utf-8")
    # Strip YAML frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3:].strip()
    # Add council-specific instructions
    text += (
        "\n\n## 议事厅规则\n"
        "你正在一个多专家飞书群聊议事中。请用「我」说话，200字以内。"
        "不要重复之前说过的观点。没有新观点就说'本轮无新增意见'。"
        "直接给出你的专业判断，不要做自我介绍。"
    )
    return text


def auto_cast(topic, experts, num_seats, rules):
    """Use LLM to pick the best experts for this topic."""
    expert_list = "\n".join(
        f"- {eid}: {e['name']} — {e['description'][:80]}"
        for eid, e in experts.items()
    )
    prompt = (
        f"议题：{topic}\n\n"
        f"可用专家（共{len(experts)}位）：\n{expert_list}\n\n"
        f"请选择最适合讨论这个议题的 {num_seats} 位专家。\n"
        f"一个席位可以融合1-2位专家的视角（如果他们的专长互补）。\n\n"
        f"严格输出JSON数组，每个元素：\n"
        f'{{"seat": 1, "expert_ids": ["id1"], "display_name": "显示名", "role": "一句话角色描述"}}\n\n'
        f"只输出JSON，不要其他文字。"
    )
    sys.stderr.write("[council] Auto-casting experts...\n")
    try:
        raw = call_llm("你是一位议事厅选角导演。根据议题选择最合适的专家组合。", prompt, rules)
        # Extract JSON from response
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
        seats = json.loads(raw)
        return seats
    except Exception as e:
        print(f"WARN: Auto-cast failed ({e}), using first {num_seats} experts", file=sys.stderr)
        fallback = list(experts.keys())[:num_seats]
        return [{"seat": i + 1, "expert_ids": [eid],
                 "display_name": experts[eid]["name"],
                 "role": experts[eid]["description"][:40]}
                for i, eid in enumerate(fallback)]


# ── Card builders ─────────────────────────────────────────────────

def build_agent_card(agent, round_num, response_text):
    return {
        "schema": "2.0", "config": {"wide_screen_mode": True},
        "header": {
            "template": agent.get("avatar_color", "blue"),
            "title": {"tag": "plain_text",
                      "content": f"{agent['display_name']} · {agent['role']}"},
            "subtitle": {"tag": "plain_text", "content": f"第 {round_num} 轮"},
        },
        "body": {"elements": [{"tag": "markdown", "content": response_text}]},
    }


def build_status_card(color, title, subtitle, content):
    return {
        "schema": "2.0", "config": {"wide_screen_mode": True},
        "header": {
            "template": color,
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": subtitle},
        },
        "body": {"elements": [{"tag": "markdown", "content": content}]},
    }


def is_no_new_points(text):
    t = text.strip().lower()
    return any(m in t for m in NO_NEW_POINTS) or (len(t) < 15 and ("无" in t or "没有" in t))


# ── Session ───────────────────────────────────────────────────────

class CouncilSession:
    def __init__(self, config, topic, expert_ids=None):
        self.config = config
        self.topic = topic
        self.rules = config.get("rules", {})
        self.chat_id = config["group_chat_id"]
        self.transcript = []
        self.human_messages = []
        self.round = 0
        self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_start_ts = None
        self.no_new_points_streak = 0

        # Discover experts
        experts_dir = config.get("experts_dir", "~/.claude/skills")
        all_experts = discover_experts(experts_dir)
        print(f"[council] Found {len(all_experts)} expert perspectives", file=sys.stderr)

        # Bots available
        bots = config.get("bots", [])
        num_seats = len(bots)

        # Cast experts to seats
        if expert_ids:
            # Manual selection
            ids = expert_ids.split(",")
            seats = [{"seat": i + 1, "expert_ids": [eid.strip()],
                      "display_name": all_experts.get(eid.strip(), {}).get("name", eid),
                      "role": all_experts.get(eid.strip(), {}).get("description", "")[:40]}
                     for i, eid in enumerate(ids[:num_seats])]
        else:
            # Auto-cast based on topic
            seats = auto_cast(topic, all_experts, num_seats, self.rules)

        # Build agents: merge bot identity + expert knowledge
        self.agents = []
        self.clients = {}
        default_id, default_secret = load_openclaw_feishu()

        for i, seat in enumerate(seats[:num_seats]):
            bot = bots[i]
            eids = seat.get("expert_ids", [])

            # Build system prompt from expert SKILL.md(s)
            prompts = []
            for eid in eids:
                if eid in all_experts:
                    prompts.append(load_expert_prompt(all_experts[eid]["skill_path"]))
            if not prompts:
                prompts.append(f"你是{seat.get('display_name', '专家')}，{seat.get('role', '')}。200字以内回复。")

            system_prompt = "\n\n---\n\n".join(prompts)

            agent = {
                "id": bot["id"],
                "display_name": seat.get("display_name", bot["name"]),
                "role": seat.get("role", ""),
                "avatar_color": bot.get("avatar_color", "blue"),
                "expert_ids": eids,
                "system_prompt": system_prompt,
            }
            self.agents.append(agent)

            # Feishu client
            feishu = bot.get("feishu", {})
            app_id = feishu.get("app_id") or default_id
            app_secret = feishu.get("app_secret") or default_secret
            self.clients[bot["id"]] = FeishuClient(app_id, app_secret)

        if not self.agents:
            print("ERROR: No agents configured", file=sys.stderr)
            sys.exit(1)

    def _primary_client(self):
        return self.clients[self.agents[0]["id"]]

    def _send_start_card(self):
        names = "、".join(f"{a['display_name']}({','.join(a['expert_ids'])})" for a in self.agents)
        max_r = self.rules.get("max_rounds", 5)
        content = (
            f"**议题：**{self.topic}\n\n"
            f"**参与者：**{names}\n\n"
            f"**规则：**最多 {max_r} 轮\n\n"
            f"💡 群内发消息可随时插话，发「停止」结束讨论"
        )
        card = build_status_card("indigo", "🏛️ 议事开始", self.session_id, content)
        self._primary_client().send_card(self.chat_id, card)

    def _build_context(self, agent):
        parts = [f"## 议题\n{self.topic}\n"]
        if self.transcript:
            parts.append("## 之前的发言\n")
            for e in self.transcript:
                parts.append(f"**{e['display_name']}（{e['role']}）** [第{e['round']}轮]:\n{e['content']}\n")
        if self.human_messages:
            parts.append("## 人类参与者的补充\n")
            for m in self.human_messages:
                parts.append(f"**人类**: {m}\n")
        parts.append(
            f"\n你现在是 {agent['display_name']}（{agent['role']}），第 {self.round} 轮。\n"
            "基于你的角色视角发表意见。有不同意见直接指出。没有新观点就说'本轮无新增意见'。"
        )
        return "\n".join(parts)

    def _poll_human_messages(self):
        if not self.session_start_ts:
            return []
        msgs = self._primary_client().get_messages(self.chat_id, start_time=self.session_start_ts)
        new = []
        for m in msgs:
            if m.get("sender_type") == "app" or m.get("msg_type") != "text":
                continue
            text = m.get("content", {}).get("text", "").strip()
            if text and text not in self.human_messages:
                new.append(text)
        return new

    def _agent_turn(self, agent):
        context = self._build_context(agent)
        sys.stderr.write(f"  [{agent['display_name']}] thinking...")
        sys.stderr.flush()
        try:
            response = call_llm(agent["system_prompt"], context, self.rules)
        except Exception as e:
            response = f"（调用失败：{str(e)[:100]}）"
            print(f"\n  ERROR: {e}", file=sys.stderr)
        sys.stderr.write(f" done ({len(response)} chars)\n")

        card = build_agent_card(agent, self.round, response)
        self.clients[agent["id"]].send_card(self.chat_id, card)

        entry = {"round": self.round, "agent_id": agent["id"],
                 "display_name": agent["display_name"], "role": agent["role"],
                 "content": response, "timestamp": time.time()}
        self.transcript.append(entry)
        return entry

    def _check_convergence(self, responses):
        if all(is_no_new_points(r["content"]) for r in responses):
            self.no_new_points_streak += 1
        else:
            self.no_new_points_streak = 0
        return self.no_new_points_streak >= self.rules.get("max_consecutive_no_new_points", 2)

    def _generate_summary(self):
        text = "\n\n".join(
            f"**{e['display_name']}（{e['role']}）** [第{e['round']}轮]:\n{e['content']}"
            for e in self.transcript)
        prompt = (f"## 议题\n{self.topic}\n\n## 讨论记录\n{text}\n\n"
                  "总结：1.共识 2.分歧 3.建议下一步。300字以内。")
        try:
            return call_llm("你是会议记录员，擅长提炼要点。", prompt, self.rules)
        except Exception as e:
            return f"（总结失败：{e}）"

    def run(self):
        max_rounds = self.rules.get("max_rounds", 5)
        cooldown = self.rules.get("cooldown_sec", 3)

        print(f"[council] Session {self.session_id}", file=sys.stderr)
        print(f"[council] Topic: {self.topic}", file=sys.stderr)
        for a in self.agents:
            print(f"  {a['display_name']} ({a['role']}) ← {','.join(a['expert_ids'])}", file=sys.stderr)

        self._send_start_card()
        self.session_start_ts = str(int(time.time()))
        time.sleep(2)

        stop_reason = ""
        while self.round < max_rounds:
            self.round += 1
            print(f"\n[council] === Round {self.round} ===", file=sys.stderr)

            new_human = self._poll_human_messages()
            if new_human:
                if any(m.lower().strip() in ("停止", "stop", "结束") for m in new_human):
                    stop_reason = "人类叫停"
                    break
                self.human_messages.extend(new_human)

            responses = []
            for agent in self.agents:
                responses.append(self._agent_turn(agent))
                time.sleep(cooldown)

            if self._check_convergence(responses):
                stop_reason = "观点收敛"
                break
        else:
            stop_reason = "达到最大轮数"

        print(f"\n[council] Ended: {stop_reason}", file=sys.stderr)
        summary = self._generate_summary() if self.rules.get("summary_at_end") else "讨论结束"
        card = build_status_card("purple", "📋 议事总结",
                                 f"{self.round} 轮 · {len(self.agents)} 位 · {stop_reason}", summary)
        self._primary_client().send_card(self.chat_id, card)

        # Save transcript
        d = SESSION_DIR / self.session_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps({
            "session_id": self.session_id, "topic": self.topic,
            "agents": [{"id": a["id"], "display_name": a["display_name"],
                        "role": a["role"], "expert_ids": a["expert_ids"]} for a in self.agents],
            "transcript": self.transcript, "human_messages": self.human_messages,
            "rounds": self.round,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[council] Saved: {d / 'transcript.json'}", file=sys.stderr)


# ── Realtime Mode ─────────────────────────────────────────────────

REALTIME_SUFFIX = (
    "\n\n## 实时群聊规则\n"
    "你正在一个多专家飞书群聊中，会看到其他专家和人类的发言。\n"
    "- 只在你有独特视角、不同意见或重要追问时才回应\n"
    "- 如果这条消息跟你的专业无关或你没有新观点，只回复 [SKIP]\n"
    "- 回应要有深度：指出具体哪位专家的哪个观点你同意/反对，并说明为什么\n"
    "- 鼓励追问：\"Knudstorp说的约束促进创造力，但在AI积木场景下约束边界在哪？\"\n"
    "- 鼓励反驳：直接指出其他专家观点的盲区或矛盾\n"
    "- 回复 200 字以内，不要做自我介绍\n"
)


class RealtimeCouncil:
    """实时多 agent 群聊：轮询 + 多线程异步响应。"""

    def __init__(self, config, topic, expert_ids=None):
        # 复用 CouncilSession 的初始化逻辑
        self._session = CouncilSession(config, topic, expert_ids)
        self.agents = self._session.agents
        self.clients = self._session.clients
        self.chat_id = self._session.chat_id
        self.topic = topic
        self.rules = self._session.rules
        self.session_id = self._session.session_id

        # 实时模式状态
        self.message_log = []          # 所有消息记录
        self.seen_msg_ids = set()      # 已处理的飞书消息 ID
        self.bot_msg_ids = set()       # 自己发出的消息 ID
        self.agent_cooldowns = {}      # agent_id -> 上次发言时间
        self.agent_locks = {a["id"]: threading.Lock() for a in self.agents}
        self.bot_count = 0             # bot 总发言数
        self.running = True
        self.session_start_ts = None
        self.last_activity = time.time()

        # 增强 system prompt
        for agent in self.agents:
            agent["system_prompt"] += REALTIME_SUFFIX

    def _primary_client(self):
        return self.clients[self.agents[0]["id"]]

    def _fetch_new_messages(self):
        """轮询飞书，返回未处理的新消息。"""
        if not self.session_start_ts:
            return []
        msgs = self._primary_client().get_messages(
            self.chat_id, start_time=self.session_start_ts)
        new = []
        for m in msgs:
            mid = m.get("message_id", "")
            if mid in self.seen_msg_ids or mid in self.bot_msg_ids:
                continue
            if mid:
                self.seen_msg_ids.add(mid)
            # 跳过非文本和卡片消息中自己发的
            if m.get("sender_type") == "app":
                # 是 bot 消息，但可能是其他 bot 发的
                # 检查是否是我们自己的 bot
                if mid in self.bot_msg_ids:
                    continue
                # 解析为 bot 消息
                content = m.get("content", {})
                text = content.get("text", "")
                if not text and m.get("msg_type") == "interactive":
                    continue  # 跳过卡片消息（我们自己发的）
                new.append({
                    "sender": f"Bot({m.get('sender_id', '?')[:8]})",
                    "sender_bot_id": m.get("sender_id", ""),
                    "content": text,
                    "is_human": False,
                    "timestamp": time.time(),
                })
            else:
                content = m.get("content", {})
                text = content.get("text", "").strip()
                if not text:
                    continue
                new.append({
                    "sender": "人类",
                    "sender_bot_id": "",
                    "content": text,
                    "is_human": True,
                    "timestamp": time.time(),
                })
        return new

    def _build_realtime_context(self, agent, trigger_msg):
        """构建实时上下文：议题 + 最近消息 + 触发消息。"""
        parts = [f"## 议题\n{self.topic}\n"]

        # 全部消息历史
        recent = self.message_log
        if recent:
            parts.append("## 最近的群聊消息\n")
            for m in recent:
                parts.append(f"**{m['sender']}**: {m['content']}\n")

        # 触发消息
        parts.append(
            f"\n## 最新消息（触发你思考的）\n"
            f"**{trigger_msg['sender']}**: {trigger_msg['content']}\n\n"
            f"你是 {agent['display_name']}（{agent['role']}）。\n"
            f"看到这条消息后，你要不要回应？\n"
            f"如果有独特视角或不同意见，直接给出观点（100字以内）。\n"
            f"如果没有，只回复 [SKIP]。"
        )
        return "\n".join(parts)

    def _agent_react(self, agent, trigger_msg):
        """agent 看到新消息后决定是否回应。"""
        aid = agent["id"]

        # 冷却期检查：15 秒内不重复发言
        cooldown = self.agent_cooldowns.get(aid, 0)
        if time.time() - cooldown < 15:
            return

        with self.agent_locks[aid]:
            # 再次检查（可能在等锁期间已经冷却过了）
            if not self.running:
                return

            context = self._build_realtime_context(agent, trigger_msg)
            try:
                response = call_llm(agent["system_prompt"], context, self.rules)
            except Exception as e:
                print(f"  [{agent['display_name']}] LLM error: {e}", file=sys.stderr)
                return

            # 判断是否 SKIP
            if "[SKIP]" in response or not response.strip() or len(response.strip()) < 5:
                sys.stderr.write(f"  [{agent['display_name']}] skip\n")
                return

            # 随机延迟，模拟思考
            time.sleep(random.uniform(1, 4))

            if not self.running:
                return

            sys.stderr.write(f"  [{agent['display_name']}] → {len(response)} chars\n")

            # 发送到飞书
            card = build_agent_card(agent, 0, response)
            result = self.clients[aid].send_card(self.chat_id, card)
            if result and result.get("data"):
                mid = result["data"].get("message_id", "")
                if mid:
                    self.bot_msg_ids.add(mid)

            # 记录
            self.agent_cooldowns[aid] = time.time()
            self.bot_count += 1
            self.last_activity = time.time()
            new_msg = {
                "sender": agent["display_name"],
                "sender_bot_id": aid,
                "content": response,
                "is_human": False,
                "timestamp": time.time(),
            }
            self.message_log.append(new_msg)

            # 把自己的回应也分发给其他 agent，驱动对话继续
            self._dispatch(new_msg)

    def _dispatch(self, msg):
        """把新消息分发给所有 agent。"""
        for agent in self.agents:
            # 不回应自己发的
            if msg.get("sender_bot_id") == agent["id"]:
                continue
            t = threading.Thread(target=self._agent_react, args=(agent, msg), daemon=True)
            t.start()

    def _opening_round(self):
        """开场轮：每个 agent 依次发表开场观点。"""
        print("[realtime] Opening round...", file=sys.stderr)
        cooldown = self.rules.get("cooldown_sec", 3)
        for agent in self.agents:
            context = (
                f"## 议题\n{self.topic}\n\n"
                f"你是 {agent['display_name']}（{agent['role']}）。\n"
                f"请就这个议题发表你的开场观点，100-200字。直接说观点，不要自我介绍。"
            )
            # 之前的 agent 发言也加入上下文
            if self.message_log:
                prev = "\n".join(f"**{m['sender']}**: {m['content']}" for m in self.message_log)
                context = f"## 议题\n{self.topic}\n\n## 其他专家已发言\n{prev}\n\n" \
                          f"你是 {agent['display_name']}（{agent['role']}）。\n" \
                          f"请就这个议题发表你的开场观点，100-200字。可以回应前面的观点。"

            sys.stderr.write(f"  [{agent['display_name']}] opening...")
            sys.stderr.flush()
            try:
                response = call_llm(agent["system_prompt"], context, self.rules)
            except Exception as e:
                response = f"（开场失败：{str(e)[:80]}）"
            sys.stderr.write(f" done\n")

            card = build_agent_card(agent, 0, response)
            result = self.clients[agent["id"]].send_card(self.chat_id, card)
            if result and result.get("data"):
                mid = result["data"].get("message_id", "")
                if mid:
                    self.bot_msg_ids.add(mid)

            self.message_log.append({
                "sender": agent["display_name"],
                "sender_bot_id": agent["id"],
                "content": response,
                "is_human": False,
                "timestamp": time.time(),
            })
            self.agent_cooldowns[agent["id"]] = time.time()
            self.bot_count += 1
            time.sleep(cooldown)

        # 开场轮结束后，把最后一条发言分发给其他 agent 触发接话
        if self.message_log:
            last_msg = self.message_log[-1]
            print("[realtime] Triggering reactions to opening remarks...", file=sys.stderr)
            # 重置冷却期，让 agent 可以立即回应
            self.agent_cooldowns.clear()
            self._dispatch(last_msg)
            # 等待 agent 回应
            time.sleep(10)

    def run(self):
        max_bot_msgs = self.rules.get("max_bot_messages", 80)
        idle_timeout = self.rules.get("idle_timeout_sec", 300)

        print(f"[realtime] Session {self.session_id}", file=sys.stderr)
        print(f"[realtime] Topic: {self.topic}", file=sys.stderr)
        for a in self.agents:
            print(f"  {a['display_name']} ({a['role']}) ← {','.join(a['expert_ids'])}", file=sys.stderr)
        print(f"[realtime] Limits: {max_bot_msgs} msgs, {idle_timeout}s idle timeout", file=sys.stderr)

        # 发送开始卡片
        names = "、".join(a["display_name"] for a in self.agents)
        content = (
            f"**议题：**{self.topic}\n\n"
            f"**参与者：**{names}\n\n"
            f"**模式：**🔴 实时群聊\n\n"
            f"💡 群内发消息可随时插话，发「停止」结束讨论"
        )
        card = build_status_card("red", "🏛️ 实时议事开始", self.session_id, content)
        self._primary_client().send_card(self.chat_id, card)
        self.session_start_ts = str(int(time.time()))
        time.sleep(2)

        # 开场轮
        self._opening_round()
        self.last_activity = time.time()

        # 进入实时轮询
        print("\n[realtime] Entering realtime mode. Polling every 2s...", file=sys.stderr)
        stop_reason = ""
        try:
            while self.running:
                # 检查结束条件
                if self.bot_count >= max_bot_msgs:
                    stop_reason = f"达到 {max_bot_msgs} 条上限"
                    break
                if time.time() - self.last_activity > idle_timeout:
                    stop_reason = f"静默 {idle_timeout} 秒"
                    break

                # 轮询新消息
                new_msgs = self._fetch_new_messages()
                for msg in new_msgs:
                    self.message_log.append(msg)
                    self.last_activity = time.time()

                    # 检查停止命令
                    if msg.get("is_human") and msg["content"].lower().strip() in ("停止", "stop", "结束"):
                        stop_reason = "人类叫停"
                        self.running = False
                        break

                    # 分发给 agents
                    self._dispatch(msg)

                if not self.running:
                    break
                time.sleep(2)

        except KeyboardInterrupt:
            stop_reason = "手动中断"

        self.running = False
        print(f"\n[realtime] Ended: {stop_reason}", file=sys.stderr)
        print(f"[realtime] Total bot messages: {self.bot_count}", file=sys.stderr)

        # 等待正在进行的 agent 线程完成
        time.sleep(5)

        # ── Stage 2: 互评排名（借鉴 Karpathy llm-council）──
        print("[realtime] Stage 2: Peer review & ranking...", file=sys.stderr)
        transcript_text = "\n\n".join(
            f"**{m['sender']}**: {m['content']}" for m in self.message_log if m.get("content"))

        # 每个 agent 匿名互评其他人的观点
        peer_reviews = []
        for agent in self.agents:
            review_prompt = (
                f"## 议题\n{self.topic}\n\n"
                f"## 完整讨论记录\n{transcript_text}\n\n"
                f"你是 {agent['display_name']}（{agent['role']}）。\n"
                f"请评价这次讨论中各位专家的表现：\n"
                f"1. 谁的观点最有洞察力？为什么？\n"
                f"2. 谁的观点有明显盲区？\n"
                f"3. 按贡献价值排名所有专家（从高到低）\n\n"
                f"格式：先简要点评，最后一行写 RANKING: 专家A > 专家B > 专家C > 专家D"
            )
            sys.stderr.write(f"  [{agent['display_name']}] reviewing...")
            sys.stderr.flush()
            try:
                review = call_llm(agent["system_prompt"], review_prompt, self.rules)
                peer_reviews.append({"reviewer": agent["display_name"], "review": review})
                sys.stderr.write(" done\n")
            except Exception:
                sys.stderr.write(" failed\n")

        # 发送互评卡片
        if peer_reviews:
            review_text = "\n\n".join(
                f"**{r['reviewer']}** 的评价：\n{r['review']}" for r in peer_reviews)
            review_card = build_status_card("turquoise", "🏅 互评排名",
                                            f"{len(peer_reviews)} 位专家互评", review_text)
            self._primary_client().send_card(self.chat_id, review_card)

        # ── Stage 3: Chairman 综合总结 ──
        print("[realtime] Stage 3: Chairman synthesis...", file=sys.stderr)
        reviews_text = "\n\n".join(
            f"{r['reviewer']}: {r['review']}" for r in peer_reviews) if peer_reviews else "无互评数据"
        chairman_prompt = (
            f"你是议事厅主席。多位专家刚刚就以下议题进行了深度讨论和互评。\n\n"
            f"## 议题\n{self.topic}\n\n"
            f"## 讨论记录\n{transcript_text}\n\n"
            f"## 专家互评\n{reviews_text}\n\n"
            f"请综合所有讨论和互评，输出最终总结：\n"
            f"1. **核心共识**：各方达成一致的关键结论\n"
            f"2. **关键分歧**：仍有争议的点及各方立场\n"
            f"3. **最佳洞察**：本次讨论中最有价值的观点（注明来自谁）\n"
            f"4. **行动建议**：基于讨论结果的具体下一步\n\n"
            f"400字以内，有结构有深度。"
        )
        try:
            summary = call_llm("你是一位资深议事厅主席，擅长从多方观点中提炼共识和洞察。", chairman_prompt, self.rules)
        except Exception:
            summary = "（总结生成失败）"

        card = build_status_card("purple", "📋 议事总结",
                                 f"{self.bot_count} 条 · {len(self.agents)} 位 · {stop_reason}",
                                 summary)
        self._primary_client().send_card(self.chat_id, card)

        # 保存
        d = SESSION_DIR / self.session_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps({
            "session_id": self.session_id, "topic": self.topic, "mode": "realtime",
            "agents": [{"id": a["id"], "display_name": a["display_name"],
                        "role": a["role"], "expert_ids": a["expert_ids"]} for a in self.agents],
            "message_log": self.message_log, "bot_count": self.bot_count,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[realtime] Saved: {d / 'transcript.json'}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="飞书议事厅")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("start", help="开始议事")
    p.add_argument("--topic", help="议题")
    p.add_argument("--topic-file", help="从文件读取议题")
    p.add_argument("--experts", help="手动指定专家ID，逗号分隔")
    p.add_argument("--mode", choices=["rounds", "realtime"], default="rounds",
                   help="模式：rounds=编排模式（默认），realtime=实时群聊")

    sub.add_parser("history", help="查看历史")

    args = parser.parse_args()
    if args.command == "start":
        config = load_config()
        topic = args.topic
        if args.topic_file:
            topic = Path(args.topic_file).read_text(encoding="utf-8").strip()
        if not topic:
            print("ERROR: --topic or --topic-file required", file=sys.stderr)
            sys.exit(1)
        if args.mode == "realtime":
            RealtimeCouncil(config, topic, expert_ids=args.experts).run()
        else:
            CouncilSession(config, topic, expert_ids=args.experts).run()
    elif args.command == "history":
        if not SESSION_DIR.exists():
            print("No sessions yet.")
            return
        for s in sorted(SESSION_DIR.iterdir(), reverse=True)[:10]:
            p = s / "transcript.json"
            if p.exists():
                d = json.loads(p.read_text())
                print(f"  {s.name}  |  {d.get('rounds','?')} 轮  |  {d.get('topic','?')[:50]}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
