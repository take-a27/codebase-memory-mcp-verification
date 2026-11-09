#!/usr/bin/env bash
# ラウンド 1（公式の入れ方。使うかどうかは Claude に任せる）
#   run1.sh <task> <A|B> <rep> [prompt_file]   1 回実行（ログ: verification/round1/runs/<task>-<cond>-<rep>.jsonl）
#   run1.sh all                                本番 36 回（T1..T6 × 3 回 × A/B 交互）。既にログがあるものは飛ばす
# A: 素の Claude Code（ユーザーのプラグイン・hooks なし、組み込みのスキルはあり）
# B: A + 公式インストーラーが入れるもの（MCP・スキル・エージェント・hooks）。追加の誘導文はなし
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
K8S="$HOME/cbm-verify/kubernetes"
OFFICIAL="$HOME/cbm-verify/official"
RUNS="$ROOT/verification/round1/runs"

run_one() {
  local task=$1 cond=$2 rep=$3 prompt_file=${4:-"$ROOT/verification/round1/tasks/$1.txt"}
  local out="$RUNS/$task-$cond-$rep.jsonl"
  local flags=(--model claude-opus-5-5 --output-format stream-json --verbose --no-session-persistence
    --setting-sources "" --strict-mcp-config
    --permission-mode bypassPermissions --disallowedTools Edit,Write,NotebookEdit)
  if [ "$cond" = B ]; then
    flags+=(--mcp-config "$OFFICIAL/mcp.json" --settings "$OFFICIAL/settings.json" --plugin-dir "$OFFICIAL/plugin")
  fi
  mkdir -p "$RUNS"
  (cd "$K8S" && claude -p "$(cat "$prompt_file")" "${flags[@]}" < /dev/null > "$out" 2> "${out%.jsonl}.stderr")
  local code=$?
  [ -s "${out%.jsonl}.stderr" ] || rm -f "${out%.jsonl}.stderr"
  echo "$task-$cond-$rep exit=$code"
}

if [ "${1:-}" = all ]; then
  for task in T1 T2 T3 T4 T5 T6; do
    for rep in 1 2 3; do
      for cond in A B; do
        [ -s "$RUNS/$task-$cond-$rep.jsonl" ] && grep -q '"type":"result"' "$RUNS/$task-$cond-$rep.jsonl" && continue
        run_one "$task" "$cond" "$rep"
      done
    done
  done
else
  run_one "$@"
fi
