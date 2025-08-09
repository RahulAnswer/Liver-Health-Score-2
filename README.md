Liver Health Assessment (Validated Option A)
Screen for steatosis and stage fibrosis using validated, guideline-aligned indices.
This app keeps FLI (fatty liver) separate from fibrosis scores (FIB-4, APRI, NFS) and reports a fibrosis-based Liver Health Score (0–100; higher = better).

Live app: add your Streamlit URL here
Demo CSV: optional link

Features
Single patient: enter values → instant results with green/yellow/red bands.

PDF upload (beta): extract AST/ALT, GGT, TG, Platelets, Albumin, ULN-AST from text-based lab PDFs.

Batch mode: upload CSV, view table, download results.

Evidence-aligned: FLI for steatosis; fibrosis risk via FIB-4/APRI/NFS only.

How to run
Local:

bash
Copy
Edit
pip install -r requirements.txt
streamlit run nafld_streamlit_app.py
Streamlit Cloud:

Push this repo (must include nafld_streamlit_app.py and requirements.txt).

In Streamlit Cloud → New app → select repo/branch → Main file: nafld_streamlit_app.py → Deploy.

Inputs
Age, Sex, BMI, Waist, TG (mg/dL), GGT (U/L), AST (U/L), ALT (U/L), ULN_AST, Platelets (×10⁹/L), Albumin (g/dL), Diabetes/IFG (Y/N).

Outputs
FLI with category + action

FIB-4, APRI, NFS with categories

Liver Health Score (0–100) + interpretation

CSV results for batch uploads

Notes
PDF parsing works for text PDFs; scanned images may require manual entry.

ULN_ALT isn’t used; ULN_AST is required for APRI (default by lab).

Disclaimer: For screening and educational use only. Not a diagnostic device. Use clinical judgment, local lab ranges, and confirmatory testing (e.g., elastography) as indicated.
