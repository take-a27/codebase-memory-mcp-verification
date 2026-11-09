#!/usr/bin/env bash
# 検証の準備: codebase-memory-mcp の公式インストーラーを「偽の HOME」に対して実行し、
# 入ったもの（MCP 設定・スキル・エージェント・hooks）を、検証の実行時だけ読み込める形にまとめる。
# 普段の ~/.claude は一切変更しない。
set -euo pipefail
BASE="$HOME/cbm-verify"
SRC_BIN="$BASE/bin/codebase-memory-mcp"
FAKEHOME="$BASE/fakehome"
OUT="$BASE/official"

mkdir -p "$FAKEHOME"
HOME="$FAKEHOME" "$SRC_BIN" install -y --clients=claude > "$BASE/fakehome-install.log" 2>&1

rm -rf "$OUT"
mkdir -p "$OUT/plugin/.claude-plugin"
# スキルとエージェントは、プラグインの形にして --plugin-dir で読み込む
cp -R "$FAKEHOME/.claude/skills" "$FAKEHOME/.claude/agents" "$OUT/plugin/"
cat > "$OUT/plugin/.claude-plugin/plugin.json" <<'JSON'
{"name": "codebase-memory", "version": "0.12.1", "description": "codebase-memory-mcp installer artifacts (skill + agents)"}
JSON
# hooks はインストーラーが書いた settings.json をそのまま --settings で読み込む
cp "$FAKEHOME/.claude/settings.json" "$OUT/settings.json"
# MCP サーバーはインストーラーと同じ名前（codebase-memory-mcp）で --mcp-config に渡す
python3 -I - "$FAKEHOME/.claude.json" "$OUT/mcp.json" <<'PY'
import json, sys
servers = json.load(open(sys.argv[1]))["mcpServers"]
json.dump({"mcpServers": servers}, open(sys.argv[2], "w"), indent=1)
PY

# hooks のグラフ補助にはデーモンが必要（hook がそう案内する）。実ユーザーの HOME で起動し、実際のインデックスを使う
"$FAKEHOME/.local/bin/codebase-memory-mcp" daemon start
"$FAKEHOME/.local/bin/codebase-memory-mcp" daemon status
echo "official config: $OUT"
