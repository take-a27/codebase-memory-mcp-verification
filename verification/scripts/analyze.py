#!/usr/bin/env python3
"""生ログ（stream-json）の集計とブラインド評価用ファイルの作成。

  python3 verification/scripts/analyze.py summary [--runs DIR]
  python3 verification/scripts/analyze.py blind   [--runs DIR] [--seed 20261108]
  python3 verification/scripts/analyze.py scores   # 採点結果のラベルを戻して条件別に集計
"""
import argparse
import csv
import json
import random
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "verification" / "round1" / "results"
RUN_ID = re.compile(r"^(?P<task>.+)-(?P<cond>[AB])-(?P<rep>\d+)$")


def load_events(path):
    events = []
    for line in Path(path).read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def tool_uses(events):
    for e in events:
        if e.get("type") == "assistant":
            for c in e.get("message", {}).get("content", []) or []:
                if c.get("type") == "tool_use":
                    yield c


def tool_result_chars(events):
    """ツール結果としてコンテキストに入った文字数（実際に読み込んだ量の目安）。"""
    total = 0
    for e in events:
        content = e.get("message", {}).get("content") if e.get("type") == "user" else None
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_result":
                t = c.get("content")
                total += len(t if isinstance(t, str) else json.dumps(t, ensure_ascii=False))
    return total


def parse_run(path):
    path = Path(path)
    m = RUN_ID.match(path.stem)
    events = load_events(path)
    names = [c.get("name", "") for c in tool_uses(events)]
    results = [e for e in events if e.get("type") == "result"]
    row = {
        "run_id": path.stem,
        "task": m["task"] if m else path.stem,
        "cond": m["cond"] if m else "",
        "rep": int(m["rep"]) if m else 0,
        "status": "ok",
        "total_tokens": None,
        "duration_s": None,
        "tool_calls": len(names),
        "mcp_calls": sum(n.startswith("mcp__") for n in names),
        "reindex_calls": sum(n.endswith("__index_repository") for n in names),
        "cost_usd": None,
        "turns": None,
        "tool_result_chars": tool_result_chars(events),
        "answer": "",
    }
    if not results or results[-1].get("is_error"):
        row["status"] = "error"
        return row
    r = results[-1]
    row["total_tokens"] = sum(
        u.get("inputTokens", 0) + u.get("outputTokens", 0)
        + u.get("cacheReadInputTokens", 0) + u.get("cacheCreationInputTokens", 0)
        for u in (r.get("modelUsage") or {}).values()
    )
    row["duration_s"] = round(r.get("duration_ms", 0) / 1000, 1)
    row["cost_usd"] = round(r.get("total_cost_usd") or 0, 4)
    row["turns"] = r.get("num_turns")
    row["answer"] = r.get("result", "")
    return row


def load_rows(runs_dir):
    return [parse_run(p) for p in sorted(Path(runs_dir).glob("*.jsonl")) if ".fail" not in p.name]


def fmt(x):
    if x is None:
        return "-"
    if x >= 100:
        return f"{x:,.0f}"
    return f"{x:.1f}" if x >= 1 or x == 0 else f"{x:.3f}"


METRICS = [("total_tokens", "総トークン"), ("duration_s", "時間(秒)"),
           ("tool_calls", "ツール呼び出し"), ("mcp_calls", "MCP 呼び出し"),
           ("turns", "ターン数"), ("tool_result_chars", "読み込んだ文字数"), ("cost_usd", "コスト$(参考)")]


