#!/usr/bin/env bash
# ラウンド 2（公式の入れ方 + CLAUDE.md で MCP を使うよう指示）
#   run2.sh <task> <rep> [prompt_file]   B を 1 回実行（ログ: verification/round2/runs/<task>-B-<rep>.jsonl）
#   run2.sh all                          本番 18 回（T1..T6 × 3 回）。既にログがあるものは飛ばす
# A（MCP なし）はラウンド 1 と同じ条件なので、ラウンド 1 のログを round2/runs にコピーして使う。
# B: ラウンド 1 の B + Kubernetes のルートに CLAUDE.md（verification/round2/CLAUDE.md）。
#    CLAUDE.md を読み込ませるため --setting-sources project にする（k8s には .claude/ がないので、増えるのは CLAUDE.md だけ。
#    CLAUDE.md があると AGENTS.md は読み込まれない）。
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
K8S="$HOME/cbm-verify/kubernetes"
OFFICIAL="$HOME/cbm-verify/official"
RUNS="$ROOT/verification/round2/runs"

cp "$ROOT/verification/round2/CLAUDE.md" "$K8S/CLAUDE.md"
trap 'rm -f "$K8S/CLAUDE.md"' EXIT

run_one() {
  local task=$1 rep=$2 prompt_file=${3:-"$ROOT/verification/round2/tasks/$1.txt"}
  local out="$RUNS/$task-B-$rep.jsonl"
  local flags=(--model claude-opus-5-5 --output-format stream-json --verbose --no-session-persistence
    --setting-sources project --strict-mcp-config
    --permission-mode bypassPermissions --disallowedTools Edit,Write,NotebookEdit
    --mcp-config "$OFFICIAL/mcp.json" --settings "$OFFICIAL/settings.json" --plugin-dir "$OFFICIAL/plugin")
  mkdir -p "$RUNS"
  (cd "$K8S" && claude -p "$(cat "$prompt_file")" "${flags[@]}" < /dev/null > "$out" 2> "${out%.jsonl}.stderr")
  local code=$?
  [ -s "${out%.jsonl}.stderr" ] || rm -f "${out%.jsonl}.stderr"
  echo "$task-B-$rep exit=$code"
}

if [ "${1:-}" = all ]; then
  for task in T1 T2 T3 T4 T5 T6; do
    for rep in 1 2 3; do
      [ -s "$RUNS/$task-B-$rep.jsonl" ] && grep -q '"type":"result"' "$RUNS/$task-B-$rep.jsonl" && continue
      run_one "$task" "$rep"
    done
  done
else
  run_one "$@"
fi
