
import os, pathlib
import pandas as pd
import numpy as np
import streamlit as st

st.set_page_config(page_title="Hyd Realestate — Unified", layout="wide")

# ---------- Data source selection (default + overrides) ----------
try:
    params = st.query_params  # Streamlit >=1.30
except AttributeError:
    params = st.experimental_get_query_params()

param_data = None
if isinstance(params, dict) and "data" in params:
    param_data = params["data"][0] if isinstance(params["data"], list) else params["data"]

DATA_CANDIDATES = [
    (os.getenv("HYD_DATA_PATH") or "").strip(),
    (param_data or "").strip(),
    "PROPERTY_LIST_UNIFIED_REFINED.xlsx",
    "PROPERTY_LIST_UNIFIED.xlsx",
    "PROPERTY_LIST_ENRICHED.xlsx",
]

DATA_CANDIDATES = [p for p in DATA_CANDIDATES if p]
DEFAULT_DATA_PATH = next((p for p in DATA_CANDIDATES if p and os.path.exists(p)), None)

def load_df(obj):
    name = getattr(obj, "name", str(obj))
    if isinstance(obj, str):
        p = obj
        if p.lower().endswith(".csv"):
            return pd.read_csv(p)
        return pd.read_excel(p)
    else:
        if name.lower().endswith(".csv"):
            return pd.read_csv(obj)
        return pd.read_excel(obj)

st.title("📊 Hyderabad Commercial Real Estate — Unified Explorer")

uploaded = st.file_uploader("Upload dataset (.xlsx or .csv)", type=["xlsx", "csv"])

if uploaded is not None:
    df = load_df(uploaded)
    data_src = f"uploaded: {uploaded.name}"
elif DEFAULT_DATA_PATH:
    df = load_df(DEFAULT_DATA_PATH)
    data_src = f"default: {pathlib.Path(DEFAULT_DATA_PATH).name}"
else:
    st.warning("No data source found. Upload a file or set HYD_DATA_PATH / ?data=...")
    st.stop()

# ---------- Derived/repair helpers (lightweight; main cleaning already done) ----------
def coerce_num(s):
    return pd.to_numeric(s, errors="coerce")

for col in ["Area_Sft","Price_INR","Rent_INR","Gross_Yield_%","Price_per_Sft","Rent_per_Sft","DealScore_%","EMI_8p5_20y_INR","EMI_Lakh","Net_Cashflow_INR","Net_Cashflow_Lakh","DSCR","Price_Crore","Rent_Lakh"]:
    if col in df.columns:
        df[col] = coerce_num(df[col])

df["Price_Crore"] = df.get("Price_Crore", df.get("Price_INR", np.nan)/1e7)
df["Rent_Lakh"]   = df.get("Rent_Lakh", df.get("Rent_INR", np.nan)/1e5)

st.sidebar.markdown(f"**Data source:** `{data_src}`")
st.sidebar.markdown(f"**Rows:** {len(df)}")

# ---------- Tabs ----------
tab_browse, tab_best, tab_loan, tab_info = st.tabs(["Browse", "Best Deals", "Loan & Cashflow", "Info"])

