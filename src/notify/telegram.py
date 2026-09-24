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


def delivered(res: dict) -> bool:
    """هل الرسالة مُسلّمة (ولن تُعاد)؟

    النجاح مؤكد (ok) أو التسليم غامض (ReadTimeout: قد تكون وصلت فعلًا) — في
    الحالتين لا نُعيد الإرسال مستقبلًا حتى لا يظهر تكرار لدى المستلم. الفشل
    المؤكد فقط (لم تصل) لا يُعتبر مُسلّمًا فيُعاد.
    """
    return bool(res.get("ok")) or "AMBIGUOUS" in str(res.get("error") or "")


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
        """إرسال رسالة نصية إلى chat_id — سياسة «إرسال واحد مؤكد» بلا تكرار.

        تلغرام لا يوفّر مفتاح تكرار (idempotency)، فالإعادة العمياء عند تأخر
        الاستجابة تُوصّل نفس الرسالة مرتين للمستلم. القاعدة:
        - HTTP 2xx مع ok:true → نجاح (وصلت مرة واحدة).
        - HTTP غير 2xx أو ok:false → لم تصل أصلًا → إعادة آمنة.
        - ConnectTimeout / ConnectionError → الطلب لم يصل → إعادة آمنة.
        - ReadTimeout → غامض (الرسالة قد تكون وصلت) → لا إعادة أبدًا، ونُبلّغ
          AMBIGUOUS ليُثبّت المرسل الشمعة كمرسلة ويُحبط تكرارًا محتملاً.
        """
        payload = {"chat_id": self.chat_id, "text": message}
        last_error = "unknown"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.post(
                    self._method_url("sendMessage"),
                    json=payload,
                    timeout=(10.0, self.timeout),  # (connect, read)
                )
                body = resp.text[:500]
                ok_json = None
                try:
                    ok_json = bool(resp.json().get("ok", False))
                except Exception:
                    ok_json = None  # لا جسم JSON (مثل وسيط/اختبار) — نتعامل كنجاح مع 2xx
                if resp.status_code < 300 and ok_json is not False:
                    logger.info("Telegram sent (attempt %s): HTTP %s", attempt, resp.status_code)
                    return {"ok": True, "attempts": attempt, "code": resp.status_code, "body": body}
                # HTTP 2xx لكن ok:false (نادر) أو HTTP خطأ: لم تصل أصلًا → إعادة آمنة
                if resp.status_code < 300:
                    last_error = f"Telegram ok=false: {body}"
                    logger.warning("Telegram ok=false (attempt %s)", attempt)
                else:
                    last_error = f"HTTP {resp.status_code}: {body}"
                    logger.warning("Telegram HTTP %s (attempt %s)", resp.status_code, attempt)
            except requests.exceptions.Timeout as exc:
                # ConnectTimeout: الطلب لم يصل → إعادة آمنة.
                # ReadTimeout: غامض (قد تكون الرسالة وصلت) → لا إعادة لتجنّب التكرار.
                if isinstance(exc, requests.exceptions.ConnectTimeout):
                    last_error = str(exc)
                    logger.warning("Telegram connect timeout (attempt %s)", attempt)
                else:
                    logger.error("Telegram read timeout — غامض، لا إعادة لتجنّب التكرار")
                    return {"ok": False, "attempts": attempt,
                            "error": "AMBIGUOUS_READ_TIMEOUT_NO_RETRY: قد تكون الرسالة وصلت"}
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("Telegram request failed (attempt %s): %s", attempt, exc)
            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)
        logger.error("Telegram فشل بعد %s محاولة: %s", self.max_retries, last_error)
        return {"ok": False, "attempts": self.max_retries, "error": last_error}