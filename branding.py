# -*- coding: utf-8 -*-
"""
branding.py — logo yerleşimi.

Görseller `assets/` klasöründen okunur; dosya yoksa uygulama sessizce logosuz
açılır. Yol `APP_LOGO` / `APP_ICON` ortam değişkenleriyle de verilebilir.

  assets/logo.png   tam logo (yatay)      → kenar çubuğu tepesi + başlık yanı
  assets/icon.png   kare işaret (altıgen) → favicon + kapalı kenar çubuğu

Her iki logo da tıklanabilir ve MET4METAB_URL adresini yeni sekmede açar.

Koyu tema notu: logonun lacivertİ (#0d2d43) Streamlit'in koyu arka planında
neredeyse görünmez olur. Resmî bir proje logosunun renkleri değiştirilmez;
bunun yerine koyu temada logo açık renkli bir zemin üzerine alınır.
"""

from __future__ import annotations

import os
from base64 import b64encode
from pathlib import Path

import streamlit as st

MET4METAB_URL = "https://www.met4metab.ptb.de/introduction"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"

MIME = {
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
}
RASTER = {".png", ".jpg", ".jpeg", ".webp"}

LOGO_NAMES = ["logo.svg", "logo.png", "logo.jpg", "logo.jpeg", "logo.webp",
              "met4metab.svg", "met4metab.png", "met4metab.jpg"]
ICON_NAMES = ["icon.png", "icon.svg", "icon.jpg", "icon.webp", "favicon.png"]

PLATE_BG = "#ffffff"  # koyu temada logonun altına konan zemin


def _find(names, env_var) -> Path | None:
    env = os.environ.get(env_var)
    if env and Path(env).is_file():
        return Path(env)
    for name in names:
        p = ASSETS_DIR / name
        if p.is_file():
            return p
    return None


def find_logo() -> Path | None:
    hit = _find(LOGO_NAMES, "APP_LOGO")
    if hit is not None:
        return hit
    if ASSETS_DIR.is_dir():  # adı ne olursa olsun ilk görsel
        for p in sorted(ASSETS_DIR.iterdir()):
            if p.suffix.lower() in MIME and p.name not in ICON_NAMES:
                return p
    return None


def find_icon() -> Path | None:
    return _find(ICON_NAMES, "APP_ICON")


def page_icon(logo: Path | None = None, fallback: str = "🧪"):
    """Sekme simgesi: önce kare işaret, sonra raster logo, sonra emoji."""
    icon = find_icon()
    if icon is not None and icon.suffix.lower() in RASTER:
        return str(icon)
    if logo is not None and logo.suffix.lower() in RASTER:
        return str(logo)
    return fallback


def is_dark() -> bool:
    try:
        return str(st.context.theme.type).lower() == "dark"
    except Exception:
        return False


def data_uri(path: Path, max_height: int | None = None) -> str:
    """Gömülü görsel. Raster dosyalar gösterim boyutuna indirgenir ve
    önbelleklenir — betik her etkileşimde yeniden çalıştığı için tam
    çözünürlüklü logoyu her seferinde göndermenin anlamı yok."""
    return _data_uri_cached(str(path), path.stat().st_mtime, max_height)


@st.cache_data(show_spinner=False)
def _data_uri_cached(path_str: str, _mtime: float, max_height: int | None) -> str:
    path = Path(path_str)
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    if suffix in RASTER and max_height:
        try:
            import io

            from PIL import Image

            im = Image.open(io.BytesIO(raw))
            target = int(max_height) * 2  # yüksek yoğunluklu ekranlar için 2×
            if im.height > target:
                w = max(1, round(im.width * target / im.height))
                im = im.convert("RGBA").resize((w, target), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
                raw, suffix = buf.getvalue(), ".png"
        except Exception:
            pass  # Pillow yoksa ya da dosya bozuksa orijinali göm
    mime = MIME.get(suffix, "application/octet-stream")
    return f"data:{mime};base64,{b64encode(raw).decode('ascii')}"


def link_html(logo: Path, url: str = MET4METAB_URL, height: int = 56,
              alt: str = "Met4Metab", plate: bool | None = None) -> str:
    if plate is None:
        plate = is_dark()
    style = (f"background:{PLATE_BG};padding:8px 12px;border-radius:10px;"
             "display:inline-block;line-height:0;") if plate else "line-height:0;"
    return (
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'title="{alt}" style="{style}">'
        f'<img src="{data_uri(logo, height)}" alt="{alt}" '
        f'style="height:{height}px;width:auto;display:block;">'
        f"</a>"
    )


def _plate_css() -> None:
    """Koyu temada st.logo'nun kendi yerleşimini açık zemine alır."""
    st.markdown(
        f"""<style>
        [data-testid="stLogo"] {{
            background:{PLATE_BG}; padding:4px 8px; border-radius:8px;
        }}
        </style>""",
        unsafe_allow_html=True,
    )


def header(title: str, caption: str = "", logo: Path | None = None,
           url: str = MET4METAB_URL, height: int = 56) -> None:
    """Logo + başlık. Logo yoksa yalnızca başlık yazılır."""
    if logo is None:
        logo = find_logo()
    if logo is None:
        st.title(title)
        if caption:
            st.caption(caption)
        return

    icon = find_icon()
    dark = is_dark()

    # kenar çubuğu logosu — st.logo yoksa (Streamlit < 1.35) HTML'e düşer
    try:
        st.logo(str(logo), link=url, size="large",
                icon_image=str(icon) if icon else None)
        if dark:
            _plate_css()
    except (AttributeError, TypeError):
        with st.sidebar:
            st.markdown(link_html(logo, url, 40, plate=dark),
                        unsafe_allow_html=True)

    col_logo, col_text = st.columns([2, 6], vertical_alignment="center")
    with col_logo:
        st.markdown(link_html(logo, url, height, plate=dark),
                    unsafe_allow_html=True)
    with col_text:
        st.title(title)
        if caption:
            st.caption(caption)
