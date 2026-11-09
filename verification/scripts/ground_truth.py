#!/usr/bin/env python3
"""お題 T4〜T6 の正解を、Kubernetes のソースから機械的に作る。

  python3 -I verification/scripts/ground_truth.py   # → verification/round1/ground_truth.md
"""
import os
import re
import subprocess

K8S = os.path.expanduser("~/cbm-verify/kubernetes")


def git_grep(*args, path="*.go"):
    r = subprocess.run(["git", "-C", K8S, "grep", "-n", *args, "--", path, ":!vendor"],
                       capture_output=True, text=True)
    return [line for line in r.stdout.splitlines() if line]


def enclosing_func(path, lineno):
    last = ""
    with open(os.path.join(K8S, path), errors="ignore") as f:
        for i, line in enumerate(f, 1):
            if line.startswith("func "):
                last = line.strip()
            if i == lineno:
                return last[:110]
    return last[:110]


def t4():
    print("## T4: `pkg/api/v1/pod.IsPodAvailable` の呼び出し元\n")
    for line in git_grep("-E", r"\.IsPodAvailable\("):
        path, ln, _ = line.split(":", 2)
        if path.startswith("staging/") or path.endswith("_test.go"):
            continue
        src = open(os.path.join(K8S, path), errors="ignore").read()
        m = re.search(r'^\s*(\w+)\s+"k8s.io/kubernetes/pkg/api/v1/pod"', src, re.M)
        alias = m.group(1) if m else None
        call = line.split(":", 2)[2]
        if not alias or f"{alias}.IsPodAvailable(" not in call:
            continue
        print(f"- {path}:{ln} — `{enclosing_func(path, int(ln))}`")
    print("\n2 段目（statefulset のラッパー関数の呼び出し元）\n")
    for fn in ("isRunningAndAvailable", "isUnavailable"):
        for line in git_grep("-wE", fn, path="pkg/controller/statefulset/*.go"):
            path, ln, code = line.split(":", 2)
            if path.endswith("_test.go") or code.lstrip().startswith("func "):
                continue
            print(f"- {fn}: {path}:{ln}")
    print()


def t5():
    print("## T5: `pkg/controller` 配下の exported なトップレベル関数で、本番コードから参照されていないもの\n")
    print("分類: unused = 定義ファイル内でも本番コードでも使われていない / same-file = 定義ファイル内でだけ使われている /")
    print("test-only = テストからだけ参照 / test-helper = テスト用パッケージ（testing/, testutil/, test/, fake_*）の関数\n")
    print("| 関数 | 定義 | 分類 | 定義ファイル内の使用（定義・コメント除く） | テストからの参照ファイル数 |")
    print("|---|---|---|---|---|")
    for d, _, files in os.walk(os.path.join(K8S, "pkg/controller")):
        for f in sorted(files):
            if not f.endswith(".go") or f.endswith("_test.go") or f.startswith("zz_generated"):
                continue
            full = os.path.join(d, f)
            rel = os.path.relpath(full, K8S)
            src = open(full, errors="ignore").read()
            for m in re.finditer(r"^func ([A-Z]\w*)\(", src, re.M):
                name = m.group(1)
                r = subprocess.run(["git", "-C", K8S, "grep", "-lw", name, "--", "*.go", ":!vendor"],
                                   capture_output=True, text=True).stdout.split()
                prod = [x for x in r if not x.endswith("_test.go")]
                if prod != [rel]:
                    continue
                in_file = sum(len(re.findall(rf"\b{name}\b", l)) for l in src.splitlines()
                              if not l.lstrip().startswith("//") and not l.startswith(f"func {name}("))
                tests = len(r) - 1
                helper = re.search(r"/(testing|testutil|test)/|/fake_", rel)
                kind = ("test-helper" if helper else "same-file" if in_file else
                        "test-only" if tests else "unused")
                print(f"| `{name}` | {rel} | {kind} | {in_file} | {tests} |")
    print()


def t6():
    print("## T6: `FilterPlugin` の実装（`Filter(ctx context.Context, ...)` メソッドを持つ型）\n")
    for line in git_grep("-E", r"^func \([A-Za-z_]+ \*?[A-Za-z_]+\) Filter\(ctx context\.Context",
                         path="pkg/scheduler/*.go"):
        path, ln, code = line.split(":", 2)
        if path.endswith("_test.go"):
            continue
        recv = re.match(r"func \(\w+ (\*?\w+)\)", code).group(1)
        print(f"- {path}:{ln} — `{recv}`")
    print()


if __name__ == "__main__":
    import sys
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "round1", "ground_truth.md")
    sys.stdout = open(out, "w")
    print("# 正解（kubernetes v1.37.2 から機械的に抽出）\n")
    t4()
    t5()
    t6()
