#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — PubMed yayın sayımı (metabolit × biyoakışkan) — Streamlit arayüzü

Çalıştırma:
    pip install -r requirements.txt
    streamlit run app.py

Orijinal CLI betiğinin (pubmed_count.py) tüm mantığı korunmuştur:
sorgu kurulumu, NCBI hız sınırları, yeniden deneme, sıralama ve loglama.
"""

import csv
import datetime as _dt
import io
import json
import re
import time

import openpyxl
import pandas as pd
import requests
import streamlit as st
from openpyxl.styles import Alignment, Font

# ----------------------------------------------------------------------------- sabitler
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

SHEET_COLS = {"metabolite_en": 3, "count": 5, "query": 8}
HEADER_ROW = 4  # veri satırları 5'ten başlar

MATRIX_SHEETS = [
    "1. Serum-Plazma", "2. Idrar", "3. Salya", "4. Gayta", "5. BOS",
    "1. Serum-Plasma", "2. Urine", "3. Saliva", "4. Feces", "5. CSF",
    "2. Idrar-Urine", "3. Salya-Saliva", "4. Gayta-Feces", "5. BOS-CSF",
]

SYNONYMS = {
    "Lactic acid":        ["lactic acid", "lactate"],
    "Glutamic acid":      ["glutamic acid", "glutamate"],
    "Aspartic acid":      ["aspartic acid", "aspartate"],
    "Uric acid":          ["uric acid", "urate"],
    "Citrate":            ["citrate", "citric acid"],
    "Succinate":          ["succinate", "succinic acid"],
    "Formate":            ["formate", "formic acid"],
    "Acetate":            ["acetate", "acetic acid"],
    "Pyruvate":           ["pyruvate", "pyruvic acid"],
    "Butyrate":           ["butyrate", "butyric acid"],
    "Propionate":         ["propionate", "propionic acid"],
    "Isovalerate":        ["isovalerate", "isovaleric acid"],
    "Isobutyrate":        ["isobutyrate", "isobutyric acid"],
    "Valerate":           ["valerate", "valeric acid"],
    "Oxalate":            ["oxalate", "oxalic acid"],
    "Hippurate":          ["hippurate", "hippuric acid"],
    "2-Oxoglutarate":     ["2-oxoglutarate", "alpha-ketoglutarate", "2-ketoglutarate"],
    "3-Hydroxybutyrate":  ["3-hydroxybutyrate", "beta-hydroxybutyrate", "3-hydroxybutyric acid"],
    "Methylmalonic acid": ["methylmalonic acid", "methylmalonate"],
    "Orotic acid":        ["orotic acid", "orotate"],
    "Pyroglutamate":      ["pyroglutamate", "pyroglutamic acid", "5-oxoproline"],
    "Trimethylamine N-oxide (TMAO)": ["trimethylamine N-oxide", "TMAO"],
    "GABA (4-aminobutyrate)": ["GABA", "gamma-aminobutyric acid", "4-aminobutyrate"],
    "Lysophosphatidylcholines (LPC)": ["lysophosphatidylcholine", "LPC"],
    "N-Acetylaspartate (NAA)": ["N-acetylaspartate", "NAA"],
    "5-Hydroxyindoleacetic acid": ["5-hydroxyindoleacetic acid", "5-HIAA"],
    "Homovanillic acid (HVA)": ["homovanillic acid", "HVA"],
    "Hexadecanoic acid (palmitic)": ["palmitic acid", "hexadecanoic acid"],
    "Carnitine":          ["carnitine", "L-carnitine"],
    "Acetylcarnitine":    ["acetylcarnitine", "acetyl-L-carnitine"],
}

_SERUM = '"serum"[TIAB] OR "plasma"[TIAB]'
_URINE = '"urine"[TIAB] OR "urinary"[TIAB]'
_SALIVA = '"saliva"[TIAB] OR "salivary"[TIAB]'
_FECES = '"feces"[TIAB] OR "faeces"[TIAB] OR "fecal"[TIAB] OR "stool"[TIAB]'
_CSF = '"cerebrospinal fluid"[TIAB] OR "CSF"[TIAB]'

# Kullanıcının elle seçebileceği matris tipleri
MATRIX_OPTIONS = {
    "Serum / Plazma": _SERUM,
    "İdrar": _URINE,
    "Salya": _SALIVA,
    "Gayta": _FECES,
    "BOS": _CSF,
}

MATRIX_TERMS = {
    "1. Serum-Plazma": _SERUM, "2. Idrar": _URINE, "3. Salya": _SALIVA,
    "4. Gayta": _FECES, "5. BOS": _CSF,
    "1. Serum-Plasma": _SERUM, "2. Urine": _URINE, "3. Saliva": _SALIVA,
    "4. Feces": _FECES, "5. CSF": _CSF,
    "2. Idrar-Urine": _URINE, "3. Salya-Saliva": _SALIVA,
    "4. Gayta-Feces": _FECES, "5. BOS-CSF": _CSF,
}

TERM_TO_LABEL = {v: k for k, v in MATRIX_OPTIONS.items()}


def secret(key, default=""):
    """secrets.toml yoksa da patlamayan güvenli okuma."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


