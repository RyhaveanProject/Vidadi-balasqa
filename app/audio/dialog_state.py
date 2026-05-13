"""Dialog state machine — coordinates listen / think / speak / interrupt.

The pipeline runs four logical states for an active voice chat:

* IDLE       — bot is silent, listening for user speech
* LISTENING  — VAD has detected speech, accumulating frames
* THINKING   — STT done, waiting for the LLM reply
* SPEAKING   — TTS playback in progress

Full-duplex behaviour:
    Inbound speech that lands while the bot is SPEAKING triggers an
    interrupt — the current TTS playback is cancelled and the new
    user utterance takes priority.  Bot reply queue is per-chat
    bounded so we never stack a backlog of replies.

The state machine is intentionally tiny — no external deps.  It
exposes:

    state.bot_is_speaking()            -> bool
    state.user_interrupted()           -> bool   (one-shot consumed)
    state.mark_user_speaking()
    state.mark_bot_speak_start()
    state.mark_bot_speak_done()
    state.request_interrupt()
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum


class DialogPhase(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class DialogState:
    def __init__(self) -> None:
        self.phase: DialogPhase = DialogPhase.IDLE
        self._interrupt_flag: bool = False
        self._last_user_speech_ts: float = 0.0
        self._last_bot_speech_ts: float = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Booleans / queries
    # ------------------------------------------------------------------

    def bot_is_speaking(self) -> bool:
        return self.phase == DialogPhase.SPEAKING

    def is_idle(self) -> bool:
        return self.phase == DialogPhase.IDLE

    def consume_interrupt(self) -> bool:
        """Return True at most once after an interrupt was requested."""
        if self._interrupt_flag:
            self._interrupt_flag = False
            return True
        return False

    def last_user_speech_age(self) -> float:
        return time.time() - self._last_user_speech_ts if self._last_user_speech_ts else 1e9

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def mark_user_speaking(self) -> None:
        self._last_user_speech_ts = time.time()
        if self.phase == DialogPhase.SPEAKING:
            # Bot is talking and user started — request interrupt.
            self._interrupt_flag = True
        if self.phase == DialogPhase.IDLE:
            self.phase = DialogPhase.LISTENING

    def mark_thinking(self) -> None:
        self.phase = DialogPhase.THINKING

    def mark_bot_speak_start(self) -> None:
        self._last_bot_speech_ts = time.time()
        self.phase = DialogPhase.SPEAKING
        self._interrupt_flag = False

    def mark_bot_speak_done(self) -> None:
        if self.phase == DialogPhase.SPEAKING:
            self.phase = DialogPhase.IDLE

    def request_interrupt(self) -> None:
        self._interrupt_flag = True
