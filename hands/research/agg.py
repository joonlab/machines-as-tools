#!/usr/bin/env python3
"""research 도구의 T0 집계기 — 클론 기기에서 돈다(LLM 0). research.py 가 base64 로 실어 보낸다.

사용법(원격): python3 agg.py <clone-url> <workdir> <허용 루트>
  workdir(예: /tmp/mmwork/research/<repo>) 에 git clone --depth 50 (이미 있으면 fetch+reset 로 갱신) 후
  파일 수·언어별 줄 수·테스트 파일 수·최근 커밋 20개·라이선스·버전·문서 후보를 JSON 한 줄로 stdout 에 낸다.
표준 라이브러리만(Python 3.8+). <허용 루트> 밖에는 쓰지 않는다.
"""
import collections
import json
import os
import re
import subprocess
import sys
import time

url, wd = sys.argv[1], os.path.realpath(sys.argv[2])
root = os.path.realpath(sys.argv[3]) if len(sys.argv) > 3 else os.path.realpath("/tmp/mmwork/research")
assert wd.startswith(root.rstrip("/") + "/"), (wd, root)
t0 = time.time()


def git(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, timeout=300)


os.makedirs(os.path.dirname(wd), exist_ok=True)
if os.path.isdir(os.path.join(wd, ".git")):
    r = git("fetch", "--depth", "50", "origin", cwd=wd)
    if r.returncode == 0:
        git("reset", "--hard", "FETCH_HEAD", cwd=wd)
    clone = "refreshed" if r.returncode == 0 else "stale:" + r.stderr.strip()[:200]
else:
    r = git("clone", "--depth", "50", "--quiet", url, wd)
    if r.returncode:
        print(json.dumps({"error": "clone failed", "stderr": r.stderr[-500:]}))
        sys.exit(1)
    clone = "cloned"
clone_s = round(time.time() - t0, 1)

EXT = {".py": "Python", ".pyi": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
       ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".go": "Go", ".rs": "Rust",
       ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".rb": "Ruby", ".php": "PHP", ".c": "C",
       ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".cs": "C#", ".sh": "Shell",
       ".bash": "Shell", ".zsh": "Shell", ".md": "Markdown", ".mdx": "Markdown", ".rst": "reST",
       ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".html": "HTML", ".css": "CSS",
       ".scss": "CSS", ".sql": "SQL", ".lua": "Lua", ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte",
       ".ex": "Elixir", ".exs": "Elixir", ".scala": "Scala", ".zig": "Zig", ".nix": "Nix"}
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$")
files = 0
langs = collections.Counter()
lang_files = collections.Counter()
tests = 0
total_bytes = 0
for root, dirs, fs in os.walk(wd):
    dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "venv", "dist", "build")]
    for f in fs:
        p = os.path.join(root, f)
        rel = os.path.relpath(p, wd)
        files += 1
        if TEST_RE.search(rel):
            tests += 1
        lang = EXT.get(os.path.splitext(f)[1].lower())
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        total_bytes += sz
        if not lang or sz > 2_000_000:
            continue
        try:
            with open(p, "rb") as fh:
                n = fh.read().count(b"\n")
        except OSError:
            continue
        langs[lang] += n
        lang_files[lang] += 1

log = git("log", "-20", "--format=%aI|%an", cwd=wd).stdout.strip().splitlines()
commits = [dict(zip(("date", "author"), ln.split("|", 1))) for ln in log if "|" in ln]
authors = collections.Counter(c["author"] for c in commits)
dates = sorted(c["date"][:10] for c in commits)
depth = len(git("rev-list", "HEAD", cwd=wd).stdout.split())


def first(path_names):
    for n in path_names:
        p = os.path.join(wd, n)
        if os.path.isfile(p):
            return p
    return None