# ----------------------------------------------------------------------------- çekirdek mantık
def build_query(metabolite_en, matrix_terms, synonyms, from_year=None, to_year=None):
    names = synonyms.get(metabolite_en, [metabolite_en])
    name_clause = " OR ".join(f'"{n}"[TIAB]' for n in names)
    q = (f'({name_clause}) AND ({matrix_terms}) '
         f'AND (metabolomics[TIAB] OR metabolite*[TIAB]) AND humans[MH]')
    if from_year or to_year:
        lo = from_year or 1800
        hi = to_year or _dt.date.today().year
        q += f' AND ("{lo}"[PDAT] : "{hi}"[PDAT])'
    return q


def esearch_count(query, api_key=None, email=None, tool="met4metab-count", retries=4):
    """(sayı, hata_mesajı) döndürür. Hata yoksa hata_mesajı None'dır."""
    params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": 0, "tool": tool}
    if api_key:
        params["api_key"] = api_key
    if email:
        params["email"] = email
    delay = 0.11 if api_key else 0.34  # NCBI: anahtarla 10/s, anahtarsız 3/s
    last_err = "bilinmeyen hata"
    for attempt in range(retries):
        try:
            r = requests.get(ESEARCH, params=params, timeout=30)
            if r.status_code == 429:
                last_err = "429 — hız sınırı"
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            time.sleep(delay)
            return int(r.json()["esearchresult"]["count"]), None
        except Exception as exc:
            last_err = str(exc)
            if attempt == retries - 1:
                return None, last_err
            time.sleep(2 ** attempt)
    return None, last_err


def sheet_row_count(ws):
    n, r = 0, HEADER_ROW + 1
    while ws.cell(r, SHEET_COLS["metabolite_en"]).value:
        n += 1
        r += 1
    return n


def guess_matrix(sheet_name):
    """Sayfa adından matris terimini tahmin eder."""
    if sheet_name in MATRIX_TERMS:
        return MATRIX_TERMS[sheet_name]
    low = sheet_name.lower()
    for key, terms in (("serum", _SERUM), ("plazma", _SERUM), ("plasma", _SERUM),
                       ("idrar", _URINE), ("urin", _URINE),
                       ("salya", _SALIVA), ("saliva", _SALIVA), ("tükürük", _SALIVA),
                       ("gayta", _FECES), ("fec", _FECES), ("fae", _FECES), ("dışkı", _FECES),
                       ("bos", _CSF), ("csf", _CSF), ("beyin", _CSF)):
        if key in low:
            return terms
    return _SERUM


