#!/usr/bin/env python3
"""ブラインド採点を `claude -p`（ヘッドレス、読み取り専用）で行う。サブエージェントを使わないので許可の確認が出ない。

  python3 -I verification/scripts/judge.py --round verification/round1 T1 T2 ...
  → <round>/results/blind/<Tn>_scores.json
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path

K8S = os.path.expanduser("~/cbm-verify/kubernetes")

PROMPT = """あなたはコードレビューに慣れた Kubernetes のエキスパートです。同じ質問に対する複数の回答を、採点観点に沿って採点してください。

- 質問: 「{question}」
- 採点観点: {rubrics} の「共通の採点基準」と「{task}」のセクション
- 正解の一覧（T4〜T6）: {ground_truth}
- 回答: {answers}
- ソース（裏取り用。読み取りのみ）: カレントディレクトリ（kubernetes v1.37.2）

注意:
- 「[tool]」は伏せ字です。回答の中の「どう調べたか」に関する記述は採点の対象外です。中身だけで判断してください。
- 回答中の具体的なファイル・行・関数名・件数は、怪しいものを中心にソースで確認し、誤りは減点してください。
- 各回答を独立に、同じ基準で採点してください。
- ファイルは編集しないでください。

最後に、次の形式の JSON だけを ```json ... ``` のコードブロックで出力してください（コメントは日本語で 1〜2 文、満たした要点・抜け・誤りを具体的に）:
[{{"answer": 1, "score": 4, "comment": "..."}}, ...]
"""


def judge(round_dir: Path, task: str):
    blind = round_dir / "results" / "blind"
    prompt = PROMPT.format(
        question=(round_dir / "tasks" / f"{task}.txt").read_text().strip(),
        rubrics=(round_dir / "rubrics.md").resolve(),
        ground_truth=(round_dir / "ground_truth.md").resolve(),
        answers=(blind / f"{task}_answers.md").resolve(),
        task=task,
    )
    r = subprocess.run(
        ["claude", "-p", prompt, "--model", "claude-opus-5-5", "--output-format", "json",
         "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config",
         "--permission-mode", "bypassPermissions", "--disallowedTools", "Edit,Write,NotebookEdit",
         "--add-dir", str(round_dir.resolve())],
        cwd=K8S, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    text = json.loads(r.stdout)["result"]
    (blind / f"{task}_judge_raw.md").write_text(text)
    m = re.findall(r"```json\s*(\[.*?\])\s*```", text, re.S)
    scores = json.loads(m[-1])
    (blind / f"{task}_scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1) + "\n")
    print(task, [s["score"] for s in scores])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", default="verification/round1")
    ap.add_argument("tasks", nargs="+")
    a = ap.parse_args()
    for t in a.tasks:
        judge(Path(a.round), t)


if __name__ == "__main__":
    main()
