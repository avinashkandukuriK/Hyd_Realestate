
import sys, json, pandas as pd, math, numpy as np

SCHEMA_PATH = "property_schema_v2.json"

BLANKS = {"", "nan", "none", "null", None}

def load_schema(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def is_blank(x):
    if x is None:
        return True
    s = str(x).strip().lower()
    return s in BLANKS

def try_float(x):
    try:
        if is_blank(x):
            return None
        return float(x)
    except Exception:
        return None

def validate(df: pd.DataFrame, schema: dict):
    errs = []
    fields = {f["name"]: f for f in schema["fields"]}

    # ensure columns exist (only for required columns)
    for f in schema["fields"]:
        if f.get("required", False) and f["name"] not in df.columns:
            errs.append(f"Missing required column in file: {f['name']}")

    # row-level validation
    for idx, row in df.iterrows():
        rownum = idx + 1

        for f in schema["fields"]:
            name = f["name"]; required = f.get("required", False)
            val = row.get(name) if name in df.columns else None

            # check required
            if required and is_blank(val):
                errs.append(f"Row {rownum}: required field '{name}' is empty")
                continue  # no more checks for this field

            # numeric checks (only if provided)
            if f.get("type") == "number" and name in df.columns and not is_blank(val):
                num = try_float(val)
                if num is None:
                    errs.append(f"Row {rownum}: field '{name}' should be numeric")
                else:
                    if "min" in f and num < f["min"]:
                        errs.append(f"Row {rownum}: field '{name}' ({num}) < min {f['min']}")
                    if "max" in f and num > f["max"]:
                        errs.append(f"Row {rownum}: field '{name}' ({num}) > max {f['max']}")
            # string enum removed for 'Floor' to allow free text or blanks

    return errs

def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_properties_v2.py <excel-or-csv-path>")
        sys.exit(1)
    path = sys.argv[1]
    if path.lower().endswith(".csv"):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path)
    schema = load_schema(SCHEMA_PATH)
    errors = validate(df, schema)
    if errors:
        print(f"❌ Validation failed with {len(errors)} issue(s):")
        for e in errors[:300]:
            print("-", e)
        if len(errors) > 300:
            print("... (truncated)")
        sys.exit(2)
    else:
        print("✅ Validation passed. Rows:", len(df))

if __name__ == "__main__":
    main()
