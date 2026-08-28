#!/usr/bin/env python3
"""Merges old memory entries into current finteza_memory.json"""
import json, os

PROJECT = "/Users/danildocenko/PycharmProjects/dashboard-similarcams"
MEMORY_FILE = os.path.join(PROJECT, "finteza_memory.json")
PATCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "old_entries_patch.json")

with open(MEMORY_FILE, encoding="utf-8") as f:
    current = json.load(f)

with open(PATCH_FILE, encoding="utf-8") as f:
    patch = json.load(f)

current_dates = {e["date"] for e in current.get("entries", [])}

added = []
for entry in patch.get("entries", []):
    if entry["date"] not in current_dates:
        current["entries"].append(entry)
        added.append(entry["date"])

current["entries"].sort(key=lambda x: x["date"])

for key in ("model_rankings", "shorts_rankings"):
    current.setdefault(key, {})
    for date, val in patch.get(key, {}).items():
        if date not in current[key]:
            current[key][date] = val

with open(MEMORY_FILE, "w", encoding="utf-8") as f:
    json.dump(current, f, ensure_ascii=False, indent=2)

print(f"✓ Added entries: {added}")
print(f"✓ Saved → {MEMORY_FILE}")
