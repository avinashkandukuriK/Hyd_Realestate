
import json, pandas as pd

def load_property_data(path: str, schema_path: str = "property_schema_v2.json"):
    # load file
    df = pd.read_excel(path) if path.lower().endswith(".xlsx") else pd.read_csv(path)
    # validate columns against schema (required only)
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    # coerce numerics
    for f in schema["fields"]:
        if f.get("type") == "number" and f["name"] in df.columns:
            df[f["name"]] = pd.to_numeric(df[f["name"]], errors="coerce")
    return df
