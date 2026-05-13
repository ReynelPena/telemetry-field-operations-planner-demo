from __future__ import annotations

import sqlite3
from typing import Any

from app.services.recommendation import normalize_text, tokenize


def answer_question(conn: sqlite3.Connection, question: str) -> dict[str, Any]:
    q = question.strip()
    tokens = tokenize(q)
    rules = []

    # First try structured matching against existing rules.
    for row in conn.execute("SELECT * FROM knowledge_rules WHERE active = 1").fetchall():
        rule = {key: row[key] for key in row.keys()}
        haystack = " ".join(
            [
                rule.get("title", ""),
                rule.get("triggers", ""),
                rule.get("vehicle_make", ""),
                rule.get("vehicle_model", ""),
                rule.get("fuel_type", ""),
                rule.get("required_tools", ""),
                rule.get("required_materials", ""),
                rule.get("checklist", ""),
                rule.get("risk_notes", ""),
            ]
        )
        overlap = tokens & tokenize(haystack)
        score = len(overlap)
        if normalize_text(rule.get("vehicle_make")) and normalize_text(rule.get("vehicle_make")) in normalize_text(q):
            score += 4
        if normalize_text(rule.get("vehicle_model")) and normalize_text(rule.get("vehicle_model")) in normalize_text(q):
            score += 4
        if normalize_text(rule.get("fuel_type")) and normalize_text(rule.get("fuel_type")) in normalize_text(q):
            score += 3
        if score > 0:
            rule["bot_score"] = score
            rule["matched_terms"] = ", ".join(sorted(overlap))
            rules.append(rule)

    rules.sort(key=lambda item: item["bot_score"], reverse=True)
    rules = rules[:5]

    vehicles = []
    for row in conn.execute("SELECT * FROM vehicles").fetchall():
        vehicle = {key: row[key] for key in row.keys()}
        haystack = " ".join([vehicle.get("make", ""), vehicle.get("model", ""), vehicle.get("fuel_type", ""), vehicle.get("skill_tags", ""), vehicle.get("notes", "")])
        overlap = tokens & tokenize(haystack)
        score = len(overlap)
        if score > 0:
            vehicle["bot_score"] = score
            vehicles.append(vehicle)
    vehicles.sort(key=lambda item: item["bot_score"], reverse=True)
    vehicles = vehicles[:3]

    if not rules and not vehicles:
        return {
            "question": q,
            "answer": "I could not find an exact technical rule yet. Add a rule in the Knowledge Base with triggers such as make, model, fuel type, recommended tools, materials, checklist, and risk notes.",
            "rules": [],
            "vehicles": [],
        }

    answer_parts = []
    if vehicles:
        v = vehicles[0]
        answer_parts.append(
            f"Detected vehicle profile: {v['make']} {v['model']} {v['fuel_type']} - difficulty {v['complexity']}/5, estimated time {v['estimated_minutes']} min."
        )
        if v.get("notes"):
            answer_parts.append(v["notes"])

    for rule in rules[:3]:
        if rule.get("required_materials"):
            answer_parts.append(f"Recommended materials: {rule['required_materials']}.")
        if rule.get("required_tools"):
            answer_parts.append(f"Recommended tools: {rule['required_tools']}.")
        if rule.get("checklist"):
            answer_parts.append(f"Checklist: {rule['checklist']}")
        if rule.get("risk_notes"):
            answer_parts.append(f"Risk / note: {rule['risk_notes']}")

    return {
        "question": q,
        "answer": "\n\n".join(answer_parts),
        "rules": rules,
        "vehicles": vehicles,
    }
