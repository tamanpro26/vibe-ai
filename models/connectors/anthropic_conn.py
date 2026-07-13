"""
models/connectors/anthropic_conn.py
Connector for Claude models on the real Anthropic API.

Two ModelDefs share this connector:
  claude_sonnet_4.6  — system manager. Uses settings.anthropic_api_key, which is
                       intentionally left invalid so the Free Manager Council
                       (the multi-model consensus system) stays the active path.
  claude_opus_4_6    — optional premium model, manual `/model` selection only.
                       Uses settings.anthropic_api_key_2, a separate real key,
                       so filling it in has no effect on the manager's key above.
Which settings field is read is decided per-ModelDef via api_key_field
(config/models_config.py) — see __init__ below.
"""
from __future__ import annotations

import json
from typing import Any

import anthropic
from loguru import logger

from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class AnthropicConnector(BaseModelConnector):
    """Wraps the Anthropic async client. Supports both plain generation and
    native tool calling (Messages API `tools` param)."""

    # Always stream — per Claude API guidance, non-streaming requests risk
    # hitting HTTP timeouts on long responses, and these models are configured
    # with a high max_tokens (see config/model_params.py). Streaming has no
    # real downside for short responses either.
    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        key_field = model_def.api_key_field or "anthropic_api_key"
        self._client = anthropic.AsyncAnthropic(api_key=getattr(settings, key_field))

    async def _call(
        self,
        prompt: str,
        system: str,
        images: list[str],
        max_tokens: int,
        temperature: float,
        **kwargs: Any,
    ) -> str:
        content: list[dict] = []
        for img in images:
            if img.startswith("http"):
                content.append({"type": "image", "source": {"type": "url", "url": img}})
            else:
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img},
                })
        content.append({"type": "text", "text": prompt})

        # Adaptive thinking for single-shot calls (planning, reasoning, critic
        # passes) — these have no cross-turn replay, so there's no risk of
        # losing thinking-block continuity the way there would be in the
        # tool-calling loop below. Anthropic requires temperature=1 (i.e. the
        # param omitted) when thinking is enabled, so it's left out here.
        create_kwargs = dict(
            model=self.api_model,
            max_tokens=max_tokens,
            system=system or "You are Claude, a helpful AI assistant.",
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
        )

        async with self._client.messages.stream(**create_kwargs) as stream:
            response = await stream.get_final_message()

        return "\n".join(b.text for b in response.content if b.type == "text")

    async def _call_with_tools(
        self,
        messages:    list[dict],
        tools:       list[dict],
        max_tokens:  int,
        temperature: float,
        **kwargs: Any,
    ) -> dict:
        """
        Native Anthropic tool calling for the agent loop. Thinking is
        deliberately NOT enabled here: core/agent_loop.py re-serializes each
        assistant turn into a generic tool_calls format rather than replaying
        raw response content blocks, so thinking blocks (and their signatures)
        wouldn't survive across turns — combining that with extended thinking
        risks API errors on multi-turn tool use, for no benefit since the
        reasoning context wouldn't be preserved anyway.
        """
        system, anthro_messages = self._to_anthropic_messages(messages)
        anthro_tools = self._to_anthropic_tools(tools)

        create_kwargs = dict(
            model=self.api_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "You are VibeAI, an autonomous coding agent.",
            messages=anthro_messages,
            tools=anthro_tools,
        )

        async with self._client.messages.stream(**create_kwargs) as stream:
            response = await stream.get_final_message()

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "args": block.input})

        if tool_calls:
            result: dict = {"type": "tool_calls", "tool_calls": tool_calls}
            if text_parts:
                result["content"] = "\n".join(text_parts)
            return result
        return {"type": "text", "content": "\n".join(text_parts)}

    @staticmethod
    def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
        """OpenAI-shaped {"type":"function","function":{...}} -> Anthropic shape."""
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return out

    @staticmethod
    def _to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
        """
        Convert this codebase's OpenAI-shaped message list (system role message,
        assistant tool_calls, role="tool" results) into (system_prompt, anthropic
        messages). Consecutive role="tool" entries are merged into a single user
        turn with multiple tool_result blocks — Anthropic expects all results for
        one assistant tool_use turn to arrive together, not as separate turns.
        """
        system = ""
        out: list[dict] = []
        pending_tool_results: list[dict] = []

        def flush_tool_results():
            nonlocal pending_tool_results
            if pending_tool_results:
                out.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

        for m in messages:
            role = m.get("role")
            if role == "system":
                system = m.get("content") or system
                continue
            if role == "tool":
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": str(m.get("content", "")),
                })
                continue
            flush_tool_results()
            if role == "assistant":
                blocks: list[dict] = []
                text = m.get("content")
                if text:
                    blocks.append({"type": "text", "text": text})
                for tc in m.get("tool_calls", []) or []:
                    fn = tc.get("function", tc)
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": args,
                    })
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif role == "user":
                out.append({"role": "user", "content": m.get("content", "")})
            else:
                logger.warning(f"[anthropic_conn] dropping unrecognized message role: {role}")
        flush_tool_results()
        return system, out
