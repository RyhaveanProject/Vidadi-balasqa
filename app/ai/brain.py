"""Gemini brain — supports direct google-generativeai OR emergent universal key.

Two reply modes:

* ``reply(...)`` — single-shot, returns the full string. Used by the
  text-chat handler where streaming chunks would add no benefit.
* ``reply_stream(...)`` — async generator yielding partial chunks
  as the model produces them.  Used by the voice-chat pipeline so
  the first TTS clip can start playing before the LLM has finished.

Streaming uses Gemini's native ``stream=True`` flag where available;
falls back to single-shot for the emergent universal-key path
(which doesn't expose token streaming in the current build).
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, List, Tuple

from app.config.settings import settings
from app.config.personality import VIDADI_SYSTEM_PROMPT, REFUSAL_DEFLECTIONS
from app.core.logger import log


class Brain:
    """Unified LLM facade. Switches backend by LLM_PROVIDER env var."""

    def __init__(self) -> None:
        self.provider = settings.LLM_PROVIDER
        self.model_name = settings.LLM_MODEL
        self._gemini_model = None
        self._init_backend()

    def _init_backend(self) -> None:
        if self.provider == "emergent":
            try:
                from emergentintegrations.llm.chat import LlmChat  # noqa: F401
                log.info("Brain: using EMERGENT universal key — model={}", self.model_name)
            except ImportError:
                log.error("emergentintegrations not installed; falling back to gemini")
                self.provider = "gemini"

        if self.provider == "gemini":
            import google.generativeai as genai
            genai.configure(api_key=settings.GEMINI_API_KEY)
            self._gemini_model = genai.GenerativeModel(
                model_name=_normalize_gemini_model(self.model_name),
                system_instruction=VIDADI_SYSTEM_PROMPT,
                generation_config={
                    "temperature": 0.95,
                    "top_p": 0.95,
                    "max_output_tokens": 220,
                },
            )
            log.info("Brain: using direct GEMINI — model={}", self.model_name)

    # ------------------------------------------------------------------
    # Single-shot
    # ------------------------------------------------------------------

    async def reply(
        self,
        user_text: str,
        speaker_name: str,
        history: List[Tuple[str, str]],
        in_voice_chat: bool = False,
    ) -> str:
        if not user_text.strip():
            return ""
        ctx = self._build_context(user_text, speaker_name, history, in_voice_chat)
        try:
            if self.provider == "emergent":
                return await self._call_emergent(ctx, speaker_name)
            return await self._call_gemini(ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("Brain.reply failed: {}", e)
            return self._fallback_line()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def reply_stream(
        self,
        user_text: str,
        speaker_name: str,
        history: List[Tuple[str, str]],
        in_voice_chat: bool = True,
    ) -> AsyncIterator[str]:
        """Async generator yielding text chunks as the model produces them."""
        if not user_text.strip():
            return
        ctx = self._build_context(user_text, speaker_name, history, in_voice_chat)

        if self.provider == "gemini" and self._gemini_model is not None:
            async for chunk in self._stream_gemini(ctx):
                yield chunk
            return

        # Emergent / fallback — no native streaming; yield as a single chunk
        try:
            full = await (
                self._call_emergent(ctx, speaker_name)
                if self.provider == "emergent"
                else self._call_gemini(ctx)
            )
            if full:
                yield full
        except Exception as e:  # noqa: BLE001
            log.exception("Brain.reply_stream failed: {}", e)
            yield self._fallback_line()

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------

    async def _call_gemini(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self._gemini_model.generate_content(prompt)
        )
        text = (resp.text or "").strip()
        return _post_filter(text)

    async def _stream_gemini(self, prompt: str) -> AsyncIterator[str]:
        """Bridge google-generativeai's sync streaming iterator to async."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _producer() -> None:
            try:
                stream = self._gemini_model.generate_content(
                    prompt, stream=True,
                )
                for ev in stream:
                    txt = getattr(ev, "text", None)
                    if txt:
                        loop.call_soon_threadsafe(queue.put_nowait, txt)
            except Exception as e:  # noqa: BLE001
                log.warning("gemini stream error: {}", e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _producer)

        leaked = ""
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            leaked += chunk
            filtered = _post_filter(chunk)
            # If post-filter swapped the chunk for a refusal line, end early
            if filtered != chunk:
                yield filtered
                return
            yield chunk

        if not leaked.strip():
            yield self._fallback_line()

    async def _call_emergent(self, prompt: str, session_id: str) -> str:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=settings.EMERGENT_LLM_KEY,
            session_id=f"vidadi-{session_id}",
            system_message=VIDADI_SYSTEM_PROMPT,
        ).with_model("gemini", self.model_name)
        resp = await chat.send_message(UserMessage(text=prompt))
        return _post_filter(str(resp).strip())

    # ------------------------------------------------------------------
    # Context + fallback
    # ------------------------------------------------------------------

    def _build_context(
        self,
        user_text: str,
        speaker_name: str,
        history: List[Tuple[str, str]],
        in_voice_chat: bool,
    ) -> str:
        h = "\n".join(f"{n}: {t}" for n, t in history[-8:])
        mode = "VOICE_CHAT (qısa cavab, 5-15 söz)" if in_voice_chat else "TEXT_CHAT"
        return (
            f"[Rejim: {mode}]\n"
            f"[Qrup söhbəti — son mesajlar]\n{h}\n\n"
            f"{speaker_name}: {user_text}\n"
            f"{settings.BOT_NAME}:"
        )

    @staticmethod
    def _fallback_line() -> str:
        import random
        return random.choice([
            "hmm, bir saniyə brat",
            "ala başım qarışıb azca, sora deyirəm",
            "həə nə deyirdin?",
        ])


_FORBIDDEN = (
    "ai modeli", "language model", "openai", "gemini", "chatgpt", "google",
    "i'm an ai", "as an ai", "virtual assistant", "süni intellekt",
)


def _post_filter(text: str) -> str:
    low = text.lower()
    for word in _FORBIDDEN:
        if word in low:
            import random
            return random.choice(REFUSAL_DEFLECTIONS)
    return text.strip(' "\'')


def _normalize_gemini_model(name: str) -> str:
    legacy_mapping = {
        "gemini-3-flash-preview": "gemini-2.5-flash",
        "gemini-3.1-pro-preview": "gemini-2.5-pro",
        "gemini-2.0-flash-exp": "gemini-2.0-flash",
    }
    return legacy_mapping.get(name, name)


brain = Brain.__new__(Brain)  # lazy init below


def get_brain() -> Brain:
    global brain
    if not isinstance(brain, Brain) or brain.__dict__ == {}:
        brain = Brain()
    return brain