lic = first(["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING"])
license_name = None
if lic:
    head = open(lic, errors="replace").read(3000)
    for key, name in [("MIT License", "MIT"), ("Permission is hereby granted, free of charge", "MIT"),
                      ("Apache License", "Apache-2.0"), ("GNU AFFERO", "AGPL"), ("GNU LESSER", "LGPL"),
                      ("GNU GENERAL PUBLIC", "GPL"), ("Mozilla Public License", "MPL-2.0"),
                      ("BSD 3-Clause", "BSD-3-Clause"), ("Redistribution and use in source", "BSD"),
                      ("ISC License", "ISC"), ("The Unlicense", "Unlicense")]:
        if key in head:
            license_name = name
            break
    license_name = license_name or ("other: " + head.strip().splitlines()[0][:60] if head.strip() else "empty")

versions = {}
pj = os.path.join(wd, "package.json")
if os.path.isfile(pj):
    try:
        d = json.load(open(pj))
        versions["package.json"] = f"{d.get('name')}@{d.get('version')}"
    except Exception as e:  # noqa: BLE001
        versions["package.json"] = f"parse error {e}"
pp = os.path.join(wd, "pyproject.toml")
if os.path.isfile(pp):
    t = open(pp, errors="replace").read()
    name = re.search(r'^name\s*=\s*"([^"]+)"', t, re.M)
    ver = re.search(r'^version\s*=\s*"([^"]+)"', t, re.M)
    dyn = re.search(r'^dynamic\s*=\s*\[([^\]]*)\]', t, re.M)
    versions["pyproject.toml"] = f"{name.group(1) if name else '?'}@" + (
        ver.group(1) if ver else ("dynamic" if dyn and "version" in dyn.group(1) else "?"))
ct = os.path.join(wd, "Cargo.toml")
if os.path.isfile(ct):
    t = open(ct, errors="replace").read()
    ver = re.search(r'^version\s*=\s*"([^"]+)"', t, re.M)
    versions["Cargo.toml"] = ver.group(1) if ver else "?"
tag_rows = git("for-each-ref", "--sort=-creatordate", "--count=5",
               "--format=%(refname:short)|%(creatordate:short)", "refs/tags", cwd=wd).stdout.split()
tags = [t.split("|")[0] for t in tag_rows]
tag_dates = dict(t.split("|", 1) for t in tag_rows if "|" in t)

DOC_RE = re.compile(r"^(readme|architecture|design|overview|contributing|claude|agents)[^/]*\.(md|mdx|rst|txt)$", re.I)
docs = []
for root, dirs, fs in os.walk(wd):
    dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv")]
    rel_root = os.path.relpath(root, wd)
    lvl = 0 if rel_root == "." else rel_root.count("/") + 1
    if lvl > 2:
        dirs[:] = []
        continue
    for f in fs:
        if DOC_RE.match(f) or (rel_root.split("/")[0] == "docs" and lvl == 1 and f.endswith((".md", ".mdx"))):
            p = os.path.normpath(os.path.join(rel_root, f))
            docs.append((lvl, 0 if f.lower().startswith("readme") else 1, p, os.path.getsize(os.path.join(root, f))))
docs.sort()

print(json.dumps({
    "workdir": wd, "clone": clone, "clone_s": clone_s, "head": git("rev-parse", "--short", "HEAD", cwd=wd).stdout.strip(),
    "files": files, "bytes": total_bytes, "test_files": tests,
    "lines_by_lang": dict(langs.most_common(12)), "files_by_lang": dict(lang_files.most_common(12)),
    "commits_in_clone": depth, "recent20": {"first": dates[0] if dates else None, "last": dates[-1] if dates else None,
                                             "authors": dict(authors.most_common(8)), "n": len(commits)},
    "license": license_name, "license_file": os.path.basename(lic) if lic else None,
    "versions": versions, "tags": tags, "tag_dates": tag_dates,
    "docs": [{"path": p, "bytes": b} for _, _, p, b in docs[:25]],
    "agg_s": round(time.time() - t0, 1),
}, ensure_ascii=False))
