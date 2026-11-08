#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一目万行读书要义 · 检索脚本 (lookup)

在本地书库中定位原文，供回答追问时引用。

用法：
    python3 lookup.py --list                          # 列出所有书
    python3 lookup.py <书> --status                    # 章节 + 精读进度
    python3 lookup.py <书> --toc                       # 章节 + 行号区间
    python3 lookup.py <书> 习惯 复利 身份认同            # 关键词检索（OR）
    python3 lookup.py <书> --chapter 5                 # 打印第 5 章正文
    python3 lookup.py <书> --chapter 5 --max 6000      # 只打印前 6000 字
    python3 lookup.py <书> "two-minute rule" -C 4      # 检索并给 4 行上下文

<书> 可以是 slug、slug 前缀、书名，或书库目录路径。
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

BOOKS_ROOT = Path(os.environ.get("YIMU_BOOKS_ROOT", Path.home() / ".workbuddy" / "books"))


def die(msg: str) -> None:
    print(f"[error] {msg}", file=sys.stderr)
    sys.exit(2)


def all_books() -> list[Path]:
    if not BOOKS_ROOT.exists():
        return []
    return sorted(
        p for p in BOOKS_ROOT.iterdir()
        if p.is_dir() and (p / "meta.json").exists()
    )


def resolve(query: str) -> Path:
    q = query.strip().strip("《》\"'")
    cand = Path(q).expanduser()
    if cand.is_dir() and (cand / "meta.json").exists():
        return cand.resolve()
    books = all_books()
    # slug 精确 → slug 前缀 → 书名精确 → 书名包含
    for b in books:
        if b.name == q:
            return b
    for b in books:
        if b.name.startswith(q):
            return b
    metas = [(b, json.loads((b / "meta.json").read_text("utf-8"))) for b in books]
    for b, m in metas:
        if m.get("title") == q:
            return b
    hits = [b for b, m in metas if q and q in m.get("title", "")]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        die("书名不唯一，请用更长的关键词或 slug：\n" + "\n".join(f"  - {h.name}" for h in hits))
    die(f"书库里没有匹配「{query}」。用 --list 看现有书目。")


def load(bookdir: Path):
    meta = json.loads((bookdir / "meta.json").read_text("utf-8"))
    lines = (bookdir / "fulltext.md").read_text("utf-8").split("\n")
    return meta, lines


def label(ch: dict) -> str:
    return ch.get("label") or ch["title"]


def chapter_of(meta: dict, lineno: int) -> dict | None:
    for ch in meta["chapters"]:
        if ch["start"] <= lineno <= ch["end"]:
            return ch
    return None


def cmd_list() -> None:
    books = all_books()
    if not books:
        print("书库为空。先用 ingest.py 建档。")
        return
    print(f"书库根目录：{BOOKS_ROOT}\n")
    for b in books:
        m = json.loads((b / "meta.json").read_text("utf-8"))
        digest = b / "digest.md"
        done = 0
        if digest.exists():
            done = digest.read_text("utf-8").count("- 状态：已读")
        print(f"- {m['title']}（{m.get('author') or '未知作者'}）"
              f"  {m['chapter_count']}章 / {m['chars']:,}字"
              f"  精读 {done}/{m['chapter_count']}")
        print(f"    slug: {b.name}")


def cmd_toc(meta: dict, with_status: bool, bookdir: Path | None = None) -> None:
    digest_txt = ""
    bookdir = bookdir or (BOOKS_ROOT / meta["slug"])
    if with_status:
        dp = bookdir / "digest.md"
        digest_txt = dp.read_text("utf-8") if dp.exists() else ""
    print(f"《{meta['title']}》 {meta.get('author') or '未知'} ｜ "
          f"{meta['chars']:,} 字 ｜ {meta['chapter_count']} 章 ｜ "
          f"共 {meta['total_lines']} 行 ｜ 切分 {meta.get('split_method')}")
    print(f"目录：{bookdir}\n")
    print("| # | 章节 | 字数 | 行号 | 状态 |")
    print("|---|---|---|---|---|")
    for ch in meta["chapters"]:
        status = ""
        if with_status and digest_txt:
            seg = re.search(
                rf"^##\s*{re.escape(label(ch))}\s*$([\s\S]*?)(?=^##\s|\Z)",
                digest_txt, re.M)
            if seg:
                m = re.search(r"- 状态：(\S+)", seg.group(0))
                status = m.group(1) if m else "未读"
        print(f"| {ch['idx']} | {label(ch)} | {ch['chars']:,} | "
              f"{ch['start']}–{ch['end']} | {status or '—'} |")


