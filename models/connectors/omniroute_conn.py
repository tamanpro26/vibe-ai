"""
models/connectors/omniroute_conn.py
Connector for a locally-run OmniRoute gateway (github.com/diegosouzapw/OmniRoute)
-- an OpenAI-compatible AI gateway that aggregates free-tier provider pools
behind one endpoint, with its own auto-routing ("auto/best-coding" etc.) and
usage tracking. Uses the openai SDK with a custom base_url, same shape as
openrouter.py.

Live-verified 2026-07-28 against a local instance on :20128 with 6 providers
connected (gemini, groq, cerebras, openrouter, nvidia, zai): "auto/best-coding"
resolved to nvidia/llama-3.1-nemotron-nano-vl-8b-v1, and groq/gemini/cerebras
model IDs were called directly, each with a real content response, a clean
"stop" finish_reason, and real token usage.

Deliberately NOT a primary/default connector. This is a local dev-mode Node
server the user starts manually (`npm run dev` in the OmniRoute checkout) --
it is not guaranteed to be running. Wiring it as a fallback tier is safe
because the existing circuit-breaker/generate_resilient machinery already
routes around any connector that fails to connect, exactly as it does for
every other provider.

Most of OmniRoute's connected providers (groq, cerebras, gemini, nvidia) are
ALSO called directly elsewhere in this registry via their own connectors --
going through OmniRoute for those specifically would add a local-gateway hop
for no new capability. The actual new value is "auto/*" meta-routing (an
OmniRoute-side pick of the best currently-available free model across every
connected provider) and its no-auth provider pool, neither of which VibeAI
has a direct equivalent for.
"""
from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from loguru import logger

from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class OmniRouteConnector(BaseModelConnector):
    """Routes calls through a local OmniRoute gateway's OpenAI-compatible API."""

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        # timeout: same reasoning as every other connector in this project --
        # no timeout means falling back to the SDK default (~600s) and
        # blocking the whole fallback chain on a hang.
        self._client = AsyncOpenAI(
            api_key=settings.omniroute_api_key or "not-configured",
            base_url=settings.omniroute_base_url,
            timeout=settings.default_timeout_ms / 1000,
        )

    async def _call(
        self,
        prompt: str,
        system: str,
        images: list[str],
        max_tokens: int,
        temperature: float,
        **kwargs: Any,
    ) -> str:
        if not settings.omniroute_api_key:
            raise RuntimeError("OMNIROUTE_API_KEY not set -- get one from the local dashboard's Endpoints page")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        if images and self.supports("vision"):
            content: list[dict] = [{"type": "text", "text": prompt}]
            for img in images:
                url = img if img.startswith("http") else f"data:image/jpeg;base64,{img}"
                content.append({"type": "image_url", "image_url": {"url": url}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        # Floor the token budget, same rule and same reason as
        # cerebras_conn.py. OmniRoute's "auto/*" routes are NON-DETERMINISTIC:
        # consecutive identical calls landed on
        # nvidia/llama-3.1-nemotron-nano-vl-8b-v1, then
        # nvidia/nemotron-3-nano-omni-30b-a3b-reasoning, then
        # nvidia/nemotron-nano-12b-v2-vl. When it picks a *reasoning* variant
        # those models emit `delta.reasoning` tokens before any
        # `delta.content`, so a small budget is spent entirely on hidden
        # reasoning and the visible answer is empty. Measured live
        # (2026-07-28): max_tokens=16 -> 19 chunks, content '' ; max_tokens=400
        # -> finish_reason 'stop', content 'OK'. An empty return is not
        # harmless here -- models/base.py raises on empty output, so tenacity
        # retries the whole ~40s call three times and the caller sees a
        # multi-minute hang rather than a fast failure.
        max_tokens = max(max_tokens, 2048)

        # ALWAYS stream. This is not a preference -- OmniRoute's "auto/*"
        # meta-routes never return a non-streaming body. Verified live
        # (2026-07-28): auto/best-coding with stream:false hangs until the
        # client gives up (HTTP 000, 0 bytes, 75s+ in curl; APITimeoutError
        # after ~280s of SDK retries), while the identical request with
        # streaming returns immediately. Direct provider models such as
        # groq/llama-3.3-70b-versatile DO answer non-streaming (HTTP 200 in
        # 0.73s), so this is specific to the auto router -- but streaming
        # works for BOTH, so we stream unconditionally rather than branch on
        # a model-name pattern that would silently rot as the catalog changes.
        stream = await self._client.chat.completions.create(
            model=self.api_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            **{k: v for k, v in kwargs.items() if k not in ("stream", "task_type")},
        )
        parts: list[str] = []
        async for chunk in stream:
            if not chunk.choices:
                continue                      # final usage-only chunk
            piece = chunk.choices[0].delta.content
            if piece:
                parts.append(piece)
        return "".join(parts)

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        """OmniRoute tool calling, for whichever underlying model it routes to."""
        import json
        if not settings.omniroute_api_key:
            raise RuntimeError("OMNIROUTE_API_KEY not set")
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        try:
            # Streamed for the same reason as _call above (auto/* never
            # answers non-streaming). Streamed tool calls arrive as fragments
            # that must be reassembled by index: `id` and `function.name`
            # typically land on the first fragment for a given index, while
            # `function.arguments` is split across many later fragments and
            # is only valid JSON once fully concatenated.
            stream = await self._client.chat.completions.create(
                model=self.api_model, messages=messages, tools=tools,
                tool_choice="auto", max_tokens=min(max_tokens, 32_768),
                temperature=temperature, stream=True,
            )
            parts: list[str] = []
            acc: dict[int, dict] = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue                  # final usage-only chunk
                delta = chunk.choices[0].delta
                if delta.content:
                    parts.append(delta.content)
                for tc in (delta.tool_calls or []):
                    slot = acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["args"] += tc.function.arguments
            content = "".join(parts)

            if acc:
                calls = []
                for _idx, slot in sorted(acc.items()):
                    if not slot["name"]:
                        continue              # never resolved into a real call
                    try:
                        args = json.loads(slot["args"] or "{}")
                    except json.JSONDecodeError:
                        # Truncated/!valid fragment stream -- skip this call
                        # rather than crash the agent loop on a bad payload.
                        logger.warning(
                            f"[omniroute] dropped tool call {slot['name']!r}: unparseable streamed arguments"
                        )
                        continue
                    calls.append({"id": slot["id"], "name": slot["name"], "args": args})
                if calls:
                    result = {"type": "tool_calls", "tool_calls": calls}
                    if content:
                        result["content"] = content
                    return result
            return {"type": "text", "content": content}
        except Exception as exc:
            # The underlying auto-routed model may not support tools -- fall
            # back to text-only, same pattern as openrouter.py.
            logger.debug(f"[omniroute] tool call failed, falling back to text: {exc}")
            last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return {"type": "text", "content": await self._call(str(last), system, [], max_tokens, temperature)}