def run_counts(wb, sheet_map, synonyms, api_key, email, from_year, to_year,
               do_sort, pct_as_value, tool, cache):
    """Seçili sayfaları işler; çalışma kitabını yerinde günceller."""
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    log_rows, previews, errors = [], {}, []

    total = sum(sheet_row_count(wb[name]) for name in sheet_map)
    done = 0
    bar = st.progress(0.0)
    status = st.empty()

    for name, terms in sheet_map.items():
        ws = wb[name]
        ncols = max(ws.max_column, SHEET_COLS["query"])
        data = []

        r = HEADER_ROW + 1
        while ws.cell(r, SHEET_COLS["metabolite_en"]).value:
            met = str(ws.cell(r, SHEET_COLS["metabolite_en"]).value).strip()
            query = build_query(met, terms, synonyms, from_year, to_year)

            if query in cache:
                cnt, err = cache[query], None
            else:
                cnt, err = esearch_count(query, api_key, email, tool)
                if err is None:
                    cache[query] = cnt

            if err:
                errors.append(f"**{name} / {met}** — {err}")

            done += 1
            bar.progress(done / total if total else 1.0)
            status.write(
                f"`{name}` — {met} → **{cnt if cnt is not None else 'HATA'}**  "
                f"({done}/{total})"
            )

            row_vals = [ws.cell(r, c).value for c in range(1, ncols + 1)]
            row_vals[SHEET_COLS["count"] - 1] = cnt
            row_vals[SHEET_COLS["query"] - 1] = query
            data.append((cnt, row_vals))
            log_rows.append([stamp, name, met, cnt, query])
            r += 1

        if do_sort:
            data.sort(key=lambda t: (-1 if t[0] is None else t[0]), reverse=True)

        total_cnt = sum(c for c, _ in data if c is not None)
        last = HEADER_ROW + len(data)
        rows_preview = []

        for i, (cnt, vals) in enumerate(data, start=1):
            rr = HEADER_ROW + i
            vals[0] = i  # sırayı yeniden numaralandır
            for c, v in enumerate(vals, start=1):
                cell = ws.cell(rr, c, value=v)
                cell.font = Font(name="Arial", size=9)
                cell.alignment = Alignment(
                    vertical="top",
                    wrap_text=(c in (2, 3, 4, 7, 8)),
                    horizontal="center" if c in (1, 5, 6) else None,
                )
            share = (cnt / total_cnt) if (cnt is not None and total_cnt) else None
            if pct_as_value:
                ws.cell(rr, 6, value=share)
            else:
                ws.cell(rr, 6,
                        value=f'=IFERROR(E{rr}/SUM($E${HEADER_ROW + 1}:$E${last}),"")')
            ws.cell(rr, 6).number_format = "0.0%"

            rows_preview.append({
                "#": i,
                "Metabolit": vals[SHEET_COLS["metabolite_en"] - 1],
                "Sayı": cnt,
                "Pay (%)": round(share * 100, 2) if share is not None else None,
            })

        previews[name] = pd.DataFrame(rows_preview)

    bar.empty()
    status.empty()
    return log_rows, previews, errors, stamp


# ----------------------------------------------------------------------------- arayüz
st.set_page_config(page_title="PubMed Metabolit Sayımı", page_icon="🧪", layout="wide")

st.title("🧪 PubMed yayın sayımı — metabolit × biyoakışkan")
st.caption(
    "Literatür sıklığı çalışma kitabındaki her matris sayfası için PubMed sorgusu kurar, "
    "NCBI E-utilities üzerinden isabet sayısını çeker ve satırları azalan sıraya dizer."
)

ss = st.session_state
ss.setdefault("qcache", {})
ss.setdefault("out_xlsx", None)
ss.setdefault("out_log", None)
ss.setdefault("previews", {})
ss.setdefault("errors", [])
ss.setdefault("stamp", None)
ss.setdefault("out_name", "counted.xlsx")

# --- kenar çubuğu
with st.sidebar:
    st.header("NCBI ayarları")
    api_key = st.text_input(
        "API anahtarı", type="password",
        value=secret("NCBI_API_KEY"),
        help="account.ncbi.nlm.nih.gov/settings/ adresinden ücretsiz. "
             "Anahtarla 10 istek/s, anahtarsız 3 istek/s.",
    )
    email = st.text_input("İletişim e-postası", help="NCBI kullanım kuralları gereği önerilir.")
    tool = st.text_input("tool adı", value="met4metab-count")

    st.divider()
    st.header("Sorgu ayarları")
    use_years = st.checkbox("Yayın yılı aralığı uygula")
    from_year = to_year = None
    if use_years:
        c1, c2 = st.columns(2)
        from_year = c1.number_input("Başlangıç", 1800, 2100, 2010, step=1)
        to_year = c2.number_input("Bitiş", 1800, 2100, _dt.date.today().year, step=1)

    st.divider()
    st.header("Çıktı ayarları")
    do_sort = st.checkbox("Satırları sayıya göre sırala", value=True)
    pct_as_value = st.checkbox(
        "Yüzde sütununu değer olarak yaz", value=True,
        help="Kapalıysa formül yazılır ve dosyanın bir kez açılıp kaydedilmesi gerekir.",
    )
    use_cache = st.checkbox("Aynı sorguyu tekrar sorma (oturum önbelleği)", value=True)
    if st.button("Önbelleği temizle", use_container_width=True):
        ss.qcache = {}
        st.toast("Önbellek temizlendi.")

    if not api_key:
        st.warning("API anahtarı yok: 3 istek/s sınırı uygulanacak (≈0,34 s/sorgu).")

# --- eşanlamlı sözlüğü
with st.expander("Eşanlamlı sözlüğü (JSON) — sorgular buna göre kurulur"):
    syn_text = st.text_area(
        "SYNONYMS", json.dumps(SYNONYMS, ensure_ascii=False, indent=2),
        height=260, label_visibility="collapsed",
    )
    try:
        synonyms = json.loads(syn_text)
        st.caption(f"✅ {len(synonyms)} giriş okundu.")
    except json.JSONDecodeError as exc:
        synonyms = SYNONYMS
        st.error(f"JSON hatası, varsayılan sözlük kullanılacak: {exc}")

