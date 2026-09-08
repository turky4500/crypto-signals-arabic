"""منع الإشارات المكررة — الاعتماد على (Symbol, Indicator, Signal Type, Candle Open Time)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class DuplicateGuard:
    def __init__(self):
        self._keys: set[str] = set()

    @classmethod
    def _extract_signatures(cls, signals: list[dict]) -> set[str]:
        out: set[str] = set()
        for s in signals or []:
            sig = s.get("signature")
            if sig:
                out.add(sig)
                continue
            # إعادة بناء دون الاعتماد على الحقل المخزن
            out.add(
                f"{s.get('symbol')}|{s.get('indicator')}|{s.get('signal_type')}|{s.get('candle_open_ms')}"
            )
        out.discard("None|None|None|None")
        return out

    def load_history(self, signals: list[dict]):
        self._keys = self._extract_signatures(signals)
        logger.info("DuplicateGuard: %d key(s) محمّل من السجل", len(self._keys))

    def is_duplicate(self, signature: str) -> bool:
        return signature in self._keys

    def add(self, signature: str):
        self._keys.add(signature)