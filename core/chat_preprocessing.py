"""Chat preprocessing — language detection, corrections, teaching, datetime.

Extracted from chat_handler.py (v3.3 refactor).
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

log = logging.getLogger("hivemind")

try:
    from core.translation_proxy import detect_language
    _TRANSLATION_AVAILABLE = True
except ImportError:
    _TRANSLATION_AVAILABLE = False
    def detect_language(t): return "fi"


@dataclass
class PreprocessResult:
    """Result of preprocessing a chat message."""
    message: str
    original_message: str
    language: str
    handled: bool = False
    response: str = ""
    method: str = ""
    translation_used: bool = False
    fi_en_result: object = None
    en_message: str = ""
    use_en_prompts: bool = False


class ChatPreprocessor:
    """Preprocessing pipeline: language, corrections, teaching, datetime."""

    # Score-based correction detection keywords
    _STRONG_CORRECTION = {"väärin", "wrong", "väärä", "virhe",
                          "korjaus", "tarkoitin"}
    _CORRECTION_PHRASES = {"ei vaan", "ei ole oikea", "väärä vastaus",
                           "oikea vastaus", "tarkoitin että"}
    _WEAK_CORRECTION = {"ei", "eikä"}

    # Datetime phrase sets
    _TIME_PHRASES = {"paljonko kello", "mikä kello", "kellonaika", "what time",
                     "current time", "monako kello", "paljon kello", "kerro kello"}
    _DATE_PHRASES = {"mikä päivä", "monesko päivä", "päivämäärä", "what date",
                     "what day", "mikä tänään", "tänään on"}

    def __init__(self, hive):
        self._hive = hive

    def detect_lang(self, message: str, language: str) -> str:
        """Detect language if 'auto'."""
        if language == "auto":
            return detect_language(message) if _TRANSLATION_AVAILABLE else "fi"
        return language

    async def detect_correction(self, message: str, detected_lang: str,
                                original_message: str, chat_t0: float) -> Optional[str]:
        """Detect user corrections. Returns response string or None."""
        hive = self._hive
        if not (getattr(hive, '_last_chat_message', None)
                and getattr(hive, '_last_chat_response', None)
                and hasattr(hive, 'consciousness') and hive.consciousness):
            return None

        msg_lower = message.lower()
        msg_words = [w.strip(".,!?;:\"'") for w in msg_lower.split()]
        msg_word_set = set(msg_words)

        corr_score = 0
        corr_score += 2 * len(msg_word_set & self._STRONG_CORRECTION)
        for p in self._CORRECTION_PHRASES:
            if p in msg_lower:
                corr_score += 3
        weak_hit = msg_word_set & self._WEAK_CORRECTION
        if weak_hit:
            if msg_words[0] in self._WEAK_CORRECTION:
                corr_score += 1
            if len(message) < 20:
                corr_score += 1

        if corr_score < 2 or len(message) <= 5:
            return None

        hive.consciousness.store_correction(
            query=hive._last_chat_message,
            bad_answer=hive._last_chat_response,
            good_answer=message,
            agent_id=getattr(hive, '_last_chat_agent_id', None) or "unknown")

        if getattr(hive, 'agent_levels', None) and getattr(hive, '_last_chat_agent_id', None):
            try:
                hive.agent_levels.record_response(
                    agent_id=hive._last_chat_agent_id,
                    agent_type="unknown",
                    was_correct=False, was_hallucination=False,
                    was_corrected=True)
            except Exception as e:
                log.debug("agent_levels correction record failed: %s", e)

        if getattr(hive, 'monitor', None):
            await hive.monitor.system("📝 Korjaus tallennettu — opin virheestä!")
        await hive._notify_ws("correction_stored", {
            "query": hive._last_chat_message[:100],
            "good_answer": message[:100],
        })

        response = "Kiitos korjauksesta! Opin virheestä ja muistan tämän jatkossa."
        hive._last_chat_message = message
        hive._last_chat_response = response
        hive._last_chat_method = ""
        _corr_agent = getattr(hive, '_last_chat_agent_id', None) or "user"
        hive.metrics.log_chat(
            query=original_message, method="correction",
            agent_id=_corr_agent, model_used="none",
            response_time_ms=(time.perf_counter() - chat_t0) * 1000,
            route="correction", language=detected_lang)
        return response

    async def detect_teaching(self, message: str, detected_lang: str,
                              original_message: str, chat_t0: float) -> Optional[str]:
        """Detect user teaching after active_learning. Returns response or None."""
        hive = self._hive
        if not (getattr(hive, '_last_chat_method', None) == "active_learning"
                and hasattr(hive, 'consciousness') and hive.consciousness
                and hive.consciousness.detect_user_teaching(
                    message, hive._last_chat_method)):
            return None

        hive.consciousness.learn_from_user(message, hive._last_chat_message)
        if getattr(hive, 'monitor', None):
            await hive.monitor.system(f"🎓 Opittu käyttäjältä: {message[:60]}")
        await hive._notify_ws("user_teaching", {
            "query": hive._last_chat_message[:100],
            "teaching": message[:100],
        })
        response = f"Kiitos! Opin juuri: {message[:100]}. Muistan tämän jatkossa."
        hive._last_chat_message = message
        hive._last_chat_response = response
        hive._last_chat_method = ""
        hive.metrics.log_chat(
            query=original_message, method="user_teaching",
            agent_id="user", model_used="none", confidence=0.9,
            response_time_ms=(time.perf_counter() - chat_t0) * 1000,
            route="user_teaching", language=detected_lang)
        return response

    async def detect_datetime(self, message: str, detected_lang: str,
                              original_message: str, chat_t0: float) -> Optional[str]:
        """Handle datetime queries directly. Returns response or None."""
        hive = self._hive
        msg_l = message.lower()
        is_time_q = any(w in msg_l for w in self._TIME_PHRASES)
        is_date_q = any(w in msg_l for w in self._DATE_PHRASES)

        if not (is_time_q or is_date_q):
            return None

        dt_now = datetime.now()
        weekdays_fi = ["maanantai", "tiistai", "keskiviikko", "torstai",
                       "perjantai", "lauantai", "sunnuntai"]
        weekday_fi = weekdays_fi[dt_now.weekday()]
        time_str = dt_now.strftime("%H.%M")
        date_str = f"{dt_now.day}.{dt_now.month}.{dt_now.year}"

        if is_time_q and is_date_q:
            response = f"Tänään on {weekday_fi} {date_str}, kello on {time_str}."
        elif is_time_q:
            response = f"Kello on {time_str}."
        else:
            response = f"Tänään on {weekday_fi} {date_str}."

        hive._last_chat_message = message
        hive._last_chat_response = response
        hive._last_chat_method = "datetime_direct"
        hive.metrics.log_chat(
            query=original_message, method="datetime_direct",
            agent_id="system", model_used="none", confidence=1.0,
            response_time_ms=(time.perf_counter() - chat_t0) * 1000,
            route="datetime_direct", language=detected_lang)
        if getattr(hive, 'monitor', None):
            await hive.monitor.system(f"🕐 Aikakysely: {response}")
        await hive._notify_ws("chat_response", {
            "message": message, "response": response,
            "language": detected_lang, "method": "datetime_direct"
        })
        return response

    async def translate_if_needed(self, message: str, detected_lang: str):
        """Translate FI→EN if needed. Returns (en_message, translation_used, fi_en_result)."""
        hive = self._hive
        if detected_lang == "fi" and getattr(hive, 'translation_proxy', None):
            try:
                fi_en_result = hive.translation_proxy.fi_to_en(message, force_opus=True)
                if fi_en_result.coverage >= 0.5 and fi_en_result.method != "passthrough":
                    if getattr(hive, 'monitor', None):
                        await hive.monitor.system(
                            f"🔄 FI→EN ({fi_en_result.method}, "
                            f"{fi_en_result.latency_ms:.1f}ms, "
                            f"{fi_en_result.coverage:.0%}): {fi_en_result.text[:80]}")
                    return fi_en_result.text, True, fi_en_result
                return message, False, None
            except Exception as e:
                log.error(f"FI->EN translation failed: {type(e).__name__}: {e}")
                return message, False, None
        return message, False, None
