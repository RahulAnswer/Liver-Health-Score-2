import math
import re
import io
from typing import Optional, Tuple, Dict

import streamlit as st
import pandas as pd

# PDF parsing
try:
    import pdfplumber
    PDF_ENABLED = True
except Exception:
    PDF_ENABLED = False

# PDF creation
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    REPORTLAB_ENABLED = True
except Exception:
    REPORTLAB_ENABLED = False

st.set_page_config(page_title="Liver Health Assessment (Validated Option A) — PDF Ready", layout="wide")

# ---------- Utility functions ----------
def _safe_log(x: Optional[float]) -> Optional[float]:
    try:
        if x is None or float(x) <= 0:
            return None
        return math.log(float(x))
    except Exception:
        return None

def fli_score(tg_mgdl, bmi, ggt_ul, waist_cm) -> Optional[float]:
    ln_tg = _safe_log(tg_mgdl)
    ln_ggt = _safe_log(ggt_ul)
    if None in (ln_tg, ln_ggt) or tg_mgdl in (None, "") or bmi in (None, "") or waist_cm in (None, ""):
        return None
    L = 0.953 * ln_tg + 0.139 * float(bmi) + 0.718 * ln_ggt + 0.053 * float(waist_cm) - 15.745
    f = (math.exp(L) / (1 + math.exp(L))) * 100.0
    return max(0.0, min(100.0, f))

def fli_category_action(fli: Optional[float]) -> Tuple[Optional[str], Optional[str], str]:
    if fli is None:
        return None, None, "#cccccc"
    if fli < 30:
        return "Low (fatty liver unlikely)", "Maintain lifestyle; periodic monitoring.", "#2e7d32"
    if fli < 60:
        return "Intermediate (cannot rule in/out)", "Consider ultrasound or repeat after lifestyle optimisation.", "#f9a825"
    return "High (fatty liver likely)", "Proceed to fibrosis staging (NFS, FIB-4, APRI).", "#c62828"

def fib4_score(age, ast_ul, alt_ul, platelets) -> Optional[float]:
    try:
        age, ast_ul, alt_ul, platelets = float(age), float(ast_ul), float(alt_ul), float(platelets)
        if alt_ul <= 0 or platelets <= 0:
            return None
        return (age * ast_ul) / (platelets * math.sqrt(alt_ul))
    except Exception:
        return None

def apri_score(ast_ul, uln_ast, platelets) -> Optional[float]:
    try:
        ast_ul, uln_ast, platelets = float(ast_ul), float(uln_ast), float(platelets)
        if uln_ast <= 0 or platelets <= 0:
            return None
        return (ast_ul / uln_ast) * 100.0 / platelets
    except Exception:
        return None

def nfs_score(age, bmi, diab_ifg, ast_ul, alt_ul, platelets, albumin_gdl) -> Optional[float]:
    try:
        age, bmi, diab_ifg, ast_ul, alt_ul, platelets, albumin_gdl = (
            float(age), float(bmi), int(diab_ifg), float(ast_ul), float(alt_ul), float(platelets), float(albumin_gdl)
        )
        if alt_ul <= 0:
            return None
        return -1.675 + 0.037 * age + 0.094 * bmi + 1.13 * diab_ifg + 0.99 * (ast_ul / alt_ul) - 0.013 * platelets - 0.66 * albumin_gdl
    except Exception:
        return None

def categorize_fib4(x: Optional[float]) -> Tuple[str, str]:
    if x is None:
        return "NA", "#cccccc"
    if x <= 1.3:
        return "Low (rules out advanced fibrosis)", "#2e7d32"
    if x < 2.67:
        return "Indeterminate", "#f9a825"
    return "High (advanced fibrosis likely)", "#c62828"

def categorize_apri(x: Optional[float]) -> Tuple[str, str]:
    if x is None:
        return "NA", "#cccccc"
    if x < 0.5:
        return "Low", "#2e7d32"
    if x < 1.0:
        return "Indeterminate", "#f9a825"
    return "High", "#c62828"

