#!/usr/bin/env bash
# Kubernetes の clone、codebase-memory-mcp の導入、インデックス作成と計測。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BASE="$HOME/cbm-verify"; K8S="$BASE/kubernetes"; BIN="$BASE/bin/codebase-memory-mcp"
K8S_TAG=v1.37.2; CBM_TAG=v0.12.1
ASSET=codebase-memory-mcp-darwin-arm64.tar.gz

mkdir -p "$BASE/dl" "$BASE/bin"
[ -d "$K8S/.git" ] || git clone -q --depth 1 --branch "$K8S_TAG" https://github.com/kubernetes/kubernetes "$K8S"
if [ ! -x "$BIN" ]; then
  (cd "$BASE/dl" && gh release download "$CBM_TAG" -R DeusData/codebase-memory-mcp -p "$ASSET" -p checksums.txt --clobber \
    && grep " $ASSET\$" checksums.txt | shasum -a 256 -c - && tar -xzf "$ASSET" -C "$BASE/bin")
fi

GO_FILES=$(cd "$K8S" && find . -name '*.go' -not -path './.git/*' | wc -l | tr -d ' ')
GO_LINES=$(cd "$K8S" && find . -name '*.go' -not -path './.git/*' -print0 | xargs -0 cat | wc -l | tr -d ' ')
DF_BEFORE=$(df -h /System/Volumes/Data | tail -1 | awk '{print $4}')

START=$(python3 -c 'import time;print(time.time())')
"$BIN" cli --json index_repository --repo-path "$K8S" --mode full > "$BASE/index_result.json"
END=$(python3 -c 'import time;print(time.time())')

DB_SIZE=$(du -sh "$HOME/.cache/codebase-memory-mcp" | awk '{print $1}')
DF_AFTER=$(df -h /System/Volumes/Data | tail -1 | awk '{print $4}')
python3 - "$ROOT/verification/index.json" "$BASE/index_result.json" <<PY
import json, sys
out, res = sys.argv[1], sys.argv[2]
d = {"k8s_tag": "$K8S_TAG", "commit_sha": "$(git -C "$K8S" rev-parse HEAD)", "cbm_version": "$CBM_TAG",
     "go_files": int("$GO_FILES"), "go_lines": int("$GO_LINES"), "mode": "full", "method": "cli index_repository",
     "index_seconds": round($END - $START, 1), "index_db_size": "$DB_SIZE",
     "disk_free_before": "$DF_BEFORE", "disk_free_after": "$DF_AFTER"}
try: d["index_result"] = json.load(open(res)).get("structuredContent")
except Exception: pass
json.dump(d, open(out, "w"), ensure_ascii=False, indent=2)
PY
cat "$ROOT/verification/index.json"
