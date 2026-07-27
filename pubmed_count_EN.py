#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pubmed_count.py — publication counts per metabolite per biofluid

WHAT IT DOES
------------
Reads each matrix sheet of the metabolite literature-frequency workbook, runs the
query held in the "PubMed query" column through NCBI E-utilities, writes the number
of hits into the "Count" column, then re-sorts the rows by count, descending.
Both the English and the Turkish workbook are recognised.

USAGE
-----
    pip install openpyxl requests
    python pubmed_count.py Metabolite_Literature_Frequency_EN.xlsx

    # With an NCBI API key (10 requests/s instead of 3) — recommended:
    #   free from https://account.ncbi.nlm.nih.gov/settings/
    python pubmed_count.py workbook.xlsx --api-key XXXX --email you@institute.gov

    # Restrict to a publication-year window:
    python pubmed_count.py workbook.xlsx --from-year 2010 --to-year 2026

    # Process selected sheets only:
    python pubmed_count.py workbook.xlsx --sheets "1. Serum-Plasma" "2. Urine"

OUTPUT
------
    <input>_counted.xlsx     — counts filled in and rows re-sorted
    pubmed_count_log.csv     — every query, its count and a timestamp (for reproducibility)

NOTES
-----
* Counts are query-sensitive. Extend the SYNONYMS dictionary to suit your protocol.
* NCBI usage rules: at most 3 requests/s without an API key. The script complies.
* The same query returns different counts on different days because PubMed grows —
  which is why the log is timestamped. Metrologically, a count means nothing without its date.
