"""Opus-MT adapter — extracted from memory_engine.py v1.18.0.

Provides FI↔EN translation via Helsinki-NLP/opus-mt models
with proxy fallback to translation_proxy.
"""

import logging

log = logging.getLogger(__name__)


class OpusMTAdapter:
    def __init__(self):
        self._proxy = None
        self._direct_fi_en = None
        self._direct_en_fi = None
        self._available = None

    def set_proxy(self, translation_proxy):
        self._proxy = translation_proxy
        self._available = True

    @property
    def available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from transformers import MarianMTModel, MarianTokenizer  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
        return self._available

    def fi_to_en(self, text):
        if not text or not text.strip():
            return text
        if self._proxy and hasattr(self._proxy, 'fi_to_en'):
            try:
                result = self._proxy.fi_to_en(text, force_opus=True)
                if hasattr(result, 'text'):
                    return result.text
                if isinstance(result, tuple):
                    return result[0]
                return str(result)
            except Exception:
                pass
        return self._direct_translate(text, "fi", "en")

    def en_to_fi(self, text):
        if not text or not text.strip():
            return text
        if self._proxy and hasattr(self._proxy, 'en_to_fi'):
            try:
                result = self._proxy.en_to_fi(text, force_opus=True)
                if hasattr(result, 'text'):
                    return result.text
                if isinstance(result, tuple):
                    return result[0]
                return str(result)
            except Exception:
                pass
        return self._direct_translate(text, "en", "fi")

    def _direct_translate(self, text, src, tgt):
        try:
            from transformers import MarianMTModel, MarianTokenizer
            model_name = f"Helsinki-NLP/opus-mt-{src}-{tgt}"
            if src == "fi" and tgt == "en":
                if self._direct_fi_en is None:
                    tok = MarianTokenizer.from_pretrained(model_name)
                    mdl = MarianMTModel.from_pretrained(model_name)
                    self._direct_fi_en = (tok, mdl)
                tok, mdl = self._direct_fi_en
            else:
                if self._direct_en_fi is None:
                    tok = MarianTokenizer.from_pretrained(model_name)
                    mdl = MarianMTModel.from_pretrained(model_name)
                    self._direct_en_fi = (tok, mdl)
                tok, mdl = self._direct_en_fi
            inputs = tok(text, return_tensors="pt", truncation=True, max_length=512)
            outputs = mdl.generate(**inputs, max_length=512)
            return tok.decode(outputs[0], skip_special_tokens=True)
        except Exception as e:
            log.error(f"Direct translate {src}->{tgt}: {e}")
            return text
