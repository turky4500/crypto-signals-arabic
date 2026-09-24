"""عميل Telegram (Bot API) — قناة احتياطية تُستخدم عند تعطل واتساب.

يرسل عبر sendMessage الرسمي إلى chat_id مسجّل مسبقًا، بتصميم مطابق لعميل
WhatsApp. البيانات تأتي من Environment Variables.

منع التكرار — طبقتان:
1) دفتر المحتوى (dedup_file): نفس chat_id + نفس النص خلال نافذة زمنية → لا
   يُرسل أصلًا ويُرجَع ok=True مع deduped=True (الرسالة وصلت سابقًا فعلًا أو
   على الأرجح؛ ومنع الأعاد يقتل أي نسخة ثانية مهما كان مصدرها).
2) شبكة حذرة: لا إعادة إلا لما نتيقن أنه لم يصل إلى Telegram إطلاقًا (HTTP
   مرفوض / فشل TCP قبل الإرسال). أي غموض (قد تكون الرسالة وصلت ثم انقطعت
   الاستجابة) يُرجَع AMBIGUOUS دون إعادة — فلا تكرار للمستلم أبدًا.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import time

import requests

logger = logging.getLogger(__name__)

DEDUP_CAP = 3000
DEDUP_WINDOW_MINUTES = 90.0


class TelegramError(RuntimeError):
    pass


def delivered(res: dict) -> bool:
    """هل الرسالة مُسلّمة (ولن تُعاد)؟

    النجاح مؤكد (ok)، أو تسليم غامض (قد وصلت فعلًا لكن انقطعت الاستجابة) — في
    الحالتين لا نُعيد الإرسال مستقبلًا حتى لا يظهر تكرار لدى المستلم. الفشل
    المؤكد فقط (لم تصل) لا يُعتبر مُسلّمًا فيُعاد.
    """
    return bool(res.get("ok")) or "AMBIGUOUS" in str(res.get("error") or "")


def _text_hash(chat_id: str, text: str) -> str:
    """بصمة النص مع وجهته — مفتاح منع التكرار على مستوى المحتوى."""
    return hashlib.sha256(f"{chat_id}\x00{text}".encode("utf-8")).hexdigest()


def _safe_retry(exc: BaseException) -> bool:
    """هل إعادة الإرسال آمنة (لم يصل الطلب إلى Telegram أصلًا)؟

    نسير في سلسلة الأسباب ونعتبرها آمنة فقط عندما نتيقن أن الطلب لم يُرسَل أي
    بايت: فشل DNS (gaierror)، رفض الاتصال (ConnectionRefusedError)، انتهاء مهلة
    الاتصال (ConnectTimeout)، أو NewConnectionError من urllib3.
    أي فشل آخر — انقطاع أثناء قراءة الاستجابة، اتصال مغلق بعد الإرسال، مهلة
    قراءة، ترميز مجزأ... — غامض: قد تكون الرسالة وصلت فعلًا، فلا نعيد أبدًا.
    """
    safe_types = (
        requests.exceptions.ConnectTimeout,
        ConnectionRefusedError,
        socket.gaierror,
    )
    cur = exc
    guard = 0
    while cur is not None and guard < 10:
        if isinstance(cur, safe_types):
            return True
        if type(cur).__name__ in ("NewConnectionError", "NameResolutionError"):
            return True
        cur = getattr(cur, "__cause__", None)
        guard += 1
    return False


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        timeout: float = 25.0,
        max_retries: int = 3,
        backoff: float = 3.0,
        dedup_file: str | None = None,
        dedup_window_minutes: float = DEDUP_WINDOW_MINUTES,
    ):
        if not token or not chat_id:
            raise TelegramError("إعدادات Telegram ناقصة (Token / Chat ID)")
        self.token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.dedup_file = dedup_file
        self.dedup_window_ms = float(dedup_window_minutes) * 60_000
        self._sent_cache: list | None = None
        self.session = requests.Session()

    def _method_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    # ------------------ دفتر المحتوى (منع التكرار) ------------------ #

    def _load_sent(self) -> list:
        if self._sent_cache is not None:
            return self._sent_cache
        entries: list = []
        if self.dedup_file:
            try:
                with open(self.dedup_file, encoding="utf-8") as fh:
                    entries = json.load(fh).get("sent") or []
            except (OSError, ValueError):
                entries = []
        self._sent_cache = entries
        return entries

    def _save_sent(self, entries: list) -> None:
        if not self.dedup_file:
            return
        try:
            path = os.path.abspath(self.dedup_file)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"sent": entries[-DEDUP_CAP:]}, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Telegram دفتر منع التكرار لم يُحفظ: %s", exc)

    def _record_sent(self, text: str) -> None:
        if not self.dedup_file:
            return
        entries = self._load_sent()
        entries.append({
            "ts": int(time.time() * 1000),
            "chat_id": self.chat_id,
            "text_hash": _text_hash(self.chat_id, text),
        })
        # نجمع الأحدث فقط لكل مفتاح (chat,text) — نحتفظ بآخر زمن
        seen: dict = {}
        for e in reversed(entries):
            key = (e.get("chat_id"), e.get("text_hash"))
            if key not in seen:
                seen[key] = e
        unique = list(reversed(list(seen.values())))
        self._sent_cache = unique
        self._save_sent(unique)

    def _is_duplicate(self, text: str) -> bool:
        """نفس الرسالة لنفس المحادثة أُرسلت خلال النافذة؟"""
        if not self.dedup_file:
            return False
        now = int(time.time() * 1000)
        chat = self.chat_id
        hsh = _text_hash(chat, text)
        for e in reversed(self._load_sent()):
            if e.get("chat_id") != chat or e.get("text_hash") != hsh:
                continue
            if now - (e.get("ts") or 0) <= self.dedup_window_ms:
                return True
            return False  # الإدخال أقدم من النافذة → المنع انتهى
        return False

    # ------------------------------ الإرسال ------------------------------ #

    def send(self, message: str) -> dict:
        """إرسال رسالة واحدة مؤكدة — بلا تكرار لدى المستلم إطلاقًا.

        القاعدة الجوهرية: لا نُرسل نفس الرسالة لنفس المحادثة مرتين خلال النافذة.
        - منع قبلي عبر دفتر المحتوى (نفس chat_id + نفس النص) → deduped=True.
        - HTTP 2xx مع ok:true → نجاح (وصلت مرة واحدة) وسُجّلت في الدفتر.
        - HTTP غير 2xx أو ok:false → لم تصل أصلًا → إعادة آمنة.
        - فشل TCP قبل الإرسال (DNS/رفض/مهلة اتصال) → إعادة آمنة.
        - أي غموض (مهلة قراءة، انقطاع أثناء قراءة الاستجابة...) → AMBIGUOUS
          بلا إعادة، وتسجيل الرسالة كمرسلة في الدفتر لمنع إعادة لاحقة.
        """
        if self._is_duplicate(message):
            logger.info("Telegram تخطّى إرسالًا مكرر المحتوى (dedup)")
            return {"ok": True, "deduped": True, "attempts": 0,
                    "error": "dedup: نفس الرسالة أُرسلت مؤخرًا لنفس المحادثة"}
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
                    ok_json = None  # لا جسم JSON (وسيط/اختبار) — نتعامل كنجاح مع 2xx
                if resp.status_code < 300 and ok_json is not False:
                    logger.info("Telegram sent (attempt %s): HTTP %s", attempt, resp.status_code)
                    self._record_sent(message)
                    return {"ok": True, "attempts": attempt, "code": resp.status_code, "body": body}
                # HTTP 2xx لكن ok:false (نادر) أو HTTP خطأ: لم تصل أصلًا → إعادة آمنة
                if resp.status_code < 300:
                    last_error = f"Telegram ok=false: {body}"
                    logger.warning("Telegram ok=false (attempt %s)", attempt)
                else:
                    last_error = f"HTTP {resp.status_code}: {body}"
                    logger.warning("Telegram HTTP %s (attempt %s)", resp.status_code, attempt)
            except requests.exceptions.Timeout as exc:
                if _safe_retry(exc):
                    last_error = str(exc)
                    logger.warning("Telegram connect timeout (attempt %s)", attempt)
                else:
                    self._record_sent(message)  # قد تكون وصلت فعلًا
                    logger.error("Telegram read timeout — غامض، لا إعادة لتجنّب التكرار")
                    return {"ok": False, "attempts": attempt,
                            "error": "AMBIGUOUS_READ_TIMEOUT_NO_RETRY: قد تكون الرسالة وصلت"}
            except requests.RequestException as exc:
                if _safe_retry(exc):
                    last_error = str(exc)
                    logger.warning("Telegram فشل قبل الإرسال (attempt %s): %s", attempt, exc)
                else:
                    self._record_sent(message)  # قد تكون وصلت فعلًا
                    logger.error("Telegram غموض — لا إعادة لتجنّب التكرار: %s", exc)
                    return {"ok": False, "attempts": attempt,
                            "error": f"AMBIGUOUS_NO_RETRY: قد تكون الرسالة وصلت ({type(exc).__name__})"}
            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)
        logger.error("Telegram فشل بعد %s محاولة: %s", self.max_retries, last_error)
        return {"ok": False, "attempts": self.max_retries, "error": last_error}

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