def cmd_chapter(meta: dict, lines: list[str], idx: int, max_chars: int) -> None:
    ch = next((c for c in meta["chapters"] if c["idx"] == idx), None)
    if not ch:
        die(f"没有第 {idx} 章（本书共 {meta['chapter_count']} 章）")
    body_start = ch.get("body_start", ch["start"] + 4)
    seq = lines[body_start - 1: ch["end"]]
    # 去掉正文开头重复的标题行 / 空行
    while seq and (not seq[0].strip()
                   or seq[0].strip() == ch["title"].strip()
                   or seq[0].strip() == ch.get("label", "").strip()):
        seq.pop(0)
    body = "\n".join(seq)
    truncated = False
    if max_chars and len(re.sub(r"\s", "", body)) > max_chars:
        cut, acc = 0, 0
        for cut, ln in enumerate(body.split("\n")):
            acc += len(ln)
            if acc > max_chars:
                break
        body = "\n".join(body.split("\n")[:cut]) + "\n…（本章已截断，用 --max 0 取全文）"
        truncated = True
    print(f"=== {label(ch)} ｜ 行 {ch['start']}–{ch['end']} "
          f"｜ {ch['chars']:,} 字{'（已截断）' if truncated else ''} ===\n")
    print(body)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def cmd_search(meta: dict, lines: list[str], terms: list[str],
               ctx: int, limit: int, regex_mode: bool) -> None:
    pats = []
    for t in terms:
        t = t.strip().strip('"')
        if not t:
            continue
        if regex_mode:
            pats.append((t, re.compile(t, re.I)))
        else:
            pats.append((t, re.compile(re.escape(t).replace(r"\ ", r"\s*"), re.I)))

    if not pats:
        die("没有可检索的关键词")

    hits = []  # (lineno, term, line)
    for i, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith(("<!--", "<a id=")):
            continue
        low = line
        for term, rx in pats:
            if rx.search(low):
                hits.append((i, term, line))
                break

    if not hits:
        print(f"《{meta['title']}》未命中：{', '.join(t for t, _ in pats)}")
        print("提示：换同义词、去掉修饰词、或把整句缩成 2–4 字的核心词再试。")
        return

    # 相邻行合并成命中簇，避免同一段重复刷屏
    clusters, cur = [], []
    for h in hits:
        if cur and h[0] - cur[-1][0] <= ctx * 2:
            cur.append(h)
        else:
            if cur:
                clusters.append(cur)
            cur = [h]
    if cur:
        clusters.append(cur)

    print(f"《{meta['title']}》命中 {len(hits)} 处 → 归并为 {len(clusters)} 簇"
          f"（最多显示 {limit} 簇）\n")
    shown = 0
    for cl in clusters:
        if shown >= limit:
            print(f"…还有 {len(clusters) - shown} 簇未显示，加 --limit 或换更窄的词。")
            break
        a, b = cl[0][0], cl[-1][0]
        ch = chapter_of(meta, a)
        where = label(ch) if ch else "卷首"
        print(f"── {where} · 行 {a}" + (f"–{b}" if b != a else "") +
              f" ── [{', '.join(sorted({t for _, t, _ in cl}))}]")
        lo = max(1, a - ctx)
        hi = min(len(lines), b + ctx)
        rows = [(i, "»" if any(i == x[0] for x in cl) else " ", lines[i - 1])
                for i in range(lo, hi + 1)]
        rows = [r for r in rows if r[2].strip() and not r[2].lstrip().startswith(("<!--", "<a id="))]
        cap = 24
        if len(rows) > cap:
            omitted = len(rows) - cap
            rows = rows[:8] + [(None, " ", f"…（此处省略 {omitted} 行，用 --chapter 读全章）")] + rows[-8:]
        for i, mark, text in rows:
            if i is None:
                print(f" {text}")
            else:
                print(f"{mark}{i:>6} | {text}")
        print()
        shown += 1


def main() -> int:
    ap = argparse.ArgumentParser(description="一目万行读书要义 · 检索")
    ap.add_argument("book", nargs="?", default="", help="slug / 书名 / 目录路径")
    ap.add_argument("terms", nargs="*", help="检索关键词（多个为 OR）")
    ap.add_argument("--list", action="store_true", help="列出书库")
    ap.add_argument("--toc", action="store_true", help="章节表 + 行号区间")
    ap.add_argument("--status", action="store_true", help="章节表 + 精读进度")
    ap.add_argument("--chapter", type=int, default=0, help="打印第 N 章正文")
    ap.add_argument("--max", type=int, default=8000, help="--chapter 的字数上限，0 为不限")
    ap.add_argument("-C", "--context", type=int, default=2, help="检索命中上下文行数")
    ap.add_argument("--limit", type=int, default=12, help="最多显示多少簇命中")
    ap.add_argument("--regex", action="store_true", help="关键词按正则解释")
    args = ap.parse_args()

    if args.list or not args.book:
        cmd_list()
        return 0

    bookdir = resolve(args.book)
    meta, lines = load(bookdir)

    if args.toc or args.status:
        cmd_toc(meta, with_status=args.status, bookdir=bookdir)
        return 0
    if args.chapter:
        cmd_chapter(meta, lines, args.chapter, args.max)
        return 0
    if args.terms:
        cmd_search(meta, lines, args.terms, args.context, args.limit, args.regex)
        return 0

    cmd_toc(meta, with_status=True, bookdir=bookdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
