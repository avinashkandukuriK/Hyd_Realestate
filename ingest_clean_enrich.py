#!/usr/bin/env python3
import re, pandas as pd, numpy as np, sys, os

def parse_money_phrase(s):
    s0 = str(s).lower().replace(',', ' ')
    mult = 1.0
    if re.search(r"crore|cr\b|cr\.", s0):
        mult = 1e7
    elif re.search(r"lakh|lac|lacs|\bl\b", s0):
        mult = 1e5
    elif re.search(r"thousand|k\b", s0):
        mult = 1e3
    m = re.search(r"([\d]+(?:\.\d+)?)", s0)
    return float(m.group(1))*mult if m else np.nan

def parse_area_phrase(s):
    s0 = str(s).lower().replace(',', ' ')
    m_sft = re.search(r"([\d]+(?:\.\d+)?)\s*(s\.?ft|sq ?ft|sft)", s0)
    if m_sft: return float(m_sft.group(1))
    m_yd = re.search(r"([\d]+(?:\.\d+)?)\s*(s\.? ?yards?|sq ?yards?|sq ?yds?)", s0)
    if m_yd: return float(m_yd.group(1))*9.0
    m_any = re.search(r"([\d]+(?:\.\d+)?)", s0)
    return float(m_any.group(1)) if m_any else np.nan

def parse_increment(s):
    s0 = str(s).lower()
    pct = None; yrs = None
    mp = re.search(r"(\d+(\.\d+)?)\s*%", s0)
    if mp: pct = float(mp.group(1))
    my = re.search(r"every\s*(\d+)", s0)
    if my: yrs = float(my.group(1))
    return pct, yrs

def looks_like_location(s):
    if ":" in s: return False
    if "contact" in str(s).lower(): return False
    return bool(re.fullmatch(r"[A-Z0-9 \-\/\.()]+", str(s).strip()))

