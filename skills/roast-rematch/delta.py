#!/usr/bin/env python3
"""Compare two roasts and report the score change per judge.

Reads two roast.json files written by roast-repo's verdict.py (the previous
one usually lives in roast/history/), writes a markdown rematch report and a
card JSON with "before -> after" values that roast-card's card.py can render.

Charges are matched by their evidence text, so a jab whose evidence moved to
another line counts as both "dropped" and "new". The report says so.

Usage:
    python3 delta.py --before roast/history/roast-2026-11-01.json \
        --after roast/roast.json --out roast/REMATCH.md --card-out roast/card.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ARCHIVE_NAME = re.compile(r"roast-\d{4}-\d{2}-\d{2}(?:-\d+)?\.json")


def load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "judges" not in data:
        raise ValueError(f"{path} does not look like a roast.json (no 'judges')")
    return data


def _archive_key(path: Path) -> tuple[str, int, str]:
    match = re.fullmatch(r"roast-(\d{4}-\d{2}-\d{2})(?:-(\d+))?", path.stem)
    if match:
        return match.group(1), int(match.group(2) or 1), path.name
    return "", 0, path.name


def history_files(after_path: str) -> list[Path]:
    history = Path(after_path).resolve().parent / "history"
    return sorted((path for path in history.glob("*.json") if ARCHIVE_NAME.fullmatch(path.name)),
                  key=_archive_key)


def select_baseline(after_path: str, since_day_one: bool = False) -> Path:
    files = history_files(after_path)
    if not files:
        raise FileNotFoundError(f"no archived roasts found beside {after_path}")
    return files[0] if since_day_one else files[-1]


def signed(d: float) -> str:
    d = round(d, 1)
    return f"+{d:.1f}" if d > 0 else (f"{d:.1f}" if d < 0 else "0.0")


def compare(before: dict, after: dict) -> dict:
    b_judges = {j["judge"]: j for j in before.get("judges", [])}
    a_judges = {j["judge"]: j for j in after.get("judges", [])}
    order = [j["judge"] for j in after.get("judges", [])] + \
            [k for k in b_judges if k not in a_judges]
    rows = []
    for key in order:
        b, a = b_judges.get(key), a_judges.get(key)
        rows.append({
            "judge": key,
            "persona": (a or b).get("persona", key.title()),
            "before": b["score"] if b else None,
            "after": a["score"] if a else None,
            "delta": round(a["score"] - b["score"], 1) if a and b else None,
            "roasts_before": len(b.get("roasts", [])) if b else 0,
            "roasts_after": len(a.get("roasts", [])) if a else 0,
        })
    b_ev = {r["evidence"]: (j["persona"], r) for j in before.get("judges", []) for r in j.get("roasts", [])}
    a_ev = {r["evidence"]: (j["persona"], r) for j in after.get("judges", []) for r in j.get("roasts", [])}
    dropped = [{"persona": p, **r} for ev, (p, r) in b_ev.items() if ev not in a_ev]
    new = [{"persona": p, **r} for ev, (p, r) in a_ev.items() if ev not in b_ev]
    return {
        "repo": after.get("repo", before.get("repo", "this repo")),
        "before_date": before.get("date", "?"), "after_date": after.get("date", "?"),
        "overall_before": before.get("overall", 0.0), "overall_after": after.get("overall", 0.0),
        "overall_delta": round(after.get("overall", 0.0) - before.get("overall", 0.0), 1),
        "tier_before": before.get("tier", ""), "tier_after": after.get("tier", ""),
        "rows": rows, "dropped": dropped, "new": new,
        "persisting": len([ev for ev in b_ev if ev in a_ev]),
    }


def biggest_mover(rows: list[dict]) -> dict | None:
    moved = [r for r in rows if r["delta"] is not None]
    return max(moved, key=lambda r: (r["delta"], r["judge"])) if moved else None


def render_markdown(c: dict) -> str:
    o = [f"# Rematch: {c['repo']}", ""]
    o.append(f"**Overall: {c['overall_before']:.1f} → {c['overall_after']:.1f} ({signed(c['overall_delta'])})**"
             + (f" · {c['tier_before']} → {c['tier_after']}" if c["tier_before"] or c["tier_after"] else ""))
    o.append("")
    o.append(f"Before: {c['before_date']} · after: {c['after_date']}")
    o.append("")
    o.append("| Judge | Before | After | Change | Roasts before | Roasts after |")
    o.append("|---|---|---|---|---|---|")
    for r in c["rows"]:
        b = "absent" if r["before"] is None else f"{r['before']:.1f}"
        a = "absent" if r["after"] is None else f"{r['after']:.1f}"
        d = "n/a" if r["delta"] is None else signed(r["delta"])
        o.append(f"| {r['persona']} ({r['judge']}) | {b} | {a} | {d} | {r['roasts_before']} | {r['roasts_after']} |")
    mover = biggest_mover(c["rows"])
    if mover and mover["delta"] > 0:
        o.append("")
        o.append(f"Most improved: {mover['persona']} ({signed(mover['delta'])}).")
    o.append("")
    o.append(f"## Charges dropped ({len(c['dropped'])})")
    o.append("")
    if c["dropped"]:
        for r in c["dropped"]:
            o.append(f"- {r['persona']}: {r['line']} (was `{r['evidence']}`)")
    else:
        o.append("- None. Every old charge is still on the sheet.")
    o.append("")
    o.append(f"## New charges ({len(c['new'])})")
    o.append("")
    if c["new"]:
        for r in c["new"]:
            o.append(f"- {r['persona']}: {r['line']} (`{r['evidence']}`)")
    else:
        o.append("- None.")
    o.append("")
    o.append(f"Charges still standing: {c['persisting']}. Charges are matched by evidence text, so a jab whose "
             "line number moved shows up as both dropped and new; check those by hand before celebrating.")
    o.append("")
    return "\n".join(o)


def card_payload(before: dict, after: dict) -> dict:
    prev = {j["judge"]: j["score"] for j in before.get("judges", [])}
    return {
        "repo": after.get("repo", "this repo"), "overall": after.get("overall", 0.0),
        "previous_overall": before.get("overall", 0.0), "headline": after.get("headline", ""),
        "date": after.get("date", ""), "mode": after.get("mode", "default"),
        "judges": [{"name": j.get("persona", j["judge"].title()), "role": j["judge"].title(),
                    "score": j["score"], "quote": j.get("verdict", ""),
                    **({"previous": prev[j["judge"]]} if j["judge"] in prev else {})}
                   for j in after.get("judges", [])],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compare two roast.json files and report per-judge score deltas.")
    p.add_argument("--before", help="explicit previous roast.json; defaults to the latest archive")
    p.add_argument("--since-day-one", action="store_true",
                   help="compare against the oldest archived roast instead of the latest")
    p.add_argument("--after", required=True, help="new roast.json")
    p.add_argument("--out", help="write the markdown report here (default: stdout)")
    p.add_argument("--card-out", help="write a before/after card JSON for card.py here")
    p.add_argument("--json", action="store_true", help="print the comparison as JSON instead of markdown")
    args = p.parse_args(argv)
    try:
        if args.before and args.since_day_one:
            raise ValueError("--before cannot be combined with --since-day-one")
        before_path = Path(args.before) if args.before else select_baseline(args.after, args.since_day_one)
        before, after = load(str(before_path)), load(args.after)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    comparison = compare(before, after)
    text = json.dumps(comparison, indent=2, ensure_ascii=False) + "\n" if args.json else render_markdown(comparison)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {Path(args.out).as_posix()}")
    else:
        sys.stdout.write(text)
    if args.card_out:
        Path(args.card_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.card_out).write_text(json.dumps(card_payload(before, after), indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
        print(f"wrote {Path(args.card_out).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
