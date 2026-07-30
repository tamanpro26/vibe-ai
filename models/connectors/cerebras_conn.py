"""
models/connectors/cerebras_conn.py
Cerebras — free, 1M tokens/day, 2,600+ tokens/sec, tool calling.
Sign up: https://cloud.cerebras.ai
"""
from __future__ import annotations
import json
import re
from typing import Any
from loguru import logger
from openai import AsyncOpenAI
from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class CerebrasConnector(BaseModelConnector):
    BASE_URL = "https://api.cerebras.ai/v1"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        # timeout: found in code review (2026-07-13) that no connector set one,
        # relying on SDK defaults (~600s) and blocking the fallback chain on a hang.
        self._client = AsyncOpenAI(
            api_key=settings.cerebras_api_key or "not-configured", base_url=self.BASE_URL,
            timeout=settings.default_timeout_ms / 1000,
        )

    async def _call(self, prompt, system, images, max_tokens, temperature, **kwargs) -> str:
        if not settings.cerebras_api_key:
            raise RuntimeError("CEREBRAS_API_KEY not set — free at https://cloud.cerebras.ai")
        messages = []
        if system: messages.append({"role": "system",  "content": system})
        messages.append({"role": "user", "content": prompt})
        # Both Cerebras free-tier models (zai-glm-4.7, gpt-oss-120b) spend hundreds
        # of tokens on hidden reasoning before emitting content; a small max_tokens
        # truncates mid-reasoning and returns None.
        max_tokens = max(max_tokens, 2048)
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages,
            max_tokens=min(max_tokens, 16_384), temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    # Input budget (token estimate). Cerebras free tier rejects long contexts
    # with 400 context_length_exceeded — and the limit FLUCTUATES: runs on
    # 2026-07-02/03 carried far larger contexts fine, then on 2026-07-04 the
    # error reported "limit is 8192". So: start from the worst observed limit,
    # and additionally ADAPT at runtime — the error body states the current
    # limit exactly ("Current length is N while limit is M"), which we parse
    # and store per api_model, then retry once with tighter truncation.
    _INPUT_BUDGET_TOKENS = 6_000
    _DISCOVERED_LIMITS: dict[str, int] = {}   # api_model -> tokens (from error bodies)
    _CTX_LIMIT_RE = re.compile(r"limit is (\d+)")

    @staticmethod
    def _est(text: str) -> int:
        return max(1, len(text) // 3)  # conservative chars/token for code+JSON

    def _budget(self) -> int:
        """Effective input budget: discovered live limit (minus completion
        headroom) if we've seen one, else the conservative default."""
        discovered = self._DISCOVERED_LIMITS.get(self.api_model)
        if discovered:
            return max(2_000, int(discovered * 0.75))
        return self._INPUT_BUDGET_TOKENS

    # Chars reserved for the anchor user message when truncation would
    # otherwise leave the model with no conversation at all (~700 tokens).
    _ANCHOR_MAX_CHARS = 2_000

    @classmethod
    def _anchor_content(cls, content: str) -> str:
        """Bounded slice of the first user message for re-anchoring.

        HEAD + TAIL, not head-only: the agent loop PREPENDS injected context
        (skills, workspace scan, repo map, plan spec — routinely 3-8k chars)
        before the task text in the first user message, so the actual task
        lives at the TAIL. Verified live (2026-07-09): a head-only slice
        re-anchored the model onto skills text + a directory listing with
        ZERO task content — a softer relapse of the invented-project bug
        this anchor exists to prevent. Head is kept too because a raw
        pasted task (no injected context) states its imperative first.
        """
        if len(content) <= cls._ANCHOR_MAX_CHARS:
            return content
        head = content[:500]
        tail = content[-(cls._ANCHOR_MAX_CHARS - 520):]
        return f"{head}\n...\n{tail}"

    @staticmethod
    def _pick_anchor_message(convo: list[dict]) -> str:
        """The content to re-anchor on. NOT simply the first user message:
        in a session that carries chat history, the first user message can
        be a greeting -- observed live (2026-07-12): an agent run whose CLI
        chat began with "hi" re-anchored on those 2 characters (logged
        "re-anchored on the original task (2 chars)"), so mid-run the model
        had neither its task nor its history and wandered off into fixing a
        leftover project.

        Must be the LAST substantial user message, not the first -- caught
        live while self-testing this exact fix (2026-07-12): _build_messages
        appends the CURRENT task AFTER all prior history, so a real
        multi-turn CLI session with an earlier substantial request (e.g. "can
        you help me reorganize the old marketing project directory structure
        please") would re-anchor on that STALE request instead of the
        actual current task -- the identical failure shape (wrong task in
        context) reintroduced by fixing the greeting case in the wrong
        direction. Falls back to the longest user message if none crosses
        the substantiality bar."""
        users = [str(m.get("content", "")) for m in convo if m.get("role") == "user"]
        if not users:
            return ""
        for c in reversed(users):
            if len(c.strip()) >= 40:
                return c
        return max(users, key=len)

    @staticmethod
    def _group_turns(convo: list[dict]) -> list[list[dict]]:
        """Group messages into TURNS: each block starts with a non-tool
        message and absorbs every tool message that immediately follows it.
        A tool result and its parent assistant tool_calls message travel
        together or not at all — never separated."""
        blocks: list[list[dict]] = []
        for m in convo:
            if m.get("role") != "tool" or not blocks:
                blocks.append([m])
            else:
                blocks[-1].append(m)
        return blocks

    def _turn_cost(self, turn: list[dict]) -> int:
        return sum(self._est(str(m.get("content", "")) + str(m.get("tool_calls", ""))) for m in turn)

    def _truncate(self, messages: list[dict], tools: list[dict] | None = None) -> list[dict]:
        """Sliding window over whole TURNS (not raw messages): keep system +
        newest turns within the budget.

        Turn-grouping fixes a bug found live (2026-07-07): the previous
        message-level window could keep a lone tool result whose parent
        assistant tool_calls message didn't fit, which then got stripped by
        the orphan-guard, repeatedly emptying the window on EVERY call once
        the conversation grew past budget — not just occasionally. The
        model was silently re-anchored to just the bare task on nearly every
        turn, with zero memory of its own prior actions, and it re-created
        the same handful of files from scratch 3-4 times each across one run
        instead of ever building on them. Grouping by turn means a tool
        result and the assistant call that produced it are kept or dropped
        together, so a turn that fits is real, usable history — not a
        dangling fragment.

        INVARIANT: the result must always contain at least one user message.
        Originally added 2026-07-06 for a related but distinct failure: when
        NO turn at all survives, the model was called with only the system
        prompt + tool schemas — no task, no history — and instead of failing
        it INVENTED a project from the one example string inside the
        design_asset tool schema (built an entire Minecraft-hosting site
        mid-way through an unrelated task). Kept as a last-resort safety net;
        turn-grouping above should make it rare rather than the common case.
        """
        system = [m for m in messages if m.get("role") == "system"]
        convo  = [m for m in messages if m.get("role") != "system"]
        remaining = self._budget()
        # The tools schema rides along on every real API call but was
        # invisible to this budget -- with 10+ tools (~3400 tokens measured
        # live) that's over half of the default 6000-token budget silently
        # unaccounted for, so "truncate to fit" was truncating to fit a
        # request ~3400 tokens smaller than the one actually sent. Mirrors
        # groq_conn.py's _truncate_messages, which already subtracts this.
        if tools:
            remaining -= self._est(json.dumps(tools))
        for m in system:
            remaining -= self._est(str(m.get("content", "")))

        turns = self._group_turns(convo)
        kept_turns: list[list[dict]] = []
        for turn in reversed(turns):
            cost = self._turn_cost(turn)
            if remaining - cost < 0 and kept_turns:
                break
            remaining -= cost
            kept_turns.append(turn)
        kept_turns.reverse()
        kept: list[dict] = [m for turn in kept_turns for m in turn]

        # Anchor guarantee: if no user message survived (every kept turn was
        # assistant/tool-only, or nothing survived at all), re-anchor on the
        # FIRST user message — in the agent loop that's the original task —
        # hard-truncated so it can't blow the budget that caused this.
        if not any(m.get("role") == "user" for m in kept):
            anchor_src = self._pick_anchor_message(convo)
            if anchor_src:
                content = self._anchor_content(anchor_src)
                kept.insert(0, {"role": "user", "content": content})
                logger.warning(
                    f"[cerebras/{self.api_model}] truncation left no user message — "
                    f"re-anchored on the original task ({len(content)} chars)"
                )
        if len(kept) < len(convo):
            logger.info(
                f"[cerebras/{self.api_model}] truncated context: "
                f"kept {len(kept)}/{len(convo)} messages within input budget"
            )
        return system + kept

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        """Cerebras supports OpenAI-compatible function calling."""
        if not settings.cerebras_api_key:
            raise RuntimeError("CEREBRAS_API_KEY not set")
        truncated = self._truncate(messages, tools)
        try:
            resp = await self._client.chat.completions.create(
                model=self.api_model, messages=truncated, tools=tools,
                tool_choice="auto", max_tokens=min(max_tokens, 8_192), temperature=temperature,
            )
        except Exception as exc:
            # Adaptive context handling: the error body states the live limit
            # exactly — learn it, re-truncate, retry once.
            text = str(exc)
            if "context_length_exceeded" in text or "reduce the length" in text:
                m = self._CTX_LIMIT_RE.search(text)
                if m:
                    self._DISCOVERED_LIMITS[self.api_model] = int(m.group(1))
                    logger.warning(
                        f"[cerebras/{self.api_model}] live context limit discovered: "
                        f"{m.group(1)} tokens — re-truncating and retrying"
                    )
                truncated = self._truncate(messages, tools)
                resp = await self._client.chat.completions.create(
                    model=self.api_model, messages=truncated, tools=tools,
                    tool_choice="auto", max_tokens=min(max_tokens, 4_096), temperature=temperature,
                )
            else:
                raise
        msg = resp.choices[0].message
        if msg.tool_calls:
            calls = [
                {"id": tc.id, "name": tc.function.name,
                 "args": json.loads(tc.function.arguments or "{}")}
                for tc in msg.tool_calls
            ]
            return {"type": "tool_calls", "tool_calls": calls}
        return {"type": "text", "content": msg.content or ""}