def fix_rent_units(df):
    rent = pd.to_numeric(df.get("Rent_INR"), errors="coerce")
    price = pd.to_numeric(df.get("Price_INR"), errors="coerce")
    area  = pd.to_numeric(df.get("Area_Sft"), errors="coerce")
    df["Rent_Fix_Flag"] = "ok"
    m_k = (rent > 0) & (rent <= 2000) & ((price >= 5e6) | (area >= 400))
    df.loc[m_k, "Rent_INR"] = rent[m_k] * 1000.0
    df.loc[m_k, "Rent_Fix_Flag"] = "x1000"
    rent = pd.to_numeric(df["Rent_INR"], errors="coerce")
    m_psf = (rent.between(30, 400)) & (area >= 300)
    df.loc[m_psf, "Rent_INR"] = rent[m_psf] * area[m_psf]
    df.loc[m_psf, "Rent_Fix_Flag"] = "₹/sft→total"
    rent = pd.to_numeric(df["Rent_INR"], errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        y_guess = (rent * 12 / price) * 100
    m_annual = y_guess > 30
    df.loc[m_annual, "Rent_INR"] = rent[m_annual] / 12.0
    df.loc[m_annual, "Rent_Fix_Flag"] = "÷12_annual"
    return df

def fix_price_units(df):
    p = pd.to_numeric(df.get("Price_INR"), errors="coerce")
    r = pd.to_numeric(df.get("Rent_INR"), errors="coerce")
    a = pd.to_numeric(df.get("Area_Sft"), errors="coerce")
    df["Price_Fix_Flag"] = "ok"
    m_cr = (p > 0) & (p < 100) & ((a >= 400) | (r >= 20000))
    df.loc[m_cr, "Price_INR"] = p[m_cr] * 1e7
    df.loc[m_cr, "Price_Fix_Flag"] = "Cr→INR"
    p2 = pd.to_numeric(df.get("Price_INR"), errors="coerce")
    m_l = (p2 >= 100) & (p2 < 50000) & ((a >= 400) | (r >= 20000))
    df.loc[m_l, "Price_INR"] = p2[m_l] * 1e5
    df.loc[m_l, "Price_Fix_Flag"] = "Lakh→INR"
    return df

def fix_area_units(df):
    area = pd.to_numeric(df.get("Area_Sft"), errors="coerce")
    uds  = pd.to_numeric(df.get("UDS_SqYards"), errors="coerce")
    df["Area_Fix_Flag"] = "ok"
    m_sqyd = (area.between(50, 400)) & (uds.notna()) & ((uds*9 - area).abs() <= 20)
    df.loc[m_sqyd, "Area_Sft"] = area[m_sqyd] * 9.0
    df.loc[m_sqyd, "Area_Fix_Flag"] = "sqyd→sft(x9)"
    return df

def main(src, out_xlsx="PROPERTY_LIST_ENRICHED.xlsx", out_csv="PROPERTY_LIST_ENRICHED.csv"):
    raw = pd.read_excel(src, sheet_name=0, header=None)
    lines = []
    for i in range(len(raw)):
        vals = [str(v).strip() for v in list(raw.iloc[i].values) if str(v).strip() and str(v).lower().strip() != 'nan']
        if vals:
            lines.append(" | ".join(vals))

    records = []
    current = None
    for ln in lines:
        if looks_like_location(ln):
            if current and ("Location" in current):
                records.append(current)
            current = {"Location": ln.strip()}
            continue
        if current is None:
            continue
        lcl = ln.lower()
        if lcl.startswith("tenant:"):
            current["Tenant"] = ln.split(":",1)[1].strip()
        elif lcl.startswith("area:"):
            current["_Area_raw"] = ln.split(":",1)[1].strip()
            current["Area_Sft"] = parse_area_phrase(current["_Area_raw"])
            m_uds = re.search(r"([\d]+(?:\.\d+)?)\s*(s\.? ?yards?|sq ?yards?|sq ?yds?)", lcl)
            if m_uds:
                try:
                    current["UDS_SqYards"] = float(m_uds.group(1))
                except:
                    pass
        elif lcl.startswith("price:"):
            current["_Price_raw"] = ln.split(":",1)[1].strip()
            current["Price_INR"] = parse_money_phrase(current["_Price_raw"])
        elif lcl.startswith("rent:"):
            current["_Rent_raw"] = ln.split(":",1)[1].strip()
            current["Rent_INR"] = parse_money_phrase(current["_Rent_raw"])
        elif lcl.startswith("lease:"):
            m = re.search(r"(\d+(\.\d+)?)", lcl)
            if m: current["Lease_Years"] = float(m.group(1))
        elif lcl.startswith("advance:"):
            m = re.search(r"(\d+(\.\d+)?)", lcl)
            if m: current["Advance_Months"] = float(m.group(1))
        elif lcl.startswith("increment:"):
            pct, yrs = parse_increment(lcl)
            if pct is not None: current["Increment_%"] = pct
            if yrs is not None: current["Increment_Every_Years"] = yrs
        elif lcl.startswith("uds:"):
            m = re.search(r"(\d+(\.\d+)?)", lcl)
            if m: current["UDS_SqYards"] = float(m.group(1))
    if current and ("Location" in current):
        records.append(current)

    df = pd.DataFrame(records)
    df = fix_rent_units(df)
    df = fix_price_units(df)
    df = fix_area_units(df)
    df["Price_per_Sft"] = (pd.to_numeric(df.get("Price_INR"), errors="coerce") / pd.to_numeric(df.get("Area_Sft"), errors="coerce")).replace([np.inf,-np.inf], np.nan)
    df["Rent_per_Sft"]  = (pd.to_numeric(df.get("Rent_INR"),  errors="coerce") / pd.to_numeric(df.get("Area_Sft"), errors="coerce")).replace([np.inf,-np.inf], np.nan)
    df["Gross_Yield_%"] = (pd.to_numeric(df.get("Rent_INR"), errors="coerce") * 12 / pd.to_numeric(df.get("Price_INR"), errors="coerce") * 100).replace([np.inf,-np.inf], np.nan)
    df.to_excel(out_xlsx, index=False)
    df.to_csv(out_csv, index=False)
    print(f"Wrote {len(df)} rows -> {out_xlsx} and {out_csv}")

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "PROPERTY LIST new.xlsx"
    main(src)
