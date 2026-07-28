#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — PubMed / Europe PMC yayın sayımı (metabolit × biyoakışkan)

Çalıştırma:
    pip install -r requirements.txt
    streamlit run app.py
"""

import csv
import datetime as _dt
import io
import json
import os
import re

import openpyxl
import pandas as pd
import streamlit as st
from openpyxl.styles import Alignment, Font

from backends import (
    MATRIX_LABELS,
    EuropePMCBackend,
    PubMedBackend,
    guess_matrix_key,
)
from cache import CountCache

# ----------------------------------------------------------------- sayfa düzeni
SHEET_COLS = {"metabolite_en": 3, "count": 5, "query": 8}
HEADER_ROW = 4  # veri satırları 5'ten başlar
EXTRA_COL = 9   # karşılaştırma sütunları buradan itibaren yazılır

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


# --------------------------------------------------------------------- yardımcı
def secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


@st.cache_resource(show_spinner=False)
def get_cache(path: str) -> CountCache:
    return CountCache(path)


def sheet_row_count(ws) -> int:
    n, r = 0, HEADER_ROW + 1
    while ws.cell(r, SHEET_COLS["metabolite_en"]).value:
        n += 1
        r += 1
    return n


def spearman(a: pd.Series, b: pd.Series):
    """Sıra korelasyonu — sıralara uygulanan Pearson. scipy gerektirmez."""
    ok = a.notna() & b.notna()
    if ok.sum() < 3:
        return None
    return float(a[ok].rank().corr(b[ok].rank()))


def topn_overlap(df, col_a, col_b, n):
    """İki kaynağın ilk n metaboliti ne kadar örtüşüyor."""
    top_a = set(df.nlargest(n, col_a)["Metabolit"])
    top_b = set(df.nlargest(n, col_b)["Metabolit"])
    return len(top_a & top_b), sorted(top_a ^ top_b)


# ------------------------------------------------------------------ ana işleyiş
def run_counts(wb, sheet_map, synonyms, backends, primary, opts, cache):
    """Seçili sayfaları işler, çalışma kitabını yerinde günceller."""
    stamp = _dt.datetime.now().isoformat(timespec="seconds")
    log_rows, previews, errors = [], {}, []
    hits = misses = 0

    total = sum(sheet_row_count(wb[s]) for s in sheet_map) * len(backends)
    done = 0
    bar = st.progress(0.0)
    status = st.empty()

    for sheet, mkey in sheet_map.items():
        ws = wb[sheet]
        records = []

        r = HEADER_ROW + 1
        while ws.cell(r, SHEET_COLS["metabolite_en"]).value:
            met = str(ws.cell(r, SHEET_COLS["metabolite_en"]).value).strip()
            ncols = max(ws.max_column, SHEET_COLS["query"])
            rec = {
                "met": met,
                "vals": [ws.cell(r, c).value for c in range(1, ncols + 1)],
                "counts": {}, "queries": {},
            }

            for b in backends:
                q = b.build_query(met, mkey, synonyms,
                                  opts["from_year"], opts["to_year"])
                cached = cache.get(b.name, q, opts["ttl"]) if cache else None
                if cached is not None:
                    cnt, fetched = cached
                    hits += 1
                    from_cache = True
                else:
                    cnt, err = b.count(q)
                    from_cache = False
                    misses += 1
                    fetched = stamp
                    if err:
                        errors.append(f"**{sheet} / {met}** — {b.name}: {err}")
                    elif cache:
                        fetched = cache.put(b.name, q, cnt)

                rec["counts"][b.name] = cnt
                rec["queries"][b.name] = q
                log_rows.append([stamp, sheet, met, b.name, cnt,
                                 "evet" if from_cache else "hayır", fetched, q])

                done += 1
                bar.progress(min(done / total, 1.0) if total else 1.0)
                status.write(
                    f"`{sheet}` · {met} · {b.name} → "
                    f"**{cnt if cnt is not None else 'HATA'}**"
                    f"{' (önbellek)' if from_cache else ''} — {done}/{total}"
                )

            records.append(rec)
            r += 1

        previews[sheet] = _finalise_sheet(ws, records, backends, primary, opts)

    bar.empty()
    status.empty()
    return {
        "log": log_rows, "previews": previews, "errors": errors,
        "stamp": stamp, "hits": hits, "misses": misses,
    }


def _finalise_sheet(ws, records, backends, primary, opts):
    """Satırları sıralar, çalışma sayfasına yazar, önizleme tablosu döndürür."""
    if not records:
        return pd.DataFrame()

    if opts["sort"]:
        records.sort(key=lambda rec: (-1 if rec["counts"].get(primary.name) is None
                                      else rec["counts"][primary.name]), reverse=True)

    df = pd.DataFrame({
        "Metabolit": [rec["met"] for rec in records],
        **{f"{b.name} sayı": [rec["counts"].get(b.name) for rec in records]
           for b in backends},
    })
    for b in backends:
        df[f"{b.name} sıra"] = df[f"{b.name} sayı"].rank(
            ascending=False, method="min").astype("Int64")
    if len(backends) == 2:
        a, c = backends[0].name, backends[1].name
        df["Δ sıra"] = (df[f"{a} sıra"] - df[f"{c} sıra"]).abs()

    total_cnt = sum(v for v in df[f"{primary.name} sayı"] if pd.notna(v))
    last = HEADER_ROW + len(records)
    secondary = [b for b in backends if b.name != primary.name]

    if secondary:
        sec = secondary[0].name
        for offset, label in enumerate([f"{sec} sayı", f"{sec} sıra",
                                        f"{primary.name} sıra", "Δ sıra"]):
            cell = ws.cell(HEADER_ROW, EXTRA_COL + offset, value=label)
            cell.font = Font(name="Arial", size=9, bold=True)
            cell.alignment = Alignment(vertical="center", horizontal="center",
                                       wrap_text=True)

    for i, rec in enumerate(records, start=1):
        rr = HEADER_ROW + i
        vals = rec["vals"]
        vals[0] = i
        vals[SHEET_COLS["count"] - 1] = rec["counts"].get(primary.name)
        vals[SHEET_COLS["query"] - 1] = rec["queries"].get(primary.name)

        for c, v in enumerate(vals, start=1):
            cell = ws.cell(rr, c, value=v)
            cell.font = Font(name="Arial", size=9)
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=(c in (2, 3, 4, 7, 8)),
                horizontal="center" if c in (1, 5, 6) else None,
            )

        cnt = rec["counts"].get(primary.name)
        share = (cnt / total_cnt) if (cnt is not None and total_cnt) else None
        if opts["pct_value"]:
            ws.cell(rr, 6, value=share)
        else:
            ws.cell(rr, 6,
                    value=f'=IFERROR(E{rr}/SUM($E${HEADER_ROW + 1}:$E${last}),"")')
        ws.cell(rr, 6).number_format = "0.0%"

        if secondary:
            sec = secondary[0].name
            row = df.iloc[i - 1]
            for offset, v in enumerate([
                rec["counts"].get(sec),
                None if pd.isna(row[f"{sec} sıra"]) else int(row[f"{sec} sıra"]),
                None if pd.isna(row[f"{primary.name} sıra"]) else int(row[f"{primary.name} sıra"]),
                None if pd.isna(row.get("Δ sıra")) else int(row["Δ sıra"]),
            ]):
                cell = ws.cell(rr, EXTRA_COL + offset, value=v)
                cell.font = Font(name="Arial", size=9)
                cell.alignment = Alignment(vertical="top", horizontal="center")

    df.insert(0, "#", range(1, len(df) + 1))
    return df


# ------------------------------------------------------------------------ arayüz
st.set_page_config(page_title="Metabolit Literatür Sayımı", page_icon="🧪", layout="wide")
st.title("🧪 Literatür sayımı — metabolit × biyoakışkan")
st.caption(
    "PubMed (E-utilities) ve Europe PMC üzerinden yayın sayısı çeker, "
    "sonuçları çalışma kitabına yazar ve iki kaynağın sıralamasını karşılaştırır."
)

ss = st.session_state
for k, v in {"out_xlsx": None, "out_log": None, "res": None,
             "out_name": "counted.xlsx", "backend_names": []}.items():
    ss.setdefault(k, v)

with st.sidebar:
    st.header("Kaynak")
    mode = st.radio(
        "Sayım kaynağı", ["PubMed", "Europe PMC", "İkisi (karşılaştırma)"],
        index=0, label_visibility="collapsed",
    )
    both = mode.startswith("İkisi")
    primary_name = "PubMed"
    if both:
        primary_name = st.selectbox(
            "Çalışma kitabına yazılacak kaynak", ["PubMed", "Europe PMC"],
            help="E sütununu, yüzdeyi ve sıralamayı bu kaynak belirler. "
                 "Diğeri I sütunundan itibaren yazılır.",
        )

    if mode != "Europe PMC":
        st.subheader("PubMed")
        api_key = st.text_input("API anahtarı (isteğe bağlı)", type="password",
                                value=secret("NCBI_API_KEY"))
        email = st.text_input("İletişim e-postası")
        tool = st.text_input("tool adı", value="met4metab-count")
        pm_delay = st.number_input(
            "İstekler arası (s)", 0.05, 5.0,
            0.11 if api_key else 0.40, step=0.01, format="%.2f",
            help="Anahtarsız sınır 3 istek/s. 0,40 s güvenli tarafta kalır.",
        )
        if not api_key:
            st.caption("Anahtarsız çalışıyor — 3 istek/s sınırı geçerli.")
    else:
        api_key = email = tool = ""
        pm_delay = 0.40

    if mode != "PubMed":
        st.subheader("Europe PMC")
        st.caption("Anahtar veya kayıt gerektirmez.")
        epmc_synonym = st.checkbox(
            "MeSH eşanlamlı genişletmesi", value=False,
            help="Açıkken sayım kendi SYNONYMS sözlüğünüze izlenebilir olmaktan çıkar.",
        )
        epmc_medline = st.checkbox(
            "SRC:MED ile MEDLINE alt kümesine indir", value=True,
            help="PubMed ile karşılaştırılabilirlik için önerilir.",
        )
        human_filter = st.text_input(
            "İnsan filtresi", value='MESH_TERMS:"Humans"',
            help="Alan adını Advanced Search Query Builder ile doğrulayın; "
                 "boş bırakılırsa filtre uygulanmaz.",
        )
        epmc_delay = st.number_input("İstekler arası (s) ", 0.05, 5.0, 0.20,
                                     step=0.01, format="%.2f")
    else:
        epmc_synonym, epmc_medline, epmc_delay = False, True, 0.20
        human_filter = 'MESH_TERMS:"Humans"'

    st.divider()
    st.header("Sorgu")
    use_years = st.checkbox("Yayın yılı aralığı uygula")
    from_year = to_year = None
    if use_years:
        c1, c2 = st.columns(2)
        from_year = c1.number_input("Başlangıç", 1800, 2100, 2010, step=1)
        to_year = c2.number_input("Bitiş", 1800, 2100, _dt.date.today().year, step=1)

    st.divider()
    st.header("Önbellek")
    use_cache = st.checkbox("Kalıcı önbelleği kullan", value=True)
    cache_path = st.text_input("Dosya", value=os.environ.get("COUNT_CACHE",
                                                            "pubmed_cache.sqlite"))
    ttl = st.number_input("Geçerlilik (gün)", 0, 3650, 30, step=1,
                          help="0 = her sorgu yeniden çekilir.")
    cache_obj = get_cache(cache_path) if use_cache else None
    if cache_obj:
        st.caption(f"{cache_obj.total()} kayıt")
        if cache_obj.total():
            st.dataframe(pd.DataFrame(cache_obj.stats()), hide_index=True)
        cc1, cc2 = st.columns(2)
        if cc1.button("Temizle", use_container_width=True):
            n = cache_obj.clear()
            st.toast(f"{n} kayıt silindi.")
            st.rerun()
        cc2.download_button("Dışa aktar", cache_obj.export_csv(),
                            file_name="count_cache.csv", mime="text/csv",
                            use_container_width=True)
        imp = st.file_uploader("Önbellek içe aktar (CSV)", type=["csv"],
                               key="cache_import")
        if imp is not None and st.button("İçe aktar", use_container_width=True):
            n = cache_obj.import_csv(imp.getvalue())
            st.toast(f"{n} kayıt alındı.")
            st.rerun()
        st.caption("Streamlit Cloud'da dosya sistemi kalıcı değildir — "
                   "koşu sonrası önbelleği dışa aktarın.")

    st.divider()
    st.header("Çıktı")
    do_sort = st.checkbox("Satırları sayıya göre sırala", value=True)
    pct_value = st.checkbox("Yüzdeyi değer olarak yaz", value=True)
    topn = st.number_input("Örtüşme için ilk N", 5, 200, 20, step=5)

# arka uçları kur
backends = []
if mode != "Europe PMC":
    backends.append(PubMedBackend(api_key or None, email or None,
                                  tool or "met4metab-count", pm_delay))
if mode != "PubMed":
    backends.append(EuropePMCBackend(epmc_delay, epmc_synonym, epmc_medline,
                                     human_filter, email or None))
primary = next((b for b in backends if b.name == primary_name), backends[0])

st.info(" · ".join(b.describe() for b in backends) +
        (f"  →  çalışma kitabına **{primary.name}** yazılacak" if both else ""))

with st.expander("Eşanlamlı sözlüğü (JSON)"):
    syn_text = st.text_area("SYNONYMS", json.dumps(SYNONYMS, ensure_ascii=False, indent=2),
                            height=240, label_visibility="collapsed")
    try:
        synonyms = json.loads(syn_text)
        st.caption(f"✅ {len(synonyms)} giriş.")
    except json.JSONDecodeError as exc:
        synonyms = SYNONYMS
        st.error(f"JSON hatası, varsayılan sözlük kullanılacak: {exc}")

uploaded = st.file_uploader("Çalışma kitabı (.xlsx)", type=["xlsx"])

if uploaded is None:
    st.caption("Beklenen düzen: başlık 4. satırda, C = metabolit (EN), "
               "E = sayı, F = pay, H = sorgu.")
else:
    raw = uploaded.getvalue()
    probe = openpyxl.load_workbook(io.BytesIO(raw))
    auto = [s for s in dict.fromkeys(MATRIX_SHEETS) if s in probe.sheetnames]
    chosen = st.multiselect("İşlenecek sayfalar", probe.sheetnames,
                            default=auto or probe.sheetnames)

    sheet_map = {}
    if chosen:
        with st.expander("Matris eşleştirmesi", expanded=not auto):
            keys = list(MATRIX_LABELS)
            for s in chosen:
                idx = keys.index(guess_matrix_key(s))
                pick = st.selectbox(s, keys, index=idx, key=f"mx_{s}",
                                    format_func=lambda k: MATRIX_LABELS[k])
                sheet_map[s] = pick

        rows = sum(sheet_row_count(probe[s]) for s in chosen)
        eta = rows * sum(b.limiter.min_interval for b in backends)
        st.caption(f"{len(chosen)} sayfa · {rows * len(backends)} sorgu · "
                   f"önbelleksiz tahmini süre ≈ {eta / 60:.1f} dk")

    if st.button("▶️ Sayımı başlat", type="primary", disabled=not chosen):
        wb = openpyxl.load_workbook(io.BytesIO(raw))
        res = run_counts(
            wb, sheet_map, synonyms, backends, primary,
            {"from_year": from_year, "to_year": to_year, "sort": do_sort,
             "pct_value": pct_value, "ttl": (None if ttl == 0 else int(ttl))},
            cache_obj,
        )

        buf = io.BytesIO()
        wb.save(buf)
        ss.out_xlsx = buf.getvalue()
        ss.out_name = re.sub(r"\.xlsx$", "_counted.xlsx", uploaded.name)

        sio = io.StringIO()
        w = csv.writer(sio)
        w.writerow(["run_timestamp", "sheet", "metabolite", "backend",
                    "count", "from_cache", "fetched_at", "query"])
        w.writerows(res["log"])
        ss.out_log = sio.getvalue().encode("utf-8-sig")
        ss.res = res
        ss.backend_names = [b.name for b in backends]

# ------------------------------------------------------------------- sonuçlar
res = ss.res
if res and ss.out_xlsx:
    st.success(
        f"Tamamlandı — {res['stamp']} · {res['misses']} istek, "
        f"{res['hits']} önbellekten"
    )
    if res["errors"]:
        with st.expander(f"⚠️ {len(res['errors'])} başarısız sorgu"):
            for e in res["errors"]:
                st.write("- " + e)

    c1, c2 = st.columns(2)
    c1.download_button("⬇️ Çalışma kitabı", ss.out_xlsx, file_name=ss.out_name,
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet", use_container_width=True)
    c2.download_button("⬇️ Log (CSV)", ss.out_log, file_name="count_log.csv",
                       mime="text/csv", use_container_width=True)

    names = ss.backend_names
    for tab, (sheet, df) in zip(st.tabs(list(res["previews"])),
                                res["previews"].items()):
        with tab:
            if df.empty:
                st.warning("Bu sayfada veri bulunamadı.")
                continue
            st.dataframe(df, use_container_width=True, hide_index=True)

            if len(names) == 2:
                a, b = names
                ca, cb = f"{a} sayı", f"{b} sayı"
                rho = spearman(df[ca], df[cb])
                n = min(int(topn), len(df))
                shared, diff = topn_overlap(df, ca, cb, n)

                st.subheader("Kaynak karşılaştırması")
                m1, m2, m3 = st.columns(3)
                m1.metric("Spearman ρ", f"{rho:.3f}" if rho is not None else "—")
                m2.metric(f"İlk {n} örtüşmesi", f"{shared}/{n}")
                m3.metric("En büyük Δ sıra",
                          int(df["Δ sıra"].max()) if df["Δ sıra"].notna().any() else "—")

                st.scatter_chart(df, x=f"{a} sıra", y=f"{b} sıra")
                if diff:
                    st.caption("Yalnızca tek kaynakta ilk %d'e girenler: %s"
                               % (n, ", ".join(diff)))
                st.caption(
                    "ρ yüksekse metabolit seçimi veri tabanı seçimine duyarlı "
                    "değildir; bu, tek bir sayım listesinden daha savunulabilir "
                    "bir ifadedir."
                )
            else:
                col = f"{names[0]} sayı"
                if df[col].notna().any():
                    st.bar_chart(df.head(20).set_index("Metabolit")[col])

# --------------------------------------------------------------- tek sorgu testi
with st.expander("🔍 Tek sorgu testi"):
    tb_name = st.selectbox("Kaynak", [b.name for b in backends], key="test_backend")
    tb = next(b for b in backends if b.name == tb_name)
    default_q = tb.build_query("Citrate", "urine", SYNONYMS, from_year, to_year)
    tq = st.text_area("Sorgu", value=default_q, height=110, key="test_query")
    if st.button("Çalıştır", key="test_run"):
        cnt, err = tb.count(tq)
        if err:
            st.error(err)
        else:
            st.metric("Sonuç sayısı", f"{cnt:,}".replace(",", "."))

st.caption(
    "Sayımlar sorguya ve tarihe duyarlıdır. Log dosyası her satır için kaynağı, "
    "sorguyu ve çekilme zamanını taşır; rapora bu üçü birlikte girer."
)