with tab_browse:
    st.subheader("Browse & Filter")
    # Basic filters
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        locs = sorted(list({str(x).strip() for x in df.get("Location", pd.Series(dtype=str)).dropna()}))
        sel_locs = st.multiselect("Locations", locs, default=[])
    with col2:
        area_min, area_max = st.slider("Area (sft)", 0, int(np.nanmax(df["Area_Sft"]) if "Area_Sft" in df else 50000), (0, int(np.nanmax(df["Area_Sft"]) if "Area_Sft" in df else 50000)))
    with col3:
        price_min_cr, price_max_cr = st.slider("Price (Cr)", 0.0, float(np.nanmax(df["Price_Crore"]) if "Price_Crore" in df else 10.0), (0.0, float(np.nanmax(df["Price_Crore"]) if "Price_Crore" in df else 10.0)))
    with col4:
        rent_min_l, rent_max_l = st.slider("Rent (Lakh/mo)", 0.0, float(np.nanmax(df["Rent_Lakh"]) if "Rent_Lakh" in df else 10.0), (0.0, float(np.nanmax(df["Rent_Lakh"]) if "Rent_Lakh" in df else 10.0)))

    col5, col6, col7 = st.columns(3)
    with col5:
        yield_min = st.slider("Min Gross Yield %", 0.0, 25.0, 0.0)
    with col6:
        dealscore_min = st.slider("Min DealScore %", 0.0, 100.0, 0.0)
    with col7:
        show_cols = st.multiselect("Columns", options=list(df.columns), default=["Location","Tenant","Area_Sft","Price_Crore","Rent_Lakh","Price_per_Sft","Rent_per_Sft","Gross_Yield_%","DealScore_%"])

    q = df.copy()
    if sel_locs:
        q = q[q["Location"].astype(str).isin(sel_locs)]
    if "Area_Sft" in q:
        q = q[(q["Area_Sft"].fillna(0) >= area_min) & (q["Area_Sft"].fillna(0) <= area_max)]
    if "Price_Crore" in q:
        q = q[(q["Price_Crore"].fillna(0.0) >= price_min_cr) & (q["Price_Crore"].fillna(0.0) <= price_max_cr)]
    if "Rent_Lakh" in q:
        q = q[(q["Rent_Lakh"].fillna(0.0) >= rent_min_l) & (q["Rent_Lakh"].fillna(0.0) <= rent_max_l)]
    if "Gross_Yield_%" in q.columns:
        q = q[q["Gross_Yield_%"].fillna(0.0) >= yield_min]
    if "DealScore_%" in q.columns:
        q = q[q["DealScore_%"].fillna(0.0) >= dealscore_min]

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    with k1: st.metric("Listings", len(q))
    with k2: st.metric("Median Yield %", f"{np.nanmedian(q['Gross_Yield_%']) if 'Gross_Yield_%' in q else np.nan:.1f}")
    with k3: st.metric("Median Price/sft", f"{np.nanmedian(q['Price_per_Sft']) if 'Price_per_Sft' in q else np.nan:,.0f}")
    with k4: st.metric("Median Rent/sft",  f"{np.nanmedian(q['Rent_per_Sft']) if 'Rent_per_Sft' in q else np.nan:,.1f}")

    st.dataframe(q[show_cols].sort_values(by="DealScore_%", ascending=False, na_position="last"), use_container_width=True)

with tab_best:
    st.subheader("Best Deals")
    topn = st.slider("Top N by DealScore %", 5, 50, 15)
    cols = ["Location","Tenant","Area_Sft","Price_Crore","Rent_Lakh","Gross_Yield_%","Price_per_Sft","Rent_per_Sft","DealScore_%","DSCR","Net_Cashflow_Lakh"]
    exist = [c for c in cols if c in df.columns]
    qq = df[exist].sort_values(by="DealScore_%", ascending=False, na_position="last").head(topn)
    st.dataframe(qq, use_container_width=True)

with tab_loan:
    st.subheader("Loan & Cashflow Simulator")
    c1, c2, c3 = st.columns(3)
    with c1: dp = st.number_input("Downpayment %", 0.0, 90.0, 20.0, 1.0)
    with c2: rate = st.number_input("Interest % (annual)", 0.0, 20.0, 8.5, 0.1)
    with c3: years = st.number_input("Tenure (years)", 1, 40, 20, 1)

    def emi(p, r_annual, years):
        r = r_annual/1200.0
        n = int(years*12)
        if p <= 0 or r <= 0 or n <= 0: return 0.0
        return p * r * (1+r)**n / ((1+r)**n - 1)

    st.markdown("Pick a row to simulate (by index):")
    idx = st.number_input("Row index", 0, max(0, len(df)-1), 0)
    if 0 <= idx < len(df):
        row = df.iloc[int(idx)]
        price = float(row.get("Price_INR", np.nan))
        rent  = float(row.get("Rent_INR", np.nan))
        if np.isnan(price) or np.isnan(rent):
            st.warning("Selected row missing Price or Rent.")
        else:
            loan_amt = price * (1.0 - dp/100.0)
            emi_val  = emi(loan_amt, rate, years)
            net_cf   = rent - emi_val
            st.metric("Loan Amount (₹)", f"{loan_amt:,.0f}")
            st.metric("EMI (₹/mo)", f"{emi_val:,.0f}")
            st.metric("Net Cashflow (₹/mo)", f"{net_cf:,.0f}")
            st.metric("DSCR", f"{(rent/emi_val) if emi_val>0 else np.nan:.2f}")

with tab_info:
    st.markdown("### About")
    st.write("This explorer uses the **Unified Refined** dataset with normalized units (Price/Rent/Area), computed KPIs (Yield, PPSF, RPSF), DealScore%, and a loan simulator. Use `?data=...` or `HYD_DATA_PATH` to point to a custom file.")
