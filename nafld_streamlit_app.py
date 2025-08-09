import math
import re
from typing import Optional, Tuple, Dict

import streamlit as st
import pandas as pd

# NEW: PDF parsing
try:
    import pdfplumber
    PDF_ENABLED = True
except Exception:
    PDF_ENABLED = False

st.set_page_config(page_title="Liver Health Assessment (Validated Option A)", layout="wide")

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
    if None in (ln_tg, ln_ggt) or tg_mgdl in (None,"") or bmi in (None,"") or waist_cm in (None,""):
        return None
    L = 0.953*ln_tg + 0.139*float(bmi) + 0.718*ln_ggt + 0.053*float(waist_cm) - 15.745
    f = (math.exp(L) / (1 + math.exp(L))) * 100.0
    return max(0.0, min(100.0, f))

def fli_category_action(fli: Optional[float]) -> Tuple[Optional[str], Optional[str], str]:
    # returns (category, action, color)
    if fli is None:
        return None, None, "#cccccc"
    if fli < 30:
        return "Low (fatty liver unlikely)", "Maintain lifestyle; periodic monitoring.", "#2e7d32"  # green
    if fli < 60:
        return "Intermediate (cannot rule in/out)", "Consider ultrasound or repeat after lifestyle optimisation.", "#f9a825"  # yellow
    return "High (fatty liver likely)", "Proceed to fibrosis staging (NFS, FIB-4, APRI).", "#c62828"  # red

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
        return -1.675 + 0.037*age + 0.094*bmi + 1.13*diab_ifg + 0.99*(ast_ul/alt_ul) - 0.013*platelets - 0.66*albumin_gdl
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
        return max(0.0, min(100.0, 0.7*fib + 0.3*apr))
    return max(0.0, min(100.0, 0.5*(fib4_sub or 0.0) + 0.25*(apri_sub or 0.0) + 0.25*(nfs_sub or 0.0)))

def color_box(text: str, color: str):
    st.markdown(f"""
        <div style="background:{color};padding:12px;border-radius:8px;color:white;font-weight:600;">
            {text}
        </div>
    """, unsafe_allow_html=True)

# ---------- PDF Parsing (beta) ----------

LAB_PATTERNS = {
    "ast_ul": r"(?:AST|SGOT)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:U/?L|IU/?L)",
    "alt_ul": r"(?:ALT|SGPT)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:U/?L|IU/?L)",
    "ggt_ul": r"(?:GGT|Gamma[\-\s]*glutamyl[\-\s]*transferase)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:U/?L|IU/?L)",
    "tg_mgdl": r"(?:Triglycerides?|TG)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*mg/?dL",
    "platelets": r"(?:Platelets?|Platelet\s*count)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:x?\s*10\^?3\s*/\s*µ?L|10\^9/L|10\^9\s*/\s*L|10\^3/µL|10\^3/uL|10\^3/\s*µL|10\^3/\s*uL|10\^3\s*\/\s*µ?L)?",
    "albumin_gdl": r"(?:Albumin)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:g/?dL|g/?L)",
    "uln_ast": r"(?:AST|SGOT)[^\n]{0,30}?(?:reference|ref\.?\s*range|range)[^\n]{0,20}?(\d{2,3})\s*(?:U/?L|IU/?L)"
}

def parse_pdf_bytes(pdf_bytes) -> Dict[str, float]:
    out: Dict[str, float] = {}
    try:
        import pdfplumber
        with pdfplumber.open(pdf_bytes) as pdf:
            texts = []
            for page in pdf.pages:
                texts.append(page.extract_text() or "")
            full = "\n".join(texts)
    except Exception:
        return out

    # normalize
    text = re.sub(r"[^\S\r\n]+", " ", full, flags=re.M)  # collapse spaces
    text = text.replace("\u00b5", "µ")

    def search_and_set(key, pattern):
        m = re.search(pattern, text, flags=re.I)
        if m:
            try:
                val = float(m.group(1))
                out[key] = val
            except Exception:
                pass

    for k, pattern in LAB_PATTERNS.items():
        search_and_set(k, pattern)

    # Unit adjustments
    if "albumin_gdl" in out:
        m = re.search(r"Albumin[^\n]{0,20}?([0-9]+(?:\.[0-9]+)?)\s*(g/?dL|g/?L)", text, flags=re.I)
        if m and "g/L" in m.group(2).replace(" ", "").lower():
            out["albumin_gdl"] = out["albumin_gdl"] / 10.0

    return out

# ---------- App UI ----------

st.title("Liver Health Assessment Tool — Validated Option A")
st.caption("Uses FLI (steatosis screening) and fibrosis scores (FIB-4, APRI, NFS). Liver Health 0–100 is based on fibrosis only.")

