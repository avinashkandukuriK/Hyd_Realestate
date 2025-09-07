
import math
import json
import pandas as pd
import streamlit as st
from io import BytesIO
try:
    import matplotlib.pyplot as plt
except Exception:
    plt = None
import numpy as np
import hashlib

from app_loader_v2 import load_property_data

SCHEMA_PATH = "property_schema_v2.json"

st.set_page_config(page_title="Property Dashboard — V2.7 (Pro)", layout="wide")
st.title("🏢 Property Listings Dashboard — V2.7 (Pro)")

st.markdown(
    "This build adds **Net numbers**, **winsorized DealScore**, **what‑if sliders**, **Shortlist**, and **location medians/outlier flags**, "
    "on top of all V2.6 features (currency toggle, include‑blank filters, Insights, Loan, Best Deals, Top Deals Only)."
)

# ---------------- Helpers ----------------
@st.cache_data(show_spinner=False)
def load_default(schema_path: str):
    try:
        df = load_property_data("PROPERTY_LIST_ENRICHED.xlsx", schema_path)
        return df
    except Exception as e:
        st.error(f"Failed to load default file: {e}")
        return None

def coerce_numeric(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def fmt_num(val, decimals=0):
    if pd.isna(val):
        return "—"
    try:
        if decimals == 0:
            return f"{val:,.0f}"
        return f"{val:,.{decimals}f}"
    except Exception:
        return str(val)

def scale_currency(series, unit):
    s = pd.to_numeric(series, errors="coerce")
    if unit == "INR":
        factor = 1.0
        label = "INR"
        symbol = "₹"
    elif unit == "Lakh":
        factor = 1e5
        label = "Lakh"
        symbol = "₹"
    else:
        factor = 1e7
        label = "Crore"
        symbol = "₹"
    return s / factor, label, symbol

def to_excel_bytes(df_out: pd.DataFrame, sheet="Sheet1") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df_out.to_excel(writer, index=False, sheet_name=sheet)
    return output.getvalue()

def emi(p, r, n):
    try:
        if pd.isna(p) or p <= 0 or r < 0 or n <= 0:
            return None
        if r == 0:
            return p / n
        return p * r * (1 + r) ** n / ((1 + r) ** n - 1)
    except Exception:
        return None

def principal_for_emi(emi_target, r, n):
    try:
        if pd.isna(emi_target) or emi_target <= 0 or r < 0 or n <= 0:
            return None
        if r == 0:
            return emi_target * n
        return emi_target * ((1 + r) ** n - 1) / (r * (1 + r) ** n)
    except Exception:
        return None

def rate_for_emi(p, n, emi_target):
    try:
        if any(pd.isna(x) or x <= 0 for x in [p, n, emi_target]):
            return None
        low, high = 1e-9, 0.05
        for _ in range(80):
            mid = (low + high) / 2.0
            e = emi(p, mid, n)
            if e is None:
                return None
            if e > emi_target:
                high = mid
            else:
                low = mid
        return (low + high) / 2.0
    except Exception:
        return None

def winsor(s, p1=0.05, p2=0.95):
    s = pd.to_numeric(s, errors="coerce")
    if s.dropna().empty:
        return s
    lo, hi = s.quantile(p1), s.quantile(p2)
    return s.clip(lower=lo, upper=hi)

def norm01(s):
    s = pd.to_numeric(s, errors="coerce")
    if s.dropna().nunique() <= 1:
        return pd.Series(0, index=s.index)
    return (s - s.min()) / (s.max() - s.min())

def rowkey(row):
    base = f"{row.get('Location','')}|{row.get('Tenant','')}|{row.get('Area_Sft','')}|{row.get('Price_INR','')}|{row.get('Rent_INR','')}"
    return hashlib.md5(base.encode('utf-8')).hexdigest()

# ---------------- Load ----------------
uploaded = st.file_uploader("Upload cleaned/enriched dataset (.xlsx or .csv)", type=["xlsx", "csv"])
if uploaded is not None:
    if uploaded.name.lower().endswith(".xlsx"):
        df = pd.read_excel(uploaded)
    else:
        df = pd.read_csv(uploaded)
else:
    df = load_default(SCHEMA_PATH)
    if df is None:
        st.stop()

# Derived if missing
if "Gross_Yield_%" not in df.columns and set(["Rent_INR", "Price_INR"]).issubset(df.columns):
    df["Gross_Yield_%"] = (pd.to_numeric(df["Rent_INR"], errors="coerce") * 12 / pd.to_numeric(df["Price_INR"], errors="coerce")) * 100
if "Price_per_Sft" not in df.columns and set(["Price_INR", "Area_Sft"]).issubset(df.columns):
    df["Price_per_Sft"] = (pd.to_numeric(df["Price_INR"], errors="coerce") / pd.to_numeric(df["Area_Sft"], errors="coerce")).replace([float("inf")], None)
if "Rent_per_Sft" not in df.columns and set(["Rent_INR", "Area_Sft"]).issubset(df.columns):
    df["Rent_per_Sft"] = (pd.to_numeric(df["Rent_INR"], errors="coerce") / pd.to_numeric(df["Area_Sft"], errors="coerce")).replace([float("inf")], None)

# Location medians & outlier flags
def add_loc_medians(dd: pd.DataFrame) -> pd.DataFrame:
    out = dd.copy()
    for col in ["Price_per_Sft", "Rent_per_Sft", "Gross_Yield_%"]:
        if col in out.columns and "Location" in out.columns:
            med = out.groupby("Location")[col].median()
            out[f"LocMedian_{col}"] = out["Location"].map(med)
            out[f"VsLocMedian_{col}_%"] = 100.0 * (pd.to_numeric(out[col], errors="coerce") / pd.to_numeric(out[f"LocMedian_{col}"], errors="coerce") - 1.0)
            # Robust outlier via MAD
            grp = out.groupby("Location")[col]
            med_all = grp.transform("median")
            mad = (grp.transform(lambda s: (np.abs(s - s.median())).median())) * 1.4826
            z = (np.abs(out[col] - med_all)) / (mad.replace(0, np.nan))
            out[f"Outlier_{col}"] = (z > 3).astype(int)
    return out

df = add_loc_medians(df)

# ---------------- Tabs ----------------
tab_browse, tab_insights, tab_loan, tab_deals = st.tabs(["📋 Browse", "📈 Insights", "💸 Loan", "⭐ Best Deals"])

# ---- Browse ----
with tab_browse:
    st.sidebar.header("Filters")
    locs = sorted([x for x in df["Location"].dropna().astype(str).unique() if x.strip()])
    sel_locs = st.sidebar.multiselect("Location", options=locs, default=locs)
    tenant_q = st.sidebar.text_input("Tenant contains", "")

    def slider_for(series, label):
        s = pd.to_numeric(series, errors="coerce").dropna()
        if s.empty:
            return None
        lo, hi = float(s.min()), float(s.max())
        step = max(1.0, (hi - lo) / 100.0)
        return st.sidebar.slider(label, min_value=lo, max_value=hi, value=(lo, hi), step=step)

    pr_range = slider_for(df.get("Price_INR", pd.Series(dtype=float)), "Price (INR)")
    ar_range = slider_for(df.get("Area_Sft", pd.Series(dtype=float)), "Area (Sft)")
    ye_range = slider_for(df.get("Gross_Yield_%", pd.Series(dtype=float)), "Gross Yield (%)")
    le_range = slider_for(df.get("Lease_Years", pd.Series(dtype=float)), "Lease (years)")
    li_range = slider_for(df.get("LockIn_Years", pd.Series(dtype=float)), "Lock-in (years)")

    parking_only = st.sidebar.checkbox("Has Parking only", value=False)

    st.sidebar.header("Display")
    unit = st.sidebar.radio("Currency unit", ["INR","Lakh","Crore"], index=2)
    top_deals = st.sidebar.checkbox("Sort by Top Deals (Yield ↓)", value=False)

    st.sidebar.header("Blanks")
    include_blank_price = st.sidebar.checkbox("Include blank Price rows", value=True)
    include_blank_area  = st.sidebar.checkbox("Include blank Area rows", value=True)
    include_blank_yield = st.sidebar.checkbox("Include blank Yield rows", value=True)
    include_blank_lease = st.sidebar.checkbox("Include blank Lease rows", value=True)
    include_blank_lock  = st.sidebar.checkbox("Include blank Lock-in rows", value=True)

    filtered = df.copy()
    if sel_locs:
        filtered = filtered[filtered["Location"].isin(sel_locs)]
    if tenant_q.strip():
        filtered = filtered[filtered["Tenant"].astype(str).str.contains(tenant_q.strip(), case=False, na=False)]

    def apply_range(fdf, col, rng, include_blank=True):
        if rng is None or col not in fdf.columns:
            return fdf
        ser = pd.to_numeric(fdf[col], errors="coerce")
        lo, hi = rng
        mask_range = (ser >= lo) & (ser <= hi)
        mask = mask_range | ser.isna() if include_blank else mask_range
        return fdf[mask]

    filtered = apply_range(filtered, "Price_INR", pr_range, include_blank_price)
    filtered = apply_range(filtered, "Area_Sft", ar_range, include_blank_area)
    filtered = apply_range(filtered, "Gross_Yield_%", ye_range, include_blank_yield)
    filtered = apply_range(filtered, "Lease_Years", le_range, include_blank_lease)
    filtered = apply_range(filtered, "LockIn_Years", li_range, include_blank_lock)

    if parking_only and "Parking_Cars" in filtered.columns:
        pc = pd.to_numeric(filtered["Parking_Cars"], errors="coerce").fillna(0)
        filtered = filtered[pc > 0]

    if top_deals and "Gross_Yield_%" in filtered.columns:
        filtered = filtered.sort_values(by="Gross_Yield_%", ascending=False, na_position="last")

    st.caption(f"Showing **{len(filtered)}** of **{len(df)}** total rows")

    # KPIs (scaled avg price in selected unit)
    price_mean = pd.to_numeric(filtered.get("Price_INR", pd.Series(dtype=float)), errors="coerce").dropna().mean()
    area_mean  = pd.to_numeric(filtered.get("Area_Sft", pd.Series(dtype=float)), errors="coerce").dropna().mean()
    yield_mean = pd.to_numeric(filtered.get("Gross_Yield_%", pd.Series(dtype=float)), errors="coerce").dropna().mean()
    price_mean_scaled, price_label, symbol = scale_currency(pd.Series([price_mean]), unit)
    price_mean_scaled = price_mean_scaled.iloc[0] if not pd.isna(price_mean) else None

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Listings", len(filtered))
    c2.metric(f"Avg Price ({price_label})", fmt_num(price_mean_scaled) if price_mean_scaled is not None else "—")
    c3.metric("Avg Area (Sft)", fmt_num(area_mean) if pd.notna(area_mean) else "—")
    c4.metric("Avg Yield (%)", fmt_num(yield_mean, 2) if pd.notna(yield_mean) else "—")
    with_parking = pd.to_numeric(filtered.get("Parking_Cars", pd.Series(dtype=float)), errors="coerce").fillna(0).gt(0).sum()
    c5.metric("With Parking", int(with_parking))

    # Results table with currency scaling for Price/Rent
    show_cols = [
        "Location","Tenant","Area_Sft","Price_INR","Rent_INR",
        "Price_per_Sft","Rent_per_Sft","Gross_Yield_%",
        "Lease_Years","LockIn_Years","Increment_%","Increment_Every_Years",
        "Advance_Months","Parking_Cars","UDS_SqYards",
        "LocMedian_Price_per_Sft","VsLocMedian_Price_per_Sft_%",
        "LocMedian_Rent_per_Sft","VsLocMedian_Rent_per_Sft_%",
        "LocMedian_Gross_Yield_%","VsLocMedian_Gross_Yield_%",
        "Outlier_Price_per_Sft","Outlier_Rent_per_Sft","Outlier_Gross_Yield_%"
    ]
    available_cols = [c for c in show_cols if c in filtered.columns]
    disp = filtered[available_cols].copy()
    disp = coerce_numeric(disp, ["Area_Sft","Price_INR","Rent_INR","Price_per_Sft","Rent_per_Sft","Gross_Yield_%",
                                 "Lease_Years","LockIn_Years","Increment_%","Increment_Every_Years","Advance_Months",
                                 "Parking_Cars","UDS_SqYards",
                                 "LocMedian_Price_per_Sft","VsLocMedian_Price_per_Sft_%",
                                 "LocMedian_Rent_per_Sft","VsLocMedian_Rent_per_Sft_%",
                                 "LocMedian_Gross_Yield_%","VsLocMedian_Gross_Yield_%"])

    disp["Price_disp"], price_label, _ = scale_currency(disp["Price_INR"], unit)
    disp["Rent_disp"], rent_label, _ = scale_currency(disp["Rent_INR"], unit)

    disp = disp.replace({pd.NA: "—"}).fillna("—")

    st.subheader("Results")
    preferred_cols = [
        "Location","Tenant","Area_Sft",f"Price ({price_label})",f"Rent ({rent_label})",
        "Price_per_Sft","Rent_per_Sft","Gross_Yield_%",
        "Lease_Years","LockIn_Years",
        "LocMedian_Price_per_Sft","VsLocMedian_Price_per_Sft_%",
        "LocMedian_Rent_per_Sft","VsLocMedian_Rent_per_Sft_%",
        "LocMedian_Gross_Yield_%","VsLocMedian_Gross_Yield_%",
        "Outlier_Price_per_Sft","Outlier_Rent_per_Sft","Outlier_Gross_Yield_%"
    ]
    try:
        st.dataframe(
            disp.rename(columns={
                "Price_disp": f"Price ({price_label})",
                "Rent_disp": f"Rent ({rent_label})"
            })[[c for c in preferred_cols if c in disp.rename(columns={"Price_disp": f"Price ({price_label})","Rent_disp": f"Rent ({rent_label})"}).columns]],
            use_container_width=True
        )
    except Exception:
        st.dataframe(disp, use_container_width=True)

    st.download_button(
        label="⬇️ Download Filtered Results (Excel)",
        data=to_excel_bytes(disp, "Filtered"),
        file_name="filtered_properties_v2_7.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ---- Insights ----
with tab_insights:
    st.subheader("Insights")
    try:
        filtered
    except NameError:
        filtered = df
    f2 = filtered.copy()
    f2["Price_INR"] = pd.to_numeric(f2["Price_INR"], errors="coerce")
    f2["Area_Sft"]  = pd.to_numeric(f2["Area_Sft"], errors="coerce")
    f2["Rent_INR"]  = pd.to_numeric(f2["Rent_INR"], errors="coerce")
    f2["Gross_Yield_%"] = pd.to_numeric(f2["Gross_Yield_%"], errors="coerce")
    if f2.dropna(how="all").empty:
        st.info("No data after filters.")
    else:
        st.markdown("**Top Locations by Listing Count**")
        loc_counts = f2["Location"].astype(str).value_counts().head(10)
        if plt is None:
            st.bar_chart(loc_counts)
        else:
            fig1, ax1 = plt.subplots()
            ax1.bar(loc_counts.index, loc_counts.values)
            ax1.set_xlabel("Location")
            ax1.set_ylabel("Listings")
            ax1.set_xticklabels(loc_counts.index, rotation=45, ha="right")
            st.pyplot(fig1)

        st.markdown("**Gross Yield (%) Distribution**")
        ys = f2["Gross_Yield_%"].dropna()
        if not ys.empty:
            if plt is None:
                hist = ys.value_counts(bins=10).sort_index()
                st.bar_chart(hist)
            else:
                fig2, ax2 = plt.subplots()
                ax2.hist(ys, bins=10)
                ax2.set_xlabel("Gross Yield (%)")
                ax2.set_ylabel("Frequency")
                st.pyplot(fig2)
        else:
            st.write("No yield data available.")

        st.markdown("**Price vs Area (bubble ~ Rent)**")
        valid = f2.dropna(subset=["Price_INR","Area_Sft","Rent_INR"])
        if not valid.empty:
            if plt is None:
                st.scatter_chart(valid.rename(columns={"Area_Sft":"x","Price_INR":"y"})[["x","y"]])
            else:
                s = (valid["Rent_INR"] / valid["Rent_INR"].max()) * 300.0
                fig3, ax3 = plt.subplots()
                ax3.scatter(valid["Area_Sft"], valid["Price_INR"], s=s)
                ax3.set_xlabel("Area (Sft)")
                ax3.set_ylabel("Price (INR)")
                st.pyplot(fig3)
        else:
            st.write("Not enough data for scatter plot.")

# ---- Loan ----
with tab_loan:
    st.subheader("Loan Analysis — EMI vs Rent")
    colA, colB, colC = st.columns(3)
    with colA:
        interest_pct = st.number_input("Annual Interest Rate (%)", min_value=0.0, max_value=30.0, value=8.5, step=0.1, key="loan_rate")
    with colB:
        tenure_years = st.number_input("Loan Tenure (years)", min_value=1, max_value=40, value=20, step=1, key="loan_years")
    with colC:
        down_pct = st.number_input("Down Payment (%)", min_value=0.0, max_value=90.0, value=20.0, step=1.0, key="loan_down")

    # Net numbers toggle
    st.markdown("**Net numbers (apply to Loan & Deals)**")
    use_net = st.checkbox("Use net numbers", value=False, key="use_net")
    colN1, colN2, colN3, colN4 = st.columns(4)
    with colN1:
        vacancy = st.number_input("Vacancy %", 0.0, 50.0, 5.0, 0.5, key="vacancy")
    with colN2:
        opex_pct = st.number_input("Opex % of rent", 0.0, 80.0, 10.0, 0.5, key="opex")
    with colN3:
        maint_inr = st.number_input("Maintenance (₹/mo)", 0.0, 1e7, 0.0, 1000.0, key="maint")
    with colN4:
        taxes_inr = st.number_input("Taxes (₹/mo)", 0.0, 1e7, 0.0, 1000.0, key="taxes")

    try:
        filtered
    except NameError:
        filtered = df.copy()

    loan_df = filtered.copy()
    loan_df["Price_INR"] = pd.to_numeric(loan_df["Price_INR"], errors="coerce")
    loan_df["Rent_INR"] = pd.to_numeric(loan_df["Rent_INR"], errors="coerce")

    r = float(interest_pct) / 100.0 / 12.0
    n = int(tenure_years) * 12
    ltv = 1.0 - (float(down_pct) / 100.0)

    loan_df["Principal_INR"] = loan_df["Price_INR"] * ltv
    loan_df["EMI_INR"] = loan_df["Principal_INR"].apply(lambda p: emi(p, r, n))

    # Net rent
    rent_net = loan_df["Rent_INR"]
    if use_net:
        rent_net = (rent_net * (1 - st.session_state["vacancy"]/100.0) * (1 - st.session_state["opex"]/100.0)) - (st.session_state["maint"] + st.session_state["taxes"])

    loan_df["Monthly_Cashflow_INR"] = rent_net - loan_df["EMI_INR"]
    loan_df["DSCR"] = loan_df.apply(lambda x: (rent_net.loc[x.name] / x["EMI_INR"]) if (pd.notna(rent_net.loc[x.name]) and pd.notna(x["EMI_INR"]) and x["EMI_INR"] > 0) else None, axis=1)
    loan_df["Down_Payment_INR"] = loan_df["Price_INR"] * (down_pct / 100.0)
    loan_df["LTV_%"] = ltv * 100.0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Listings in scope", int(len(loan_df.dropna(subset=["Price_INR", "Rent_INR"]))))
    avg_emi = pd.to_numeric(loan_df["EMI_INR"], errors="coerce").dropna().mean()
    k2.metric("Avg EMI (INR)", fmt_num(avg_emi) if pd.notna(avg_emi) else "—")
    pos_cf = (pd.to_numeric(loan_df["Monthly_Cashflow_INR"], errors="coerce") > 0).sum()
    k3.metric("Positive Cashflow", int(pos_cf))
    med_dscr = pd.to_numeric(loan_df["DSCR"], errors="coerce").dropna().median()
    k4.metric("Median DSCR", fmt_num(med_dscr, 2) if pd.notna(med_dscr) else "—")

    show_cols_loan = [
        "Location","Tenant","Area_Sft","Price_INR","Rent_INR",
        "Down_Payment_INR","Principal_INR","EMI_INR",
        "Monthly_Cashflow_INR","DSCR","LTV_%","Lease_Years","LockIn_Years"
    ]
    avail_loan_cols = [c for c in show_cols_loan if c in loan_df.columns]
    st.dataframe(loan_df[avail_loan_cols].reset_index(drop=True), use_container_width=True)

    st.download_button(
        label="⬇️ Download Loan Analysis (Excel)",
        data=to_excel_bytes(loan_df[avail_loan_cols], "Loan Analysis"),
        file_name="loan_analysis_v2_7.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

# ---- Best Deals ----
with tab_deals:
    st.subheader("Best Deals — Winsorized DealScore (%) + Net Numbers + What‑if + Shortlist")

    # Inputs
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        target_yield = st.number_input("Target Yield %", min_value=0.0, max_value=50.0, value=7.0, step=0.1)
    with col2:
        min_lease = st.number_input("Min Lease Years", min_value=0.0, max_value=99.0, value=5.0, step=1.0)
    with col3:
        min_lock = st.number_input("Min Lock-in Years", min_value=0.0, max_value=99.0, value=2.0, step=1.0)
    with col4:
        min_dscr = st.number_input("Min DSCR", min_value=0.0, max_value=5.0, value=1.1, step=0.1)
    with col5:
        topn = st.number_input("Show Top N", min_value=1, max_value=100, value=10, step=1)

    w1, w2, w3 = st.columns(3)
    with w1:
        deal_w_yield = st.slider("Weight: Yield", 0.0, 1.0, 0.5, 0.05)
    with w2:
        deal_w_lease = st.slider("Weight: Lease", 0.0, 1.0, 0.2, 0.05)
    with w3:
        deal_w_price = st.slider("Weight: Price/ft² (lower better)", 0.0, 1.0, 0.3, 0.05)

    # Loan assumptions
    lc1, lc2, lc3 = st.columns(3)
    with lc1:
        d_interest = st.number_input("Loan: Annual Interest %", min_value=0.0, max_value=30.0, value=st.session_state.get("loan_rate", 8.5), step=0.1, key="d_int")
    with lc2:
        d_years = st.number_input("Loan: Tenure (years)", min_value=1, max_value=40, value=st.session_state.get("loan_years", 20), step=1, key="d_years")
    with lc3:
        d_down = st.number_input("Loan: Down Payment %", min_value=0.0, max_value=90.0, value=st.session_state.get("loan_down", 20.0), step=1.0, key="d_down")

    # Net numbers (shared with Loan)
    st.markdown("**Net numbers (shared with Loan tab)**")
    use_net = st.checkbox("Use net numbers", value=st.session_state.get("use_net", False), key="use_net_deals")
    vacancy = st.number_input("Vacancy %", 0.0, 50.0, st.session_state.get("vacancy", 5.0), 0.5, key="vacancy_deals")
    opex_pct = st.number_input("Opex % of rent", 0.0, 80.0, st.session_state.get("opex", 10.0), 0.5, key="opex_deals")
    maint_inr = st.number_input("Maintenance (₹/mo)", 0.0, 1e7, st.session_state.get("maint", 0.0), 1000.0, key="maint_deals")
    taxes_inr = st.number_input("Taxes (₹/mo)", 0.0, 1e7, st.session_state.get("taxes", 0.0), 1000.0, key="taxes_deals")

    # What‑if sliders
    st.markdown("**What‑if adjustments** (applied before scoring)")
    colw1, colw2 = st.columns(2)
    with colw1:
        price_disc = st.slider("Offer Price discount (%)", 0.0, 30.0, 0.0, 0.5)
    with colw2:
        rent_up = st.slider("Rent increase (%)", 0.0, 30.0, 0.0, 0.5)

    top_deals_only = st.checkbox("Top Deals Only (DSCR ≥ 1.1, Yield ≥ target, Lease ≥ min, Lock‑in ≥ min, Positive CF)", value=False)

    try:
        filtered
    except NameError:
        filtered = df.copy()

    deals = filtered.copy()
    deals = coerce_numeric(deals, ["Price_INR","Rent_INR","Gross_Yield_%","Lease_Years","LockIn_Years","Price_per_Sft","Parking_Cars"])

    # Apply what‑if
    deals["Price_INR_adj"] = deals["Price_INR"] * (1 - price_disc/100.0)
    deals["Rent_INR_adj"] = deals["Rent_INR"] * (1 + rent_up/100.0)

    # Financing
    r = float(d_interest) / 100.0 / 12.0
    n = int(d_years) * 12
    ltv = 1.0 - (float(d_down) / 100.0)

    deals["Principal_INR"] = deals["Price_INR_adj"] * ltv
    deals["EMI_INR"] = deals["Principal_INR"].apply(lambda p: emi(p, r, n))

    # Net rent
    rent_eff = deals["Rent_INR_adj"]
    if use_net:
        rent_eff = (rent_eff * (1 - vacancy/100.0) * (1 - opex_pct/100.0)) - (maint_inr + taxes_inr)

    # Recompute yield on adjusted values
    deals["Gross_Yield_%"] = (deals["Rent_INR_adj"] * 12 / deals["Price_INR_adj"] * 100).where((deals["Price_INR_adj"] > 0) & deals["Rent_INR_adj"].notna())
    deals["Net_Yield_%"] = (rent_eff * 12 / deals["Price_INR_adj"] * 100).where((deals["Price_INR_adj"] > 0) & rent_eff.notna())

    deals["Monthly_Cashflow_INR"] = rent_eff - deals["EMI_INR"]
    deals["DSCR"] = deals.apply(lambda x: (rent_eff.loc[x.name] / x["EMI_INR"]) if (pd.notna(rent_eff.loc[x.name]) and pd.notna(x["EMI_INR"]) and x["EMI_INR"] > 0) else None, axis=1)

    # Hard constraints (soft if value missing)
    ly = pd.to_numeric(deals["Lease_Years"], errors="coerce")
    li = pd.to_numeric(deals["LockIn_Years"], errors="coerce")
    ds = pd.to_numeric(deals["DSCR"], errors="coerce")

    mask = pd.Series(True, index=deals.index)
    mask &= (ly.isna() | (ly >= min_lease))
    mask &= (li.isna() | (li >= min_lock))
    mask &= (ds.isna() | (ds >= min_dscr))
    deals = deals[mask].copy()

    # Winsorized DealScore with missingness penalty
    y_base = winsor(deals["Net_Yield_%"] if use_net else deals["Gross_Yield_%"])
    l_base = winsor(deals["Lease_Years"])
    p_base = winsor(deals["Price_per_Sft"])
    y_score = norm01(y_base)
    l_score = norm01(l_base)
    p_score = 1 - norm01(p_base)  # lower is better
    penalty = (
        deals["Gross_Yield_%"].isna().astype(float)
      + deals["Lease_Years"].isna().astype(float)
      + deals["Price_per_Sft"].isna().astype(float)
    ) * 0.02
    deals["DealScore"] = (deal_w_yield*y_score.fillna(0)
                        + deal_w_lease*l_score.fillna(0)
                        + deal_w_price*p_score.fillna(0)) - penalty
    deals["DealScore"] = deals["DealScore"].clip(lower=0)
    deals["DealScore_%"] = (deals["DealScore"] * 100).round(1)

    # Positive CF suggestions
    deals["Pmax_for_rent"] = deals["Rent_INR_adj"].apply(lambda m: principal_for_emi(m if not use_net else (m*(1 - vacancy/100.0)*(1 - opex_pct/100.0) - (maint_inr + taxes_inr)), r, n))
    deals["Price_BreakEven_INR"] = deals.apply(lambda x: (x["Pmax_for_rent"] / ltv) if (pd.notna(x["Pmax_for_rent"]) and ltv > 0) else None, axis=1)
    deals["Price_Discount_Needed_%"] = deals.apply(
        lambda x: (100.0 * (1 - x["Price_BreakEven_INR"] / x["Price_INR_adj"])) if (pd.notna(x["Price_BreakEven_INR"]) and pd.notna(x["Price_INR_adj"]) and x["Price_INR_adj"] > 0) else None,
        axis=1
    )
    deals["Rent_Needed_PosCF_INR"] = deals["EMI_INR"]
    if use_net:
        # Invert net formula approximately for rent needed
        # rent_gross * (1 - vac)*(1 - opex) - (maint+taxes) >= EMI  => rent_gross >= (EMI + maint+taxes) / ((1-v)(1-o))
        denom = (1 - vacancy/100.0) * (1 - opex_pct/100.0)
        deals["Rent_Needed_PosCF_INR"] = (deals["EMI_INR"] + (maint_inr + taxes_inr)) / (denom if denom > 0 else np.nan)
    deals["Rent_Increase_%"] = deals.apply(
        lambda x: (100.0 * (x["Rent_Needed_PosCF_INR"] - x["Rent_INR_adj"]) / x["Rent_INR_adj"]) if (pd.notna(x["Rent_INR_adj"]) and x["Rent_INR_adj"] > 0 and pd.notna(x["Rent_Needed_PosCF_INR"])) else None,
        axis=1
    )
    deals["LTV_Needed"] = deals.apply(
        lambda x: min(1.0, (principal_for_emi((x["Rent_INR_adj"] if not use_net else ((x["Rent_INR_adj"]*(1 - vacancy/100.0)*(1 - opex_pct/100.0) - (maint_inr + taxes_inr)))), r, n) / x["Price_INR_adj"])) if (pd.notna(x["Price_INR_adj"]) and pd.notna(x["Rent_INR_adj"]) and x["Price_INR_adj"] > 0) else None,
        axis=1
    )
    deals["Down_%_Needed_for_PosCF"] = deals["LTV_Needed"].apply(lambda l: (1.0 - l) * 100.0 if pd.notna(l) else None)
    deals["Extra_Down_%_Required"] = deals["Down_%_Needed_for_PosCF"].apply(lambda need: max(0.0, need - d_down) if pd.notna(need) else None)
    deals["Extra_Down_INR"] = deals.apply(
        lambda x: (x["Price_INR_adj"] * x["Extra_Down_%_Required"] / 100.0) if (pd.notna(x.get("Price_INR_adj")) and pd.notna(x.get("Extra_Down_%_Required"))) else None,
        axis=1
    )
    deals["Rate_Needed_monthly"] = deals.apply(lambda x: rate_for_emi(x.get("Principal_INR"), n, (x.get("Rent_INR_adj") if not use_net else ((x.get("Rent_INR_adj")*(1 - vacancy/100.0)*(1 - opex_pct/100.0) - (maint_inr + taxes_inr)) ))), axis=1)
    deals["Rate_Needed_annual_%"] = deals["Rate_Needed_monthly"].apply(lambda rm: rm * 12 * 100 if pd.notna(rm) else None)
    deals["Rate_Reduction_pp"] = deals["Rate_Needed_annual_%"].apply(lambda req: (d_interest - req) if pd.notna(req) else None)

    def best_action(row):
        if pd.notna(row.get("Monthly_Cashflow_INR")) and row["Monthly_Cashflow_INR"] >= 0:
            return "Already positive cashflow"
        candidates = []
        pdp = row.get("Price_Discount_Needed_%")
        if pd.notna(pdp) and pdp > 0:
            candidates.append(("Negotiate price ↓", pdp, f"Offer ≤ ₹{fmt_num(row.get('Price_BreakEven_INR'))} (−{pdp:.1f}%)"))
        edp = row.get("Extra_Down_%_Required")
        if pd.notna(edp) and edp > 0:
            candidates.append(("Increase down payment", edp, f"Add {edp:.1f}% (₹{fmt_num(row.get('Extra_Down_INR'))})"))
        rip = row.get("Rent_Increase_%")
        if pd.notna(rip) and rip > 0:
            candidates.append(("Raise rent", rip, f"Rent ≥ ₹{fmt_num(row.get('Rent_Needed_PosCF_INR'))} (+{rip:.1f}%)"))
        rr = row.get("Rate_Reduction_pp")
        if pd.notna(rr) and rr > 0:
            candidates.append(("Negotiate rate", rr, f"Rate ≤ {row.get('Rate_Needed_annual_%'):.2f}% (−{rr:.2f} pp)"))
        if not candidates:
            return "—"
        best = min(candidates, key=lambda x: x[1])
        return f"{best[0]} → {best[2]}"
    deals["Best_Action"] = deals.apply(best_action, axis=1)

    # Top Deals Only filter
    if top_deals_only:
        meets = pd.Series(True, index=deals.index)
        meets &= (pd.to_numeric(deals["DSCR"], errors="coerce") >= 1.1)
        meets &= (pd.to_numeric((deals["Net_Yield_%"] if use_net else deals["Gross_Yield_%"]), errors="coerce") >= target_yield)
        meets &= (pd.to_numeric(deals["Lease_Years"], errors="coerce") >= min_lease) | deals["Lease_Years"].isna()
        meets &= (pd.to_numeric(deals["LockIn_Years"], errors="coerce") >= min_lock) | deals["LockIn_Years"].isna()
        meets &= (pd.to_numeric(deals["Monthly_Cashflow_INR"], errors="coerce") >= 0)
        deals = deals[meets]

    # Sort & show
    deals_sorted = deals.sort_values(by=["DealScore"], ascending=False, na_position="last").head(int(topn)).copy()

    # Shortlist (editable)
    deals_sorted["RowKey"] = deals_sorted.apply(rowkey, axis=1)
    if "shortlist" not in st.session_state:
        st.session_state["shortlist"] = {}
    # Pre-fill selections
    deals_sorted["⭐ Shortlist"] = deals_sorted["RowKey"].map(lambda k: st.session_state["shortlist"].get(k, False))
    edited = st.data_editor(
        deals_sorted[[
            "⭐ Shortlist","DealScore_%","Location","Tenant","Area_Sft","Price_INR_adj","Rent_INR_adj",
            "Gross_Yield_%","Net_Yield_%","Lease_Years","LockIn_Years","DSCR",
            "Price_per_Sft","Price_BreakEven_INR","Price_Discount_Needed_%",
            "Rent_Needed_PosCF_INR","Rent_Increase_%",
            "Down_%_Needed_for_PosCF","Extra_Down_%_Required","Extra_Down_INR",
            "Rate_Needed_annual_%","Rate_Reduction_pp","Best_Action","RowKey"
        ]].reset_index(drop=True),
        hide_index=True,
        use_container_width=True,
        key="editor_deals"
    )

    # Update shortlist
    for _, row in edited.iterrows():
        st.session_state["shortlist"][row["RowKey"]] = bool(row["⭐ Shortlist"])

    # Shortlist export
    shortlist_keys = [k for k, v in st.session_state["shortlist"].items() if v]
    shortlist_df = deals[deals.apply(rowkey, axis=1).isin(shortlist_keys)].copy()
    st.download_button(
        "⬇️ Download Shortlist (CSV)",
        data=shortlist_df.to_csv(index=False),
        file_name="shortlist_v2_7.csv",
        mime="text/csv"
    )

    st.download_button(
        label="⬇️ Download Best Deals (Excel)",
        data=to_excel_bytes(deals_sorted.drop(columns=["RowKey","⭐ Shortlist"], errors="ignore"), "Best Deals V2.7"),
        file_name="best_deals_v2_7.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