"""


import argparse
import csv
import datetime as _dt
import re
import sys
import time
import urllib.parse

try:
    import requests
except ImportError:
    sys.exit("requests is required:  pip install requests")
try:
    import openpyxl
    from openpyxl.styles import Font, Alignment
except ImportError:
    sys.exit("openpyxl is required:  pip install openpyxl")

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

# Sheet column positions: metabolite (EN), count, query
SHEET_COLS = {"metabolite_en": 3, "count": 5, "query": 8}
HEADER_ROW = 4          # data rows start at 5
# Both language workbooks are recognised
MATRIX_SHEETS = [
    "1. Serum-Plazma", "2. Idrar", "3. Salya", "4. Gayta", "5. BOS",
    "1. Serum-Plasma", "2. Urine", "3. Saliva", "4. Feces", "5. CSF",
    "1. Serum-Plazma", "2. Idrar-Urine", "3. Salya-Saliva", "4. Gayta-Feces", "5. BOS-CSF",
]

# Synonyms, ORed together so the count is fair.
# Extend for your own review.
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

MATRIX_TERMS = {
    # Turkish workbook
    "1. Serum-Plazma": _SERUM, "2. Idrar": _URINE, "3. Salya": _SALIVA,
    "4. Gayta": _FECES, "5. BOS": _CSF,
    # English workbook
    "1. Serum-Plasma": _SERUM, "2. Urine": _URINE, "3. Saliva": _SALIVA,
    "4. Feces": _FECES, "5. CSF": _CSF,
    # legacy single-file naming
    "2. Idrar-Urine": _URINE, "3. Salya-Saliva": _SALIVA,
    "4. Gayta-Feces": _FECES, "5. BOS-CSF": _CSF,
}


def build_query(metabolite_en, matrix_terms, from_year=None, to_year=None):
    names = SYNONYMS.get(metabolite_en, [metabolite_en])
    name_clause = " OR ".join(f'"{n}"[TIAB]' for n in names)
    q = (f'({name_clause}) AND ({matrix_terms}) '
         f'AND (metabolomics[TIAB] OR metabolite*[TIAB]) AND humans[MH]')
    if from_year or to_year:
        lo = from_year or 1800
        hi = to_year or _dt.date.today().year
        q += f' AND ("{lo}"[PDAT] : "{hi}"[PDAT])'
    return q


def esearch_count(query, api_key=None, email=None, tool="met4metab-count", retries=4):
    params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": 0, "tool": tool}
    if api_key:
        params["api_key"] = api_key
    if email:
        params["email"] = email
    delay = 0.11 if api_key else 0.34          # NCBI: 10/s with a key, 3/s without
    for attempt in range(retries):
        try:
            r = requests.get(ESEARCH, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            time.sleep(delay)
            return int(r.json()["esearchresult"]["count"])
        except Exception as exc:
            if attempt == retries - 1:
                print(f"    ! failed: {exc}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
    return None


def main():
    ap = argparse.ArgumentParser(description="PubMed publication counts per metabolite per biofluid")
    ap.add_argument("xlsx")
    ap.add_argument("--api-key", default=None, help="NCBI API key (recommended)")
    ap.add_argument("--email", default=None, help="contact e-mail for NCBI")
    ap.add_argument("--from-year", type=int, default=None)
    ap.add_argument("--to-year", type=int, default=None)
    ap.add_argument("--sheets", nargs="*", default=None)
    ap.add_argument("--no-sort", action="store_true", help="do not re-sort the rows")
    args = ap.parse_args()

    wb = openpyxl.load_workbook(args.xlsx)
    seen = set()
    sheets = args.sheets or [s for s in MATRIX_SHEETS
                             if s in wb.sheetnames and not (s in seen or seen.add(s))]
    stamp = _dt.datetime.now().isoformat(timespec="seconds")

    log_rows = []
    for name in sheets:
        if name not in wb.sheetnames:
            print(f"[skipped] {name} not found")
            continue
        ws = wb[name]
        terms = MATRIX_TERMS.get(name)
        if not terms:
            print(f"[skipped] no matrix terms defined for {name}")
            continue
        print(f"\n=== {name} ===")

        data = []
        r = HEADER_ROW + 1
        while ws.cell(r, SHEET_COLS["metabolite_en"]).value:
            met = str(ws.cell(r, SHEET_COLS["metabolite_en"]).value).strip()
            query = build_query(met, terms, args.from_year, args.to_year)
            cnt = esearch_count(query, args.api_key, args.email)
            print(f"  {met:42s} {cnt if cnt is not None else 'ERROR'}")
            row_vals = [ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
            row_vals[SHEET_COLS["count"] - 1] = cnt
            row_vals[SHEET_COLS["query"] - 1] = query
            data.append((cnt if cnt is not None else -1, row_vals))
            log_rows.append([stamp, name, met, cnt, query])
            r += 1

        if not args.no_sort:
            data.sort(key=lambda t: t[0], reverse=True)

        for i, (_, vals) in enumerate(data, start=1):
            rr = HEADER_ROW + i
            vals[0] = i                                  # renumber the rank
            for c, v in enumerate(vals, start=1):
                cell = ws.cell(rr, c, value=v)
                cell.font = Font(name="Arial", size=9)
                cell.alignment = Alignment(
                    vertical="top",
                    wrap_text=(c in (2, 3, 4, 7, 8)),
                    horizontal="center" if c in (1, 5, 6) else None,
                )
            # percentage column: share within the matrix
            last = HEADER_ROW + len(data)
            ws.cell(rr, 6, value=f"=IFERROR(E{rr}/SUM($E${HEADER_ROW+1}:$E${last}),\"\")")
            ws.cell(rr, 6).number_format = "0.0%"

    out = re.sub(r"\.xlsx$", "_counted.xlsx", args.xlsx)
    wb.save(out)
    with open("pubmed_count_log.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "sheet", "metabolite", "count", "query"])
        w.writerows(log_rows)

    print(f"\nSaved: {out}")
    print("Log:   pubmed_count_log.csv")
    print("\nNOTE: to make the percentage column evaluate, open the file and save it once,")
    print("      or run:  soffice --headless --convert-to xlsx " + out)


if __name__ == "__main__":
    main()