with st.expander("Upload Lab PDF (beta: text-based PDFs only)"):
    if not PDF_ENABLED:
        st.warning("PDF parsing requires 'pdfplumber'. Add it to requirements.txt.")
    else:
        up = st.file_uploader("Upload a lab PDF (text-based, not scanned)", type=["pdf"])
        if up is not None:
            data = parse_pdf_bytes(up)
            if data:
                st.success("Parsed these fields from the PDF (you can edit below):")
                st.json({k: round(v, 3) for k, v in data.items()})
                # prefill session state so the form below picks them up
                for key, val in data.items():
                    mapping = {"ast_ul":"ast","alt_ul":"alt","ggt_ul":"ggt","tg_mgdl":"tg","platelets":"platelets","albumin_gdl":"albumin","uln_ast":"uln_ast"}
                    if key in mapping:
                        st.session_state[mapping[key]] = float(val)
            else:
                st.info("Couldn't read values. If the PDF is scanned, please type manually.")

with st.expander("Single-Patient Assessment", expanded=True):
    col1, col2, col3 = st.columns(3)
    with col1:
        age = st.number_input("Age (years)", min_value=0, max_value=120, value=40, step=1, key="age")
        sex = st.selectbox("Sex", ["M", "F"], key="sex")
        bmi = st.number_input("BMI (kg/m²)", min_value=10.0, max_value=80.0, value=27.0, step=0.1, key="bmi")
        waist_cm = st.number_input("Waist circumference (cm)", min_value=40.0, max_value=200.0, value=95.0, step=0.5, key="waist")
    with col2:
        tg = st.number_input("Triglycerides (mg/dL)", min_value=10.0, max_value=2000.0, value=160.0, step=1.0, key="tg")
        ggt = st.number_input("GGT (U/L)", min_value=1.0, max_value=2000.0, value=45.0, step=1.0, key="ggt")
        ast = st.number_input("AST (U/L)", min_value=1.0, max_value=5000.0, value=35.0, step=0.5, key="ast")
        alt = st.number_input("ALT (U/L)", min_value=1.0, max_value=5000.0, value=30.0, step=0.5, key="alt")
    with col3:
        uln_ast = st.number_input("ULN AST (U/L)", min_value=10.0, max_value=100.0, value=40.0, step=1.0, help="Upper limit of normal for your lab", key="uln_ast")
        platelets = st.number_input("Platelets (10⁹/L)", min_value=20.0, max_value=1000.0, value=230.0, step=1.0, key="platelets")
        albumin = st.number_input("Albumin (g/dL)", min_value=1.0, max_value=6.0, value=4.2, step=0.1, key="albumin")
        diab_ifg = st.selectbox("Diabetes / IFG", ["No", "Yes"], key="diab")

    diab_flag = 1 if diab_ifg == "Yes" else 0

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

with st.expander("Batch Processing (CSV upload)"):
    st.markdown("Template columns (case-insensitive): **age, sex, bmi, waist_cm, tg_mgdl, ggt_ul, ast_ul, alt_ul, uln_ast, platelets, albumin_gdl, diab_ifg**")
    file = st.file_uploader("Upload CSV", type=["csv"], key="csvu")
    if file is not None:
        df = pd.read_csv(file)
        # Normalize columns
        rename_map = {
            'age':'age', 'sex':'sex', 'bmi':'bmi', 'waist_cm':'waist_cm', 'waist':'waist_cm',
            'tg_mgdl':'tg_mgdl', 'tg':'tg_mgdl', 'triglycerides':'tg_mgdl',
            'ggt_ul':'ggt_ul', 'ggt':'ggt_ul',
            'ast_ul':'ast_ul', 'ast':'ast_ul',
            'alt_ul':'alt_ul', 'alt':'alt_ul',
            'uln_ast':'uln_ast',
            'platelets':'platelets',
            'albumin_gdl':'albumin_gdl', 'albumin':'albumin_gdl',
            'diab_ifg':'diab_ifg', 'diabetes':'diab_ifg'
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
                age=bmi=waist=tg=ggt=ast=alt=uln=plate=alb=None
                diab=0

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
                "age": r.get("age"), "sex": r.get("sex"), "bmi": r.get("bmi"), "waist_cm": r.get("waist_cm"),
                "tg_mgdl": r.get("tg_mgdl"), "ggt_ul": r.get("ggt_ul"),
                "ast_ul": r.get("ast_ul"), "alt_ul": r.get("alt_ul"), "uln_ast": r.get("uln_ast"),
                "platelets": r.get("platelets"), "albumin_gdl": r.get("albumin_gdl"), "diab_ifg": r.get("diab_ifg"),
                "FLI": None if fli is None else round(fli,1), "FLI_category": fli_cat, "FLI_action": fli_act,
                "FIB4": None if fib4 is None else round(fib4,3),
                "APRI": None if apri is None else round(apri,3),
                "NFS": None if nfs is None else round(nfs,3),
                "LiverHealth100": None if liver100 is None else round(liver100,1)
            })
        out = pd.DataFrame(results)
        st.dataframe(out, use_container_width=True)
        st.download_button("Download results CSV", data=out.to_csv(index=False).encode("utf-8"), file_name="nafld_results.csv", mime="text/csv")

st.caption("Disclaimer: For screening and educational purposes only. Not a substitute for professional medical advice.")
