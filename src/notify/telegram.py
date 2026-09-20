"""عميل Telegram (Bot API) — قناة احتياطية تُستخدم عند تعطل واتساب.

يرسل عبر sendMessage الرسمي إلى chat_id مسجّل مسبقًا، بتصميم مطابق لعميل
WhatsApp (Retry محدود + تسجيل الأخطاء). البيانات تأتي من Environment Variables.
"""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        timeout: float = 25.0,
        max_retries: int = 3,
        backoff: float = 3.0,
    ):
        if not token or not chat_id:
            raise TelegramError("إعدادات Telegram ناقصة (Token / Chat ID)")
        self.token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.session = requests.Session()

    def _method_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def ping(self, timeout: float = 8.0) -> tuple[bool, str]:
        """فحص حي: التحقق من صحة توكن البوت عبر getMe.

        التوكن غير الصالح يُرجع HTTP 401، والتوكن الصالح 200 — يُستخدم لذلك
        لإظهار حالة اتصال تلغرام صادقة في اللوحة.
        """
        try:
            resp = self.session.get(self._method_url("getMe"), timeout=timeout)
            if resp.status_code < 300:
                return True, f"HTTP {resp.status_code}"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as exc:
            return False, str(exc)[:200]
        except Exception as exc:  # أي خطأ شبكة آخر
            return False, str(exc)[:200]

    def send(self, message: str) -> dict:
        """إرسال رسالة نصية إلى chat_id مع Retry محدود — يمنع الإرسال المتكرر لنفس الفشل."""
        payload = {"chat_id": self.chat_id, "text": message}
        last_error = "unknown"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(
                    self._method_url("sendMessage"),
                    json=payload,
                    timeout=self.timeout,
                )
                body = resp.text[:500]
                if resp.status_code < 300:
                    logger.info("Telegram sent (attempt %s): HTTP %s", attempt, resp.status_code)
                    return {"ok": True, "attempts": attempt, "code": resp.status_code, "body": body}
                last_error = f"HTTP {resp.status_code}: {body}"
                logger.warning("Telegram HTTP %s (attempt %s)", resp.status_code, attempt)
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("Telegram request failed (attempt %s): %s", attempt, exc)
            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)
        logger.error("Telegram فشل بعد %s محاولة: %s", self.max_retries, last_error)
        return {"ok": False, "attempts": self.max_retries, "error": last_error}