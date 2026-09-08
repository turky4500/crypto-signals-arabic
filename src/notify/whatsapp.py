"""عميل WhatsApp مع Retry محدود + تسجيل الأخطاء. البيانات تأتي من Environment Variables."""
from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)


class WhatsAppError(RuntimeError):
    pass


class WhatsAppClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        receiver: str,
        timeout: float = 25.0,
        max_retries: int = 3,
        backoff: float = 3.0,
    ):
        if not api_url or not token or not receiver:
            raise WhatsAppError("إعدادات WhatsApp ناقصة (API URL / Token / Receiver)")
        self.api_url = api_url
        self.token = token
        self.receiver = receiver
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.session = requests.Session()

    def send(self, message: str) -> dict:
        """إرسال رسالة مع Retry محدود — يمنع الإرسال المتكرر لنفس الفشل."""
        payload = {"to": self.receiver, "message": message}
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        last_error = "unknown"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                body = resp.text[:500]
                if resp.status_code < 300:
                    logger.info("WhatsApp sent (attempt %s): HTTP %s %s", attempt, resp.status_code, body)
                    return {"ok": True, "attempts": attempt, "code": resp.status_code, "body": body}
                last_error = f"HTTP {resp.status_code}: {body}"
                logger.warning("WhatsApp HTTP %s (attempt %s)", resp.status_code, attempt)
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("WhatsApp request failed (attempt %s): %s", attempt, exc)
            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)
        logger.error("WhatsApp فشل بعد %s محاولة: %s", self.max_retries, last_error)
        return {"ok": False, "attempts": self.max_retries, "error": last_error}