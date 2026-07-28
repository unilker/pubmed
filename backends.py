# -*- coding: utf-8 -*-
"""
backends.py — literatür sayım arka uçları.

İki kaynak desteklenir:
  * PubMed      — NCBI E-utilities (esearch), anahtarsız 3 istek/s, anahtarla 10 istek/s
  * Europe PMC  — EBI Articles RESTful API, anahtar/kayıt gerektirmez

Bu modül Streamlit'e bağımlı değildir; ayrı ayrı test edilebilir ve
başka bir arayüzden (CLI, notebook) de kullanılabilir.
"""

from __future__ import annotations

import datetime as _dt
import random
import threading
import time

import requests

EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# --- matris terimleri: arka uçtan bağımsız, düz kelime listeleri --------------
MATRIX_TERMS = {
    "serum":  ["serum", "plasma"],
    "urine":  ["urine", "urinary"],
    "saliva": ["saliva", "salivary"],
    "feces":  ["feces", "faeces", "fecal", "stool"],
    "csf":    ["cerebrospinal fluid", "CSF"],
}

# Etiketler burada değil, locales/<dil>.json içinde ("matrix.serum" vb.):
# bu modül arayüzden ve dilden bağımsız kalır.
MATRIX_KEYS = list(MATRIX_TERMS)

# Bağlam terimleri — orijinal betikteki hâliyle korundu (metabolite* joker)
CONTEXT_TERMS = ["metabolomics", "metabolite*"]


# ----------------------------------------------------------------- hız sınırı
class RateLimiter:
    """En az `min_interval` saniyelik aralık + küçük rastgele sapma."""

    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            gap = self.min_interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap + random.uniform(0, 0.03))
            self._last = time.monotonic()


def _retry_after(resp, fallback: float) -> float:
    """Retry-After başlığını saniyeye çevirir; tarih biçimini yok sayar."""
    raw = resp.headers.get("Retry-After")
    try:
        return min(float(raw), 30.0)
    except (TypeError, ValueError):
        return fallback


def _fetch_count(url, params, limiter, extract, retries=4, timeout=30):
    """(sayı, hata) döndürür. 429/503'te Retry-After'a uyar, üstel geri çekilir."""
    last_err = "unknown error"
    for attempt in range(retries):
        limiter.wait()
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 503):
                last_err = f"HTTP {r.status_code} rate limit"
                time.sleep(_retry_after(r, 2.0 ** attempt))
                continue
            r.raise_for_status()
            return extract(r.json()), None
        except Exception as exc:  # ağ, JSON, biçim — hepsi aynı şekilde ele alınır
            last_err = str(exc)
            if attempt == retries - 1:
                return None, last_err
            time.sleep(2.0 ** attempt)
    return None, last_err


# ------------------------------------------------------------------ arka uçlar
class Backend:
    name = "?"
    limiter: RateLimiter

    def build_query(self, metabolite, matrix_key, synonyms, from_year=None, to_year=None) -> str:
        raise NotImplementedError

    def count(self, query):
        raise NotImplementedError

    def info(self) -> dict:
        """Arayüzün çevireceği, dilden bağımsız durum bilgisi."""
        return {"name": self.name}


