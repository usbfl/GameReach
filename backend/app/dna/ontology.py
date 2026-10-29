import json
from pathlib import Path

ONTOLOGY = json.loads(Path(__file__).with_name("ontology.json").read_text(encoding="utf-8"))
VERSION = ONTOLOGY["ontology_version"]
RUBRIC = ONTOLOGY["rubric_version"]
FEATURES = ONTOLOGY["features"]
LABELS = {k: v["label"] for k, v in FEATURES.items()}
LEVEL_KEYS = {k for k, v in FEATURES.items() if v["type"] == "ordinal"}


def valid_value(key, value):
    if key not in FEATURES:
        return False
    if value is None:
        return True
    f = FEATURES[key]
    typ = f["type"]
    if typ == "bool":
        return type(value) is bool
    if typ == "ordinal":
        return type(value) is int and 0 <= value <= 4
    if typ == "enum":
        return type(value) is str and value in f["values"]
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) is int for n in value)
        and 0 <= value[0] <= value[1] <= 1440
    )


def describe(key, value):
    f = FEATURES[key]
    if value is None:
        return "还不了解"
    if f["type"] == "bool":
        return ("有" if value else "没有") + f["label"]
    if f["type"] == "ordinal":
        return f["anchors"][value]
    if f["type"] == "enum":
        return f["anchors"][f["values"].index(value)]
    return f"一局约 {value[0]}–{value[1]} 分钟"