# --- dosya yükleme
uploaded = st.file_uploader("Çalışma kitabı (.xlsx)", type=["xlsx"])

if uploaded is None:
    st.info(
        "Metabolite_Literature_Frequency_EN.xlsx veya TR sürümünü yükleyin. "
        "Beklenen düzen: başlık 4. satırda, C = metabolit (EN), E = sayı, F = pay, H = sorgu."
    )
else:
    raw = uploaded.getvalue()
    wb_probe = openpyxl.load_workbook(io.BytesIO(raw))
    auto = [s for s in dict.fromkeys(MATRIX_SHEETS) if s in wb_probe.sheetnames]

    chosen = st.multiselect(
        "İşlenecek sayfalar",
        options=wb_probe.sheetnames,
        default=auto or wb_probe.sheetnames,
    )

    sheet_map = {}
    if chosen:
        with st.expander("Matris terimlerini gözden geçir", expanded=not auto):
            for name in chosen:
                guessed = guess_matrix(name)
                labels = list(MATRIX_OPTIONS)
                idx = labels.index(TERM_TO_LABEL[guessed])
                pick = st.selectbox(name, labels, index=idx, key=f"mx_{name}")
                sheet_map[name] = MATRIX_OPTIONS[pick]

        n_rows = sum(sheet_row_count(wb_probe[s]) for s in chosen)
        eta = n_rows * (0.11 if api_key else 0.34)
        st.caption(f"{len(chosen)} sayfa, {n_rows} sorgu — tahmini süre ≈ {eta / 60:.1f} dk.")

    if st.button("▶️ Sayımı başlat", type="primary", disabled=not chosen):
        wb = openpyxl.load_workbook(io.BytesIO(raw))
        cache = ss.qcache if use_cache else {}
        log_rows, previews, errors, stamp = run_counts(
            wb, sheet_map, synonyms, api_key or None, email or None,
            from_year, to_year, do_sort, pct_as_value, tool or "met4metab-count", cache,
        )

        buf = io.BytesIO()
        wb.save(buf)
        ss.out_xlsx = buf.getvalue()
        ss.out_name = re.sub(r"\.xlsx$", "_counted.xlsx", uploaded.name)

        sio = io.StringIO()
        w = csv.writer(sio)
        w.writerow(["timestamp", "sheet", "metabolite", "count", "query"])
        w.writerows(log_rows)
        ss.out_log = sio.getvalue().encode("utf-8-sig")

        ss.previews, ss.errors, ss.stamp = previews, errors, stamp

# --- sonuçlar
if ss.out_xlsx:
    st.success(f"Sayım tamamlandı — {ss.stamp}")
    if ss.errors:
        with st.expander(f"⚠️ {len(ss.errors)} sorgu başarısız"):
            for e in ss.errors:
                st.write("- " + e)

    c1, c2 = st.columns(2)
    c1.download_button(
        "⬇️ Çalışma kitabını indir", ss.out_xlsx, file_name=ss.out_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
    c2.download_button(
        "⬇️ Log dosyasını indir (CSV)", ss.out_log, file_name="pubmed_count_log.csv",
        mime="text/csv", use_container_width=True,
    )

    if ss.previews:
        for tab, (name, df) in zip(st.tabs(list(ss.previews)), ss.previews.items()):
            with tab:
                st.dataframe(df, use_container_width=True, hide_index=True)
                if not df["Sayı"].isna().all():
                    st.bar_chart(df.set_index("Metabolit")["Sayı"].head(20))

# --- tek sorgu testi
with st.expander("🔍 Tek sorgu testi"):
    tq = st.text_area(
        "PubMed sorgusu",
        value=build_query("Citrate", _URINE, SYNONYMS),
        height=90,
    )
    if st.button("Sorguyu çalıştır"):
        cnt, err = esearch_count(tq, api_key or None, email or None, tool or "met4metab-count")
        if err:
            st.error(err)
        else:
            st.metric("Sonuç sayısı", f"{cnt:,}".replace(",", "."))

st.caption(
    "Sayımlar sorguya duyarlıdır ve PubMed büyüdükçe değişir; bu yüzden log zaman damgalıdır. "
    "Metrolojik olarak bir sayım, tarihi olmadan bir şey ifade etmez."
)