def summary(runs_dir):
    rows = load_rows(runs_dir)
    RESULTS.mkdir(parents=True, exist_ok=True)
    cols = ["run_id", "task", "cond", "rep", "status", "total_tokens", "duration_s",
            "tool_calls", "mcp_calls", "reindex_calls", "turns", "tool_result_chars", "cost_usd"]
    with open(RESULTS / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    lines = ["# 集計（平均 [最小〜最大]）", "",
             "| タスク | 条件 | n | " + " | ".join(label for _, label in METRICS) + " |",
             "|---|---|---|" + "---|" * len(METRICS)]
    means = {}
    for task in sorted({r["task"] for r in rows}):
        for cond in ("A", "B"):
            ok = [r for r in rows if r["task"] == task and r["cond"] == cond and r["status"] == "ok"]
            if not ok:
                continue
            cells = []
            for key, _ in METRICS:
                vals = [r[key] for r in ok]
                mean = statistics.mean(vals)
                means[(task, cond, key)] = mean
                cells.append(f"{fmt(mean)} [{fmt(min(vals))}〜{fmt(max(vals))}]")
            lines.append(f"| {task} | {cond} | {len(ok)} | " + " | ".join(cells) + " |")

    lines += ["", "## B ÷ A（平均の比）", "", "| タスク | 総トークン | 時間 | ツール呼び出し | ターン数 | 読み込んだ文字数 | コスト |", "|---|---|---|---|---|---|---|"]
    for task in sorted({r["task"] for r in rows}):
        ratios = []
        for key in ("total_tokens", "duration_s", "tool_calls", "turns", "tool_result_chars", "cost_usd"):
            a, b = means.get((task, "A", key)), means.get((task, "B", key))
            ratios.append(f"{b / a:.2f}x" if a and b is not None else "-")
        lines.append(f"| {task} | " + " | ".join(ratios) + " |")

    warns = [f"- {r['run_id']}: error" for r in rows if r["status"] == "error"]
    warns += [f"- {r['run_id']}: MCP を一度も呼んでいない" for r in rows
              if r["cond"] == "B" and r["status"] == "ok" and r["mcp_calls"] == 0]
    warns += [f"- {r['run_id']}: index_repository を {r['reindex_calls']} 回呼んだ" for r in rows if r["reindex_calls"]]
    lines += ["", "## 注意", ""] + (warns or ["- なし"])
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def redact(text, tool_names):
    text = re.sub(r"codebase-memory\S*", "[tool]", text)
    text = re.sub(r"mcp__\S+", "[tool]", text)
    for name in sorted(tool_names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", "[tool]", text)
    text = re.sub(r"コードグラフ|グラフ|\b(code )?graph\b|\bscout\b|\bauditor\b", "[tool]", text, flags=re.IGNORECASE)
    return re.sub(r"\bcbm\b", "[tool]", text, flags=re.IGNORECASE)


def cbm_tool_names(runs_dir):
    """B 条件の init イベントから MCP ツール名（mcp__<server>__<tool> の <tool> 部分）を取り出す。"""
    for p in sorted(Path(runs_dir).glob("*-B-*.jsonl")):
        for e in load_events(p):
            if e.get("type") == "system" and e.get("subtype") == "init":
                return {t.split("__", 2)[2] for t in e.get("tools", []) if t.startswith("mcp__")}
    return set()


def blind(runs_dir, seed):
    rows = [r for r in load_rows(runs_dir) if r["status"] == "ok"]
    names = cbm_tool_names(runs_dir)
    out = RESULTS / "blind"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    key = {}
    for task in sorted({r["task"] for r in rows}):
        items = [r for r in rows if r["task"] == task]
        rng.shuffle(items)
        key[task] = {str(i): r["run_id"] for i, r in enumerate(items, 1)}
        body = [f"# {task} の回答（{len(items)} 件）", ""]
        for i, r in enumerate(items, 1):
            body += [f"## Answer {i}", "", redact(r["answer"], names).strip(), ""]
        (out / f"{task}_answers.md").write_text("\n".join(body))
    (out / "key.json").write_text(json.dumps(key, indent=2) + "\n")
    print(f"wrote {out} ({sum(len(v) for v in key.values())} answers)")


def scores():
    out = RESULTS / "blind"
    key = json.loads((out / "key.json").read_text())
    rows = []
    for task, mapping in key.items():
        for s in json.loads((out / f"{task}_scores.json").read_text()):
            run_id = mapping[str(s["answer"])]
            m = RUN_ID.match(run_id)
            rows.append({"run_id": run_id, "task": task, "cond": m["cond"], "rep": int(m["rep"]),
                         "answer": s["answer"], "score": s["score"], "comment": s["comment"]})
    rows.sort(key=lambda r: r["run_id"])
    with open(out / "scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    lines = ["| タスク | A（MCP なし） | B（MCP あり） |", "|---|---|---|"]
    for task in sorted(key):
        cells = []
        for cond in ("A", "B"):
            vals = [r["score"] for r in rows if r["task"] == task and r["cond"] == cond]
            cells.append(f"{statistics.mean(vals):.2f} ({', '.join(map(str, vals))})")
        lines.append(f"| {task} | " + " | ".join(cells) + " |")
    print("\n".join(lines))


def main():
    global RESULTS
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["summary", "blind", "scores"])
    ap.add_argument("--runs", default=str(ROOT / "verification" / "round1" / "runs"))
    ap.add_argument("--seed", type=int, default=20261108)
    ap.add_argument("--results", default=str(RESULTS), help="出力先（ラウンド 2 は verification/round2/results）")
    a = ap.parse_args()
    RESULTS = Path(a.results)
    if a.cmd == "summary":
        summary(a.runs)
    elif a.cmd == "blind":
        blind(a.runs, a.seed)
    else:
        scores()


if __name__ == "__main__":
    main()
