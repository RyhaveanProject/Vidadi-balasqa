"""Gemini brain — supports direct google-generativeai OR emergent universal key."""
from __future__ import annotations

import asyncio
from typing import List, Tuple

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
                from emergentintegrations.llm.chat import LlmChat  # noqa
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

    async def reply(
        self,
        user_text: str,
        speaker_name: str,
        history: List[Tuple[str, str]],
        in_voice_chat: bool = False,
    ) -> str:
        """history: list of (speaker_name, text) tuples, oldest first."""
        if not user_text.strip():
            return ""

        ctx = self._build_context(user_text, speaker_name, history, in_voice_chat)

        try:
            if self.provider == "emergent":
                return await self._call_emergent(ctx, speaker_name)
            return await self._call_gemini(ctx)
        except Exception as e:
            log.exception("Brain.reply failed: {}", e)
            return self._fallback_line()

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

    async def _call_gemini(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None, lambda: self._gemini_model.generate_content(prompt)
        )
        text = (resp.text or "").strip()
        return _post_filter(text)

    async def _call_emergent(self, prompt: str, session_id: str) -> str:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=settings.EMERGENT_LLM_KEY,
            session_id=f"vidadi-{session_id}",
            system_message=VIDADI_SYSTEM_PROMPT,
        ).with_model("gemini", self.model_name)
        resp = await chat.send_message(UserMessage(text=prompt))
        return _post_filter(str(resp).strip())

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
    # Trim quotes
    return text.strip(' "\'')


def _normalize_gemini_model(name: str) -> str:
    # google-generativeai 0.8.x supports current Gemini model IDs directly.
    # The previous mapping pinned to `gemini-2.0-flash-exp`, which Google has
    # since retired — causing every API call to 404 and the brain to fall
    # back to the same 3 hard-coded lines (the "AI keeps repeating itself"
    # bug reported by the owner). We pass the env-configured model name
    # through unchanged when it's a known current model, and only remap
    # legacy aliases.
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