class PubMedBackend(Backend):
    """NCBI E-utilities. Sorgu dili orijinal betikle birebir aynıdır."""

    name = "PubMed"

    def __init__(self, api_key=None, email=None, tool="met4metab-count", delay=None):
        self.api_key = api_key or None
        self.email = email or None
        self.tool = tool or "met4metab-count"
        if delay is None:
            delay = 0.11 if self.api_key else 0.40  # anahtarsızken 3/s sınırının altında kal
        self.limiter = RateLimiter(delay)

    def build_query(self, metabolite, matrix_key, synonyms, from_year=None, to_year=None):
        names = synonyms.get(metabolite, [metabolite])
        name_clause = " OR ".join(f'"{n}"[TIAB]' for n in names)
        matrix_clause = " OR ".join(f'"{t}"[TIAB]' for t in MATRIX_TERMS[matrix_key])
        ctx_clause = " OR ".join(f"{t}[TIAB]" for t in CONTEXT_TERMS)
        q = (f"({name_clause}) AND ({matrix_clause}) "
             f"AND ({ctx_clause}) AND humans[MH]")
        if from_year or to_year:
            lo = from_year or 1800
            hi = to_year or _dt.date.today().year
            q += f' AND ("{lo}"[PDAT] : "{hi}"[PDAT])'
        return q

    def count(self, query):
        params = {"db": "pubmed", "term": query, "retmode": "json",
                  "retmax": 0, "tool": self.tool}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["email"] = self.email
        return _fetch_count(EUTILS_URL, params, self.limiter,
                            lambda j: int(j["esearchresult"]["count"]))

    def info(self):
        return {"name": self.name, "keyed": bool(self.api_key)}


class EuropePMCBackend(Backend):
    """
    EBI Europe PMC. Anahtar gerektirmez.

    synonym: Europe PMC sorguları MeSH/UniProt eşanlamlılarıyla genişletebilir.
    Varsayılana güvenilmez — parametre her istekte açıkça gönderilir, çünkü
    açıkken sayım artık kendi SYNONYMS sözlüğüne izlenebilir olmaz.
    """

    name = "Europe PMC"

    def __init__(self, delay=0.20, synonym=False, restrict_medline=True,
                 human_filter='MESH_TERMS:"Humans"', email=None):
        self.limiter = RateLimiter(delay)
        self.synonym = bool(synonym)
        self.restrict_medline = bool(restrict_medline)
        self.human_filter = (human_filter or "").strip()
        self.email = email or None

    @staticmethod
    def _clause(field, terms):
        parts = []
        for t in terms:
            # joker içeren tek kelimeler tırnaksız yazılır, aksi hâlde eşleşmez
            if "*" in t and " " not in t:
                parts.append(f"{field}:{t}")
            else:
                parts.append(f'{field}:"{t}"')
        return "(" + " OR ".join(parts) + ")"

    def build_query(self, metabolite, matrix_key, synonyms, from_year=None, to_year=None):
        names = synonyms.get(metabolite, [metabolite])
        parts = [
            self._clause("TITLE_ABS", names),
            self._clause("TITLE_ABS", MATRIX_TERMS[matrix_key]),
            self._clause("TITLE_ABS", CONTEXT_TERMS),
        ]
        if self.human_filter:
            parts.append(f"({self.human_filter})")
        if self.restrict_medline:
            parts.append("(SRC:MED)")
        if from_year or to_year:
            lo = from_year or 1800
            hi = to_year or _dt.date.today().year
            parts.append(f"(PUB_YEAR:[{lo} TO {hi}])")
        return " AND ".join(parts)

    def count(self, query):
        params = {"query": query, "format": "json", "resultType": "idlist",
                  "pageSize": 1, "synonym": "true" if self.synonym else "false"}
        if self.email:
            params["email"] = self.email
        return _fetch_count(EPMC_URL, params, self.limiter,
                            lambda j: int(j["hitCount"]))

    def info(self):
        return {"name": self.name, "synonym": self.synonym,
                "medline": self.restrict_medline}


def guess_matrix_key(sheet_name: str) -> str:
    """Sayfa adından matris anahtarını tahmin eder."""
    low = str(sheet_name).lower()
    for needle, key in (
        ("serum", "serum"), ("plazma", "serum"), ("plasma", "serum"),
        ("idrar", "urine"), ("ıdrar", "urine"), ("urin", "urine"),
        ("salya", "saliva"), ("saliva", "saliva"), ("tükürük", "saliva"),
        ("gayta", "feces"), ("fec", "feces"), ("fae", "feces"),
        ("stool", "feces"), ("dışkı", "feces"),
        ("bos", "csf"), ("csf", "csf"), ("beyin", "csf"),
    ):
        if needle in low:
            return key
    return "serum"
