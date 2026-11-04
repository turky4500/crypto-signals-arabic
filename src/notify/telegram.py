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

import contextlib
import hashlib
import json
import logging
import os
import socket
import time

import requests

try:  # قفل الملفات (Linux/macOS)؛ غير متاح على Windows
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

logger = logging.getLogger(__name__)

DEDUP_CAP = 3000
DEDUP_WINDOW_MINUTES = 90.0
PERMANENT_HTTP = (400, 401, 403, 404)
MAX_RETRY_AFTER = 30.0


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

    # ------------------ دفتر الإرسال (منع التكرار) ------------------ #
    #
    # الدفتر ملف JSON دائم داخل data/ (يُحفظ في git بين الجولات). كل إدخال:
    #   {ts, chat_id, text_hash, key?, state}
    # state: "pending"   → حُجز قبل الاتصال بـ Telegram (كتابة مسبقة WAL)
    #        "sent"      → أكّد Telegram القبول (ok:true)
    #        "ambiguous" → انقطع الاتصال بعد الإرسال: قد تكون وصلت
    # (الإدخالات القديمة بلا state تُعامَل كـ "sent").
    # الإدخالات pending/ambiguous/sent كلها تمنع إعادة الإرسال (الأولوية لعدم
    # التكرار: لا تُرسَل مرتين). فشل مؤكد (لم تصل) يحذف الحجز فتُعاد لاحقًا.
    # key = معرّف الإشارة الفريد (العملة|المؤشر|النوع|وقت الشمعة) ولا يُنهي
    # صلاحيته الزمن؛ إشارة جديدة على شمعة جديدة لها key مختلف فتُرسل طبيعيًا.
    # الحجز والفحص يتمّان تحت قفل ملف (flock) فلا تُرسل عمليتان نفس الإشارة.

    @contextlib.contextmanager
    def _ledger_lock(self):
        if not self.dedup_file or fcntl is None:
            yield
            return
        path = os.path.abspath(self.dedup_file) + ".lock"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _load_sent(self, force: bool = False) -> list:
        if self._sent_cache is not None and not force:
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
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Telegram دفتر منع التكرار لم يُحفظ: %s", exc)

    def _find_blocker(self, entries: list, hsh: str, key: str | None) -> dict | None:
        now = int(time.time() * 1000)
        chat = self.chat_id
        for e in reversed(entries):
            if e.get("chat_id") != chat:
                continue
            if key and e.get("key") == key:
                return e  # نفس الإشارة بالضبط — بلا نافذة زمنية
            if e.get("text_hash") == hsh and now - (e.get("ts") or 0) <= self.dedup_window_ms:
                return e  # نفس النص خلال النافذة
        return None

    def _is_duplicate(self, text: str, key: str | None = None) -> bool:
        if not self.dedup_file:
            return False
        with self._ledger_lock():
            return self._find_blocker(
                self._load_sent(force=True), _text_hash(self.chat_id, text), key
            ) is not None

    def _reserve(self, text: str, key: str | None) -> bool:
        """فحص + حجز ذريّان (كتابة مسبقة). False = مكرر فلا يُرسَل."""
        if not self.dedup_file:
            return True
        hsh = _text_hash(self.chat_id, text)
        with self._ledger_lock():
            entries = self._load_sent(force=True)
            if self._find_blocker(entries, hsh, key) is not None:
                return False
            entries.append({"ts": int(time.time() * 1000), "chat_id": self.chat_id,
                            "text_hash": hsh, "key": key, "state": "pending"})
            self._sent_cache = entries
            self._save_sent(entries)
            return True

    def _finish(self, text: str, key: str | None, state: str | None) -> None:
        """state: "sent" | "ambiguous" | None (فشل مؤكد → حذف الحجز)."""
        if not self.dedup_file:
            return
        hsh = _text_hash(self.chat_id, text)
        with self._ledger_lock():
            entries = self._load_sent(force=True)
            idx = None
            for i in range(len(entries) - 1, -1, -1):
                e = entries[i]
                if (e.get("chat_id") == self.chat_id and e.get("text_hash") == hsh
                        and e.get("key") == key and e.get("state") == "pending"):
                    idx = i
                    break
            if state is None:
                if idx is not None:
                    entries.pop(idx)
            elif idx is not None:
                entries[idx]["state"] = state
                entries[idx]["ts"] = int(time.time() * 1000)
            else:
                entries.append({"ts": int(time.time() * 1000), "chat_id": self.chat_id,
                                "text_hash": hsh, "key": key, "state": state})
            self._sent_cache = entries
            self._save_sent(entries)

    def _record_sent(self, text: str, key: str | None = None) -> None:
        self._finish(text, key, "sent")

    # ------------------------------ الإرسال ------------------------------ #

    def send(self, message: str, dedup_key: str | None = None) -> dict:
        """إرسال رسالة واحدة مؤكدة — بلا تكرار لدى المستلم قدر الإمكان.

        dedup_key: معرّف فريد للإشارة؛ نفس المفتاح لا يُرسل مرتين أبدًا (حتى لو
        تغيّر النص أو مرّ وقت طويل). بدونه يُعتمد على بصمة النص خلال النافذة.

        - فحص + حجز ذريّان قبل الاتصال → لا يرسل عمليتان نفس الإشارة.
        - HTTP 2xx مع ok:true → نجاح وتسجَّل "sent".
        - 400/401/403/404 → خطأ دائم (توكن/صلاحيات/تنسيق): بلا إعادة، يُحذف الحجز.
        - 429 → ننتظر retry_after (حتى 30ث) ثم نعيد؛ أكثر من ذلك يُعدّ فشلًا مؤكدًا.
        - 5xx / ok:false / فشل قبل الإرسال (DNS، رفض اتصال، مهلة اتصال) → لم تصل
          → إعادة آمنة.
        - أي غموض (مهلة قراءة، انقطاع بعد الإرسال) → AMBIGUOUS بلا إعادة وتسجَّل
          "ambiguous".

        حدّ الضمان: إن انقطع التنفيذ (قتل العملية/انقطاع الجهاز) بعد قبول Telegram
        للرسالة وقبل تسجيل "sent" يبقى الحجز "pending" فيمنع إعادة الإرسال في
        الجولات التالية (ضمان «مرة واحدة على الأكثر»): قد تضيع إشارة نادرًا لكنها
        لا تتكرر. لا يمكن لأي نظام يعتمد Bot API (بلا معرّف رسالة من جهة العميل)
        ضمان «مرة واحدة بالضبط» في هذه الحالة.
        """
        if not self._reserve(message, dedup_key):
            logger.info("Telegram تخطّى إرسالًا مكررًا (dedup)")
            return {"ok": True, "deduped": True, "attempts": 0,
                    "error": "dedup: نفس الإشارة/الرسالة أُرسلت من قبل"}
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
                parsed = None
                try:
                    parsed = resp.json()
                    ok_json = bool(parsed.get("ok", False))
                except Exception:
                    ok_json = None  # لا جسم JSON (وسيط/اختبار) — نتعامل كنجاح مع 2xx
                if resp.status_code < 300 and ok_json is not False:
                    logger.info("Telegram sent (attempt %s): HTTP %s", attempt, resp.status_code)
                    self._finish(message, dedup_key, "sent")
                    return {"ok": True, "attempts": attempt, "code": resp.status_code, "body": body}
                if resp.status_code < 300:
                    last_error = f"Telegram ok=false: {body}"
                    logger.warning("Telegram ok=false (attempt %s)", attempt)
                else:
                    last_error = f"HTTP {resp.status_code}: {body}"
                    logger.warning("Telegram HTTP %s (attempt %s)", resp.status_code, attempt)
                    if resp.status_code in PERMANENT_HTTP:
                        break  # توكن/صلاحيات/تنسيق: الإعادة لا تفيد
                    if resp.status_code == 429:
                        wait = 0.0
                        try:
                            wait = float(((parsed or {}).get("parameters") or {}).get("retry_after") or 0)
                        except (TypeError, ValueError, AttributeError):
                            wait = 0.0
                        if wait > MAX_RETRY_AFTER:
                            break  # انتظار طويل: نفشل بوضوح بدل حجز الجولة
                        if wait and attempt < self.max_retries:
                            time.sleep(wait + 0.5)
                            continue
            except requests.exceptions.Timeout as exc:
                if _safe_retry(exc):
                    last_error = str(exc)
                    logger.warning("Telegram connect timeout (attempt %s)", attempt)
                else:
                    self._finish(message, dedup_key, "ambiguous")  # قد تكون وصلت فعلًا
                    logger.error("Telegram read timeout — غامض، لا إعادة لتجنّب التكرار")
                    return {"ok": False, "attempts": attempt,
                            "error": "AMBIGUOUS_READ_TIMEOUT_NO_RETRY: قد تكون الرسالة وصلت"}
            except requests.RequestException as exc:
                if _safe_retry(exc):
                    last_error = str(exc)
                    logger.warning("Telegram فشل قبل الإرسال (attempt %s): %s", attempt, exc)
                else:
                    self._finish(message, dedup_key, "ambiguous")  # قد تكون وصلت فعلًا
                    logger.error("Telegram غموض — لا إعادة لتجنّب التكرار: %s", exc)
                    return {"ok": False, "attempts": attempt,
                            "error": f"AMBIGUOUS_NO_RETRY: قد تكون الرسالة وصلت ({type(exc).__name__})"}
            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)
        logger.error("Telegram فشل نهائيًا: %s", last_error)
        self._finish(message, dedup_key, None)  # فشل مؤكد: لم تصل → أفرج عن الحجز
        return {"ok": False, "attempts": attempt, "error": last_error}

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