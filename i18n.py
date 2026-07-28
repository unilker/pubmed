# -*- coding: utf-8 -*-
"""
i18n.py — çok dilli arayüz.

Metinler `locales/<kod>.json` içinde düz anahtar → dize eşlemesi olarak durur.
Yeni bir dil eklemek için `locales/xx.json` dosyasını kopyalayıp çevirin ve
LANGUAGES sözlüğüne ekleyin; Python tarafında değişiklik gerekmez.

Dil seçimi sırası:  ?lang=de adres parametresi → oturum → APP_LANG → varsayılan.
Adres parametresi güncellendiği için seçilen dil, bağlantı paylaşıldığında
karşı tarafta da açılır.

Eksik anahtar İngilizceye, o da yoksa anahtarın kendisine düşer — çeviri
tamamlanmamışken bile uygulama çalışır.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

LANGUAGES = {
    "tr": "Türkçe",
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
}
FALLBACK = "en"
DEFAULT = "tr"


@st.cache_data(show_spinner=False)
def load(lang: str) -> dict:
    path = LOCALES_DIR / f"{lang}.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _query_lang() -> str | None:
    try:
        value = st.query_params.get("lang")
    except Exception:
        return None
    return str(value).lower() if value else None


def current() -> str:
    if "lang" not in st.session_state:
        candidate = (_query_lang() or os.environ.get("APP_LANG") or DEFAULT).lower()
        st.session_state["lang"] = candidate if candidate in LANGUAGES else DEFAULT
    return st.session_state["lang"]


def set_lang(lang: str) -> None:
    if lang not in LANGUAGES:
        return
    st.session_state["lang"] = lang
    try:
        st.query_params["lang"] = lang
    except Exception:
        pass


def t(key: str, **fmt) -> str:
    """Çeviriyi getirir; {ad} yer tutucuları fmt ile doldurulur."""
    lang = current()
    text = load(lang).get(key) or load(FALLBACK).get(key) or key
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return text  # çeviride bozuk yer tutucu varsa ham metni göster


def selector(container=None):
    """Dil seçici. Seçim değişirse betiği yeniden çalıştırır."""
    target = container or st
    codes = list(LANGUAGES)
    active = current()
    chosen = target.selectbox(
        t("lang.label"), codes,
        index=codes.index(active),
        format_func=lambda code: LANGUAGES[code],
        key="lang_widget",
    )
    if chosen != active:
        set_lang(chosen)
        st.rerun()
    return chosen


def missing_keys(lang: str) -> list[str]:
    """Çeviri boşluklarını listeler — test_smoke.py bunu kullanır."""
    return sorted(set(load(FALLBACK)) - set(load(lang)))