def categorize_nfs(x: Optional[float]) -> Tuple[str, str]:
    if x is None:
        return "NA", "#cccccc"
    if x < -1.455:
        return "Low", "#2e7d32"
    if x <= 0.675:
        return "Indeterminate", "#f9a825"
    return "High", "#c62828"

def subscore_fib4(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    if x <= 1.3:
        return 100.0
    if x < 2.67:
        return max(40.0, 100.0 - (x - 1.3) * (60.0 / (2.67 - 1.3)))
    return 20.0

def subscore_apri(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    if x <= 0.5:
        return 100.0
    if x <= 1.5:
        return max(60.0, 100.0 - (x - 0.5) * 40.0)
    if x <= 2.0:
        return max(20.0, 60.0 - (x - 1.5) * (40.0 / 0.5))
    return 20.0

def subscore_nfs(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    if x <= -1.455:
        return 100.0
    if x < 0.676:
        return 50.0
    return 20.0

def combine_liver_health(fib4_sub, apri_sub, nfs_sub) -> Optional[float]:
    if fib4_sub is None and apri_sub is None and nfs_sub is None:
        return None
    if nfs_sub is None:
        fib = fib4_sub or 0.0
        apr = apri_sub or 0.0
        return max(0.0, min(100.0, 0.7 * fib + 0.3 * apr))
    return max(0.0, min(100.0, 0.5 * (fib4_sub or 0.0) + 0.25 * (apri_sub or 0.0) + 0.25 * (nfs_sub or 0.0)))

def color_box(text: str, color: str):
    st.markdown(
        f"""
        <div style="background:{color};padding:12px;border-radius:8px;color:white;font-weight:600;">
            {text}
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---- Safety clamp for values coming from PDF/session ----

def _clamp(v, lo, hi):
    try:
        x = float(v)
    except Exception:
        return None
    if x < lo: return lo
    if x > hi: return hi
    return x


def sanitize_state():
    limits = {
        "tg": (10, 5000),
        "ggt": (1, 2000),
        "ast": (1, 5000),
        "alt": (1, 5000),
        "uln_ast": (5, 200),
        "platelets": (20, 1000),   # 10^9/L
        "albumin": (1, 6),
        "bmi": (10, 80),
        "waist": (40, 200),
        "age": (0, 120),
    }
    for k, (lo, hi) in limits.items():
        if k in st.session_state:
            v = _clamp(st.session_state[k], lo, hi)
            if v is not None:
                st.session_state[k] = v

# ---------- PDF Parsing (STRICT first, LOOSE fallback) ----------
STRICT_PATTERNS = {
    # Labs
    "ast_ul": r"(?:AST|SGOT)[^\n]{0,80}?(\d+(?:\.\d+)?)(?=[^\n]{0,20}(?:U/?L|IU/?L))",
    "alt_ul": r"(?:ALT|SGPT)[^\n]{0,80}?(\d+(?:\.\d+)?)(?=[^\n]{0,20}(?:U/?L|IU/?L))",
    "ggt_ul": r"(?:GGT|Gamma[\-\s]*glutamyl[\-\s]*transferase)[^\n]{0,80}?(\d+(?:\.\d+)?)(?=[^\n]{0,20}(?:U/?L|IU/?L))",
    "tg_mgdl": r"(?:Triglycerides?|TG)[^\n]{0,80}?(\d+(?:\.\d+)?)(?=[^\n]{0,20}mg/?dL)",
    "platelets": r"(?:Platelets?|Platelet\s*count)[^\n]{0,80}?(\d+(?:\.\d+)?)(?=[^\n]{0,30}(?:10\^9/?L|10\^3/?µ?L))",
    "albumin_gdl": r"(?:Albumin)[^\n]{0,80}?(\d+(?:\.\d+)?)(?=[^\n]{0,20}g/?[dD][lL])",
    "uln_ast": r"(?:AST|SGOT)[^\n]{0,80}?(?:ref(?:erence)?\s*range|range)[^\n]{0,40}?(\d{2,3})\s*(?:U/?L|IU/?L)?",
    # Demographics
    "name": r"(?:Patient\s*Name|Name)\s*[:\-]\s*([A-Za-z][A-Za-z\s\.\-']{1,60})",
    "sex": r"(?:Sex|Gender)\s*[:\-]\s*(Male|Female|M|F)",
    "age": r"(?:Age)\s*[:\-]\s*(\d{1,3})(?:\s*(?:years?|yrs?|y))?",
}

LOOSE_PATTERNS = {
    "ast_ul": r"(?:AST|SGOT)[^\d]{0,80}?(\d+(?:\.\d+)?)",
    "alt_ul": r"(?:ALT|SGPT)[^\d]{0,80}?(\d+(?:\.\d+)?)",
    "ggt_ul": r"(?:GGT|Gamma[\-\s]*glutamyl[\-\s]*transferase)[^\d]{0,80}?(\d+(?:\.\d+)?)",
    "tg_mgdl": r"(?:Triglycerides?|TG)[^\d]{0,80}?(\d+(?:\.\d+)?)",
    "platelets": r"(?:Platelets?|Platelet\s*count)[^\d]{0,80}?(\d+(?:\.\d+)?)",
    "albumin_gdl": r"(?:Albumin)[^\d]{0,80}?(\d+(?:\.\d+)?)",
    "uln_ast": r"(?:AST|SGOT)[^\n]{0,80}?(?:ref(?:erence)?\s*range|range)[^\n]{0,40}?(\d{2,3})",
    # Demographics (looser)
    "name": r"(?:Patient\s*Name|Name)[^\n]{0,20}([A-Za-z][A-Za-z\s\.\-']{1,60})",
    "sex": r"(?:Sex|Gender)[^\n]{0,20}(Male|Female|M|F)",
    "age": r"(?:Age)[^\d]{0,20}(\d{1,3})",
}


def parse_pdf_bytes_return_text(pdf_bytes) -> Tuple[Dict[str, float], str]:
    out: Dict[str, float] = {}
    full = ""
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            texts = [page.extract_text() or "" for page in pdf.pages]
            full = "\n".join(texts)
    except Exception:
        return out, full

    text = re.sub(r"[^\S\r\n]+", " ", full, flags=re.M).replace("\u00b5", "µ")

    # --- ULN AST: prefer capturing the upper value from a range like "3 - 50" on the AST line ---
    if "uln_ast" not in out:
        ULN_RANGE_PATTERNS = [
            r"(?:AST|SGOT)[^\n]*?U/?L[^\n]*?(\d{1,3})\s*[-–‐]\s*(\d{2,3})",  # ...U/L ... 3 - 50
            r"(?:AST|SGOT)[^\n]*?(?:ref(?:erence)?\s*(?:range|interval)|bio\.?\s*ref.*?|range)[^\n]*?(\d{1,3})\s*[-–‐]\s*(\d{2,3})"
        ]
        for pat in ULN_RANGE_PATTERNS:
            m = re.search(pat, text, flags=re.I)
            if m:
                lo_v, hi_v = int(m.group(1)), int(m.group(2))
                out["uln_ast"] = float(max(lo_v, hi_v))
                break

    def search_and_set(key):
        # Don't overwrite uln_ast if we already captured a range
        if key == "uln_ast" and "uln_ast" in out:
            return
        m = re.search(STRICT_PATTERNS[key], text, flags=re.I)
        if not m:
            m = re.search(LOOSE_PATTERNS[key], text, flags=re.I)
        if m:
            try:
                out[key] = m.group(1).strip()
            except Exception:
                pass

    for k in STRICT_PATTERNS.keys():
        search_and_set(k)

    # Albumin g/L → g/dL if unit nearby indicates g/L
    if "albumin_gdl" in out:
        try:
            out["albumin_gdl"] = float(out["albumin_gdl"])
        except Exception:
            out["albumin_gdl"] = None
        m = re.search(r"Albumin[^\n]{0,40}?(\d+(?:\.\d+)?)\s*(g/?dL|g/?L)", text, flags=re.I)
        if m and "g/L" in m.group(2).replace(" ", "").lower():
            if isinstance(out["albumin_gdl"], (int, float)):
                out["albumin_gdl"] = out["albumin_gdl"] / 10.0

    # Cast numeric fields that need to be float
    for num_key in ["ast_ul", "alt_ul", "ggt_ul", "tg_mgdl", "platelets", "albumin_gdl", "uln_ast", "age"]:
        if num_key in out:
            try:
                out[num_key] = float(out[num_key])
            except Exception:
                out[num_key] = None

    # Normalize sex to M/F
    if "sex" in out:
        s = str(out["sex"]).strip().upper()
        out["sex"] = "F" if s.startswith("F") else ("M" if s.startswith("M") else s)

    return out, full

# ---------- App UI ----------
st.title("Liver Health Assessment Tool — Validated Option A (with PDF)")
st.caption("Uses FLI (steatosis screening) and fibrosis scores (FIB-4, APRI, NFS). Liver Health 0–100 is based on fibrosis only.")

with st.expander("Upload Lab PDF (beta: text-based PDFs only)"):
    if not PDF_ENABLED:
        st.warning("PDF parsing requires 'pdfplumber'. Add it to requirements.txt.")
    else:
        up = st.file_uploader("Upload a lab PDF (text-based, not scanned)", type=["pdf"])
        if up is not None:
            data, raw_text = parse_pdf_bytes_return_text(up)
            if data:
                st.success("Parsed these fields from the PDF (you can edit below):")
                # Round numeric values only for display
                preview = {}
                for k, v in data.items():
                    if isinstance(v, (int, float)) and v is not None:
                        preview[k] = round(float(v), 3)
                    else:
                        preview[k] = v
                st.json(preview)
                mapping = {"ast_ul": "ast", "alt_ul": "alt", "ggt_ul": "ggt", "tg_mgdl": "tg",
                           "platelets": "platelets", "albumin_gdl": "albumin", "uln_ast": "uln_ast",
                           "age": "age", "sex": "sex", "name": "name"}
                for key, val in data.items():
                    if key in mapping:
                        st.session_state[mapping[key]] = val
                # clamp to safe ranges to prevent widget errors
                sanitize_state()
            else:
                st.info("Couldn't read values. If the PDF is scanned, please type manually.")

            with st.expander("Debug: show raw extracted text (optional)"):
                st.text(raw_text if raw_text else "No text extracted.")

with st.expander("Single-Patient Assessment", expanded=True):
    col0, col1, col2, col3 = st.columns(4)
    with col0:
        name = st.text_input("Patient Name", value=st.session_state.get("name", ""), key="name")
        sex = st.selectbox("Sex", ["M", "F"], index=(0 if st.session_state.get("sex", "M") == "M" else 1), key="sex")
        age = st.number_input("Age (years)", min_value=0, max_value=120, value=int(st.session_state.get("age", 40)), step=1, key="age")
    with col1:
        bmi = st.number_input("BMI (kg/m²)", min_value=10.0, max_value=80.0, value=float(st.session_state.get("bmi", 27.0)), step=0.1, key="bmi")
        waist_cm = st.number_input("Waist circumference (cm)", min_value=40.0, max_value=200.0, value=float(st.session_state.get("waist", 95.0)), step=0.5, key="waist")
    with col2:
        tg = st.number_input("Triglycerides (mg/dL)", min_value=10.0, max_value=2000.0, value=float(st.session_state.get("tg", 160.0)), step=1.0, key="tg")
        ggt = st.number_input("GGT (U/L)", min_value=1.0, max_value=2000.0, value=float(st.session_state.get("ggt", 45.0)), step=1.0, key="ggt")
        ast = st.number_input("AST (U/L)", min_value=1.0, max_value=5000.0, value=float(st.session_state.get("ast", 35.0)), step=0.5, key="ast")
        alt = st.number_input("ALT (U/L)", min_value=1.0, max_value=5000.0, value=float(st.session_state.get("alt", 30.0)), step=0.5, key="alt")
    with col3:
        uln_ast = st.number_input("ULN AST (U/L)", min_value=10.0, max_value=100.0, value=float(st.session_state.get("uln_ast", 40.0)), step=1.0,
                                   help="Upper limit of normal for your lab", key="uln_ast")
        platelets = st.number_input("Platelets (10⁹/L)", min_value=20.0, max_value=1000.0, value=float(st.session_state.get("platelets", 230.0)), step=1.0, key="platelets")
        albumin = st.number_input("Albumin (g/dL)", min_value=1.0, max_value=6.0, value=float(st.session_state.get("albumin", 4.2)), step=0.1, key="albumin")
        diab_ifg = st.selectbox("Diabetes / IFG", ["No", "Yes"], index=(1 if str(st.session_state.get("diab", "No")) in ["1", "Yes"] else 0), key="diab")

    diab_flag = 1 if diab_ifg == "Yes" else 0

    pdf_bytes = None

    if st.button("Calculate"):
        fli = fli_score(tg, bmi, ggt, waist_cm)
        fli_cat, fli_act, fli_color = fli_category_action(fli)

        fib4 = fib4_score(age, ast, alt, platelets)
        fib4_cat, fib4_color = categorize_fib4(fib4)
        apri = apri_score(ast, uln_ast, platelets)
        apri_cat, apri_color = categorize_apri(apri)
        nfs = nfs_score(age, bmi, diab_flag, ast, alt, platelets, albumin)
        nfs_cat, nfs_color = categorize_nfs(nfs)

        fib4_sub = subscore_fib4(fib4)
        apri_sub = subscore_apri(apri)
        nfs_sub = subscore_nfs(nfs)
        liver100 = combine_liver_health(fib4_sub, apri_sub, nfs_sub)

        st.subheader("Results")

        st.markdown("**Patient**")
        st.write(f"Name: {name or '—'}  |  Sex: {sex}  |  Age: {age} years")

        st.markdown("**Step 1 — Fatty Liver (Steatosis) Screening (FLI)**")
        color_box(f"FLI: {fli:.1f}  •  {fli_cat}", fli_color) if fli is not None else st.info("Insufficient inputs for FLI.")
        st.caption(f"Action: {fli_act}") if fli_act else None

        st.markdown("---")
        st.markdown("**Step 2 — Fibrosis Staging (FIB-4, APRI, NFS)**")
        c1, c2, c3 = st.columns(3)
        with c1:
            color_box(f"FIB-4: {fib4:.3f}  •  {fib4_cat}", fib4_color) if fib4 is not None else st.info("FIB-4: insufficient inputs.")
        with c2:
            color_box(f"APRI: {apri:.3f}  •  {apri_cat}", apri_color) if apri is not None else st.info("APRI: insufficient inputs.")
        with c3:
            color_box(f"NFS: {nfs:.3f}  •  {nfs_cat}", nfs_color) if nfs is not None else st.info("NFS: insufficient inputs.")

        st.markdown("---")
        st.markdown("**Fibrosis-based Liver Health Score (0–100; higher is better)**")
        l_text = None
        if liver100 is not None:
            if liver100 >= 85:
                l_color = "#2e7d32"
                l_text = "Low probability of advanced fibrosis — routine monitoring."
            elif liver100 >= 60:
                l_color = "#f9a825"
                l_text = "Indeterminate probability — consider elastography (FibroScan)."
            else:
                l_color = "#c62828"
                l_text = "High probability of advanced fibrosis — hepatology referral, imaging/workup."
            color_box(f"Liver Health: {liver100:.1f} / 100  •  {l_text}", l_color)
        else:
            st.info("Insufficient inputs to compute the fibrosis-based Liver Health Score.")

        # ---------- Build PDF ----------
        if REPORTLAB_ENABLED:
            buf = io.BytesIO()
            doc = SimpleDocTemplate(buf, pagesize=A4, title="Liver Health Report")
            styles = getSampleStyleSheet()
            story = []

            title = f"<b>Liver Health Report</b>"
            story.append(Paragraph(title, styles["Title"]))
            story.append(Spacer(1, 8))

            pinfo = f"<b>Patient:</b> {name or '—'} &nbsp;&nbsp; <b>Sex:</b> {sex} &nbsp;&nbsp; <b>Age:</b> {int(age)} years"
            story.append(Paragraph(pinfo, styles["Normal"]))
            story.append(Spacer(1, 10))

            # Table of results
            rows = [["Metric", "Value", "Interpretation / Action"]]
            rows.append(["FLI", f"{fli:.1f}" if fli is not None else "—", (f"{fli_cat}. {fli_act}" if fli is not None else "Insufficient inputs")])
            rows.append(["FIB-4", f"{fib4:.3f}" if fib4 is not None else "—", (
                "Advanced fibrosis unlikely; routine monitoring." if fib4 is not None and fib4 <= 1.3 else (
                "Indeterminate; consider elastography (FibroScan)." if fib4 is not None and fib4 < 2.67 else (
                "Advanced fibrosis likely; refer to hepatology." if fib4 is not None else "Insufficient inputs"
            ))])
            rows.append(["APRI", f"{apri:.3f}" if apri is not None else "—", (
                "Significant fibrosis unlikely." if apri is not None and apri < 0.5 else (
                "Indeterminate; consider elastography / repeat testing." if apri is not None and apri < 1.0 else (
                "Advanced fibrosis likely; specialist referral." if apri is not None else "Insufficient inputs"
            ))])
            rows.append(["NFS", f"{nfs:.3f}" if nfs is not None else "—", (
                "Advanced fibrosis unlikely." if nfs is not None and nfs < -1.455 else (
                "Indeterminate; consider elastography / specialist assessment." if nfs is not None and nfs <= 0.675 else (
                "Advanced fibrosis likely; specialist referral." if nfs is not None else "Insufficient inputs"
            ))])
            rows.append(["Liver Health (0–100)", f"{liver100:.1f}" if liver100 is not None else "—", (l_text or "Insufficient inputs")])

            tbl = Table(rows, hAlign='LEFT', colWidths=[130, 100, 260])
            tbl.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#eeeeee')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.black),
                ('GRID', (0,0), (-1,-1), 0.25, colors.grey),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#fafafa')]),
            ]))
            story.append(tbl)

            story.append(Spacer(1, 12))
            story.append(Paragraph("<b>Disclaimer:</b> This report is for screening and educational purposes only and is not a substitute for professional medical advice.", styles['Italic']))

            doc.build(story)
            pdf_bytes = buf.getvalue()
            buf.close()
        else:
            st.error("reportlab is not available. Please ensure it's added to requirements.txt.")

        if pdf_bytes:
            st.download_button(
                "Download PDF Report",
                data=pdf_bytes,
                file_name=f"liver_health_report_{(name or 'patient').replace(' ', '_')}.pdf",
                mime="application/pdf",
            )

with st.expander("Batch Processing (CSV upload)"):
    st.markdown("Template columns (case-insensitive): **name, age, sex, bmi, waist_cm, tg_mgdl, ggt_ul, ast_ul, alt_ul, uln_ast, platelets, albumin_gdl, diab_ifg**")
    file = st.file_uploader("Upload CSV", type=["csv"], key="csvu")
    if file is not None:
        df = pd.read_csv(file)
        rename_map = {
            'name': 'name', 'patientname': 'name',
            'age': 'age', 'sex': 'sex', 'bmi': 'bmi', 'waist_cm': 'waist_cm', 'waist': 'waist_cm',
            'tg_mgdl': 'tg_mgdl', 'tg': 'tg_mgdl', 'triglycerides': 'tg_mgdl',
            'ggt_ul': 'ggt_ul', 'ggt': 'ggt_ul',
            'ast_ul': 'ast_ul', 'ast': 'ast_ul',
            'alt_ul': 'alt_ul', 'alt': 'alt_ul',
            'uln_ast': 'uln_ast',
            'platelets': 'platelets',
            'albumin_gdl': 'albumin_gdl', 'albumin': 'albumin_gdl',
            'diab_ifg': 'diab_ifg', 'diabetes': 'diab_ifg'
        }
        df = df.rename(columns={c: rename_map.get(str(c).strip().lower(), c) for c in df.columns})

        results = []
        for _, r in df.iterrows():
            try:
                age = float(r.get("age", float("nan")))
                bmi = float(r.get("bmi", float("nan")))
                waist = float(r.get("waist_cm", float("nan")))
                tg = float(r.get("tg_mgdl", float("nan")))
                ggt = float(r.get("ggt_ul", float("nan")))
                ast = float(r.get("ast_ul", float("nan")))
                alt = float(r.get("alt_ul", float("nan")))
                uln = float(r.get("uln_ast", 40.0)) if pd.notna(r.get("uln_ast")) else 40.0
                plate = float(r.get("platelets", 250.0)) if pd.notna(r.get("platelets")) else 250.0
                alb = float(r.get("albumin_gdl", float("nan"))) if pd.notna(r.get("albumin_gdl")) else None
                diab = int(r.get("diab_ifg", 0)) if pd.notna(r.get("diab_ifg")) else 0
            except Exception:
                age = bmi = waist = tg = ggt = ast = alt = uln = plate = alb = None
                diab = 0

            fli = fli_score(tg, bmi, ggt, waist)
            fli_cat, fli_act, _ = fli_category_action(fli)
            fib4 = fib4_score(age, ast, alt, plate)
            apri = apri_score(ast, uln, plate)
            nfs = nfs_score(age, bmi, diab, ast, alt, plate, alb) if alb is not None else None
            fib4_sub = subscore_fib4(fib4)
            apri_sub = subscore_apri(apri)
            nfs_sub = subscore_nfs(nfs) if nfs is not None else None
            liver100 = combine_liver_health(fib4_sub, apri_sub, nfs_sub)

            results.append({
                "name": r.get("name"), "age": r.get("age"), "sex": r.get("sex"), "bmi": r.get("bmi"), "waist_cm": r.get("waist_cm"),
                "tg_mgdl": r.get("tg_mgdl"), "ggt_ul": r.get("ggt_ul"),
                "ast_ul": r.get("ast_ul"), "alt_ul": r.get("alt_ul"), "uln_ast": r.get("uln_ast"),
                "platelets": r.get("platelets"), "albumin_gdl": r.get("albumin_gdl"), "diab_ifg": r.get("diab_ifg"),
                "FLI": None if fli is None else round(fli, 1), "FLI_category": fli_cat, "FLI_action": fli_act,
                "FIB4": None if fib4 is None else round(fib4, 3),
                "APRI": None if apri is None else round(apri, 3),
                "NFS": None if nfs is None else round(nfs, 3),
                "LiverHealth100": None if liver100 is None else round(liver100, 1)
            })
        out = pd.DataFrame(results)
        st.dataframe(out, use_container_width=True)
        st.download_button("Download results CSV", data=out.to_csv(index=False).encode("utf-8"),
                           file_name="nafld_results.csv", mime="text/csv")

st.caption("Disclaimer: For screening and educational purposes only. Not a substitute for professional medical advice.")
