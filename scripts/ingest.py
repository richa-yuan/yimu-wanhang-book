#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一目万行读书要义 · 建档脚本 (ingest)

把一本电子书拆成可检索的本地书库：
    ~/.workbuddy/books/<slug>/
        fulltext.md   全文，带章节锚点与行号（只读，生成后不要再改）
        meta.json     元数据 + 章节行号索引
        outline.md    章节结构表
        digest.md     逐章精读笔记（占位骨架，由模型边读边填）
        quotes.md     金句库
        glossary.md   人名/概念索引

用法：
    python3 ingest.py /path/to/book.epub
    python3 ingest.py /path/to/book.pdf --author "詹姆斯·克利尔"
    python3 ingest.py /path/to/book.txt --title "原子习惯" --blocksize 9000

依赖：仅 Python 标准库。PDF 走 workbuddy-pdf CLI（若本机可用）。
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

BOOKS_ROOT = Path(os.environ.get("YIMU_BOOKS_ROOT", Path.home() / ".workbuddy" / "books"))

# 抽取过程中顺带抓到的内嵌元信息（dc:title / dc:creator 等）
DOC_META: dict[str, str] = {}

# ---------------------------------------------------------------- 编码与清洗

ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "big5", "utf-16", "latin-1"]


def read_unknown_encoding(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        # 粗判：乱码率过高则换下一个编码
        if enc != "latin-1" and text.count("\ufffd") > len(text) * 0.01:
            continue
        return text
    return raw.decode("utf-8", errors="replace")


TAG_RE = re.compile(r"<[^>]+>")
BLANK_RE = re.compile(r"[ \t\u3000]+")


def strip_html(fragment: str) -> str:
    fragment = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</(p|div|h[1-6]|li|tr|section|article)>", "\n\n", fragment)
    fragment = TAG_RE.sub("", fragment)
    fragment = html.unescape(fragment)
    lines = [BLANK_RE.sub(" ", ln).strip() for ln in fragment.split("\n")]
    return "\n".join(lines)


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = unicodedata.normalize("NFC", text)
    out, blank = [], 0
    for line in text.split("\n"):
        line = BLANK_RE.sub(" ", line).rstrip()
        if not line:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(line)
    return "\n".join(out).strip() + "\n"


SENT_END = set("。！？…；：」』”’）)】》〉!.?:;\"')]›·")


def is_cjk(ch: str) -> bool:
    return ("\u3400" <= ch <= "\u9fff") or ("\uff00" <= ch <= "\uffef") \
        or ("\u3000" <= ch <= "\u303f")


BREAK_START_RE = re.compile(r"^(?:[•·◦▪▸►\-–—*]\s|\d{1,3}[.、)]\s|\|)")


def reflow(text: str) -> str:
    """把 PDF/纯文本常见的硬换行重新合成段落。

    只在「上一行没说完 + 这一行不是标题/列表」时拼接，空行一律视为段落边界。
    """
    lines = text.split("\n")
    out: list[str] = []
    for raw in lines:
        cur = raw.strip()
        if not cur:
            out.append("")
            continue
        if not out or not out[-1]:
            out.append(cur)
            continue
        prev = out[-1]
        ends_ok = prev[-1] in SENT_END
        is_head = any(rx.match(cur) for rx in HEAD_RES) or MD_HEAD_RE.match(cur)
        if ends_ok or is_head or BREAK_START_RE.match(cur) or len(prev) < 12:
            out.append(cur)
            continue
        # 英文断词：上一行以连字符结尾且下一行小写开头
        if prev.endswith("-") and cur[:1].islower() and prev[-2:-1].isalpha():
            out[-1] = prev[:-1] + cur
            continue
        sep = "" if (is_cjk(prev[-1]) and is_cjk(cur[0])) else " "
        out[-1] = prev + sep + cur
    return "\n".join(out)


# ---------------------------------------------------------------- 各格式抽取

def extract_epub(path: Path) -> str:
    parts = []
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        opf = None
        if "META-INF/container.xml" in names:
            try:
                root = ET.fromstring(zf.read("META-INF/container.xml"))
                for el in root.iter():
                    if el.tag.endswith("rootfile"):
                        opf = el.get("full-path")
                        break
            except ET.ParseError:
                opf = None
        order = []
        base = ""
        DOC_META.clear()
        if opf and opf in names:
            base = str(Path(opf).parent)
            base = "" if base == "." else base + "/"
            try:
                oroot = ET.fromstring(zf.read(opf))
                for el in oroot.iter():
                    tag = el.tag.split("}")[-1].lower()
                    if tag in ("title", "creator") and el.text and el.text.strip():
                        DOC_META.setdefault(
                            "title" if tag == "title" else "author", el.text.strip())
                manifest = {}
                for el in oroot.iter():
                    if el.tag.endswith("item") and el.get("id"):
                        manifest[el.get("id")] = el.get("href")
                for el in oroot.iter():
                    if el.tag.endswith("itemref"):
                        href = manifest.get(el.get("idref"))
                        if href:
                            order.append(base + href.split("#")[0])
            except ET.ParseError:
                order = []
        if not order:
            order = sorted(
                n for n in names
                if n.lower().endswith((".xhtml", ".html", ".htm"))
            )
        seen = set()
        for name in order:
            if name in seen or name not in names:
                continue
            seen.add(name)
            try:
                parts.append(strip_html(zf.read(name).decode("utf-8", errors="replace")))
            except Exception:
                continue
        # 补漏：spine 之外散落的 xhtml
        for name in names:
            if name.lower().endswith((".xhtml", ".html", ".htm")) and name not in seen:
                try:
                    parts.append(strip_html(zf.read(name).decode("utf-8", errors="replace")))
                except Exception:
                    continue
    return "\n\n".join(p for p in parts if p.strip())


def extract_docx(path: Path) -> str:
    DOC_META.clear()
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        if "docProps/core.xml" in zf.namelist():
            try:
                croot = ET.fromstring(zf.read("docProps/core.xml"))
                for el in croot.iter():
                    tag = el.tag.split("}")[-1].lower()
                    if tag == "title" and el.text and el.text.strip():
                        DOC_META.setdefault("title", el.text.strip())
                    if tag == "creator" and el.text and el.text.strip():
                        DOC_META.setdefault("author", el.text.strip())
            except ET.ParseError:
                pass
    xml = re.sub(r"(?i)<w:br[^>]*/>", "\n", xml)
    xml = re.sub(r"(?i)</w:p>", "\n\n", xml)
    xml = re.sub(r"(?i)</w:tr>", "\n", xml)
    xml = TAG_RE.sub("", xml)
    return html.unescape(xml)


def find_pdf_cli() -> list[str] | None:
    """定位 PDF 抽取命令，返回可直接拼参数的前缀。

    优先级：WORKBUDDY_PDF_SKILL 环境变量 → PATH 上的 workbuddy-pdf
    → macOS 上 WorkBuddy 内置技能的 run.sh。
    """
    env = os.environ.get("WORKBUDDY_PDF_SKILL")
    if env:
        p = Path(env) / "scripts" / "run.sh"
        if p.exists():
            return ["sh", str(p)]
    on_path = shutil.which("workbuddy-pdf")
    if on_path:
        return [on_path]
    app = Path("/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/"
               "resources/plugins/workbuddy-builtin/skills/workbuddy-pdf")
    p = app / "scripts" / "run.sh"
    if p.exists():
        return ["sh", str(p)]
    return None


def extract_pdf(path: Path) -> tuple[str, str]:
    cli = find_pdf_cli()
    if not cli:
        raise RuntimeError(
            "找不到 workbuddy-pdf CLI。请把 PDF 转成 txt 后再建档："
            "`ingest.py <book.txt> --title \"...\"`；"
            "或设置 WORKBUDDY_PDF_SKILL 指向 workbuddy-pdf 技能目录。"
        )
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "book.txt"
        proc = subprocess.run(
            cli + [str(path), str(out), "-f", "txt", "-q"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(
                f"workbuddy-pdf 抽取失败 (exit={proc.returncode})："
                f"{(proc.stderr or '')[-400:]}"
            )
        return out.read_text("utf-8", errors="replace"), "workbuddy-pdf/-f txt"


def extract_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".txt" or suffix in (".md", ".markdown"):
        return read_unknown_encoding(path), "plain"
    if suffix == ".epub":
        return extract_epub(path), "epub(spine)"
    if suffix == ".docx":
        return extract_docx(path), "docx"
    if suffix in (".html", ".htm", ".xhtml"):
        return strip_html(read_unknown_encoding(path)), "html"
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix in (".mobi", ".azw3", ".azw"):
        raise RuntimeError(
            f"{suffix} 是 Kindle 私有格式，本机无解析器。"
            "请先转成 epub/txt（Calibre 的 ebook-convert，或用 Kindle 导出），再重新建档。"
        )
    raise RuntimeError(f"不支持的格式：{suffix}")


# ---------------------------------------------------------------- 章节切分

CN_NUM = "〇零一二三四五六七八九十百千两0-9"
HEAD_PATTERNS = [
    rf"^(第\s*[{CN_NUM}]{{1,6}}\s*[章回节篇卷部](?:\s*[^\n]{{0,40}})?)$",
    r"^(Chapter\s+(?:[0-9]{1,3}|[IVXLCDMivxlcdm]{1,8})\b[^\n]{0,60})$",
    r"^(CHAPTER\s+(?:[0-9]{1,3}|[IVXLCDMivxlcdm]{1,8})\b[^\n]{0,60})$",
    r"^((?:Part|PART|Book|BOOK|Volume|VOLUME)\s+(?:[0-9]{1,3}|[IVXLCDM]{1,8})\b[^\n]{0,60})$",
    r"^((?:序言|序|前言|引言|导言|楔子|自序|推荐序|译者序|后记|尾声|结语|结束语|附录|致谢|参考文献)(?:[^\n]{0,20}))$",
]
HEAD_RES = [re.compile(p) for p in HEAD_PATTERNS]
MD_HEAD_RE = re.compile(r"^(#{1,2})\s+([^\n]{1,50})$")


def detect_chapters(lines: list[str], blocksize: int) -> tuple[list[dict], str]:
    """返回 (章节列表, 切分方式)。章节为 {title, start}（0-based 行号）。"""
    hits = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or len(line) > 66:
            continue
        m = MD_HEAD_RE.match(line)
        if m:
            hits.append((i, m.group(2).strip(), 1 if len(m.group(1)) == 1 else 2))
            continue
        for res_index, rx in enumerate(HEAD_RES):
            if rx.match(line):
                # 宽松但可控：标题必须在版面上自成一行。
                # 抽取后的正文一段一行，真正的章节标题行几乎不会超过 45 字；
                # 前一行允许是空行、页码、文件名等短行（电子书常见）。
                prev = lines[i - 1].strip() if i > 0 else ""
                ok_prev = (not prev) or len(prev) <= 15
                ok_len = len(line) <= 45 or res_index >= 4
                if ok_prev and ok_len:
                    hits.append((i, line, 2 if res_index >= 4 else 1))
                break

    # 去掉过密命中的误报（同一标题模式连续命中）
    cleaned, last = [], -99
    for idx, title, prio in hits:
        if idx - last < 2:
            continue
        cleaned.append({"title": title, "start": idx, "prio": prio})
        last = idx

    strong = [c for c in cleaned if c["prio"] <= 2]
    picked = strong if len(strong) >= 3 else cleaned

    if len(picked) >= 3:
        for c in picked:
            c.pop("prio", None)
        method = "heading"
        # 第一章之前若有实质内容（前言等），补一个「卷首」
        if picked[0]["start"] > 5:
            head_text = "".join(lines[: picked[0]["start"]]).strip()
            if len(head_text) > 400:
                picked.insert(0, {"title": "卷首", "start": 0})
        return picked, method

    # 回退：按字符量切块
    chapters, buf, start, acc = [], [], 0, 0
    for i, line in enumerate(lines):
        buf.append(line)
        acc += len(line)
        if acc >= blocksize:
            chapters.append({"title": f"区块 {len(chapters) + 1}（第 {start + 1}–{i + 1} 行）", "start": start})
            buf, acc, start = [], 0, i + 1
    if buf and "".join(buf).strip():
        chapters.append({"title": f"区块 {len(chapters) + 1}（第 {start + 1}–{len(lines)} 行）", "start": start})
    return chapters, "blocks"


# ---------------------------------------------------------------- 元信息

TITLE_NOISE = [
    r"\.(epub|pdf|txt|docx|mobi|azw3|md)$", r"[（(][^)）]{0,12}[)）]$",
    r"[-_ ]?(完整版|精校版|校对版|文字版|全本|全集|高清|扫描版|简体|繁体)$",
]


def guess_title(path: Path) -> str:
    name = path.stem
    for _ in range(3):
        for pat in TITLE_NOISE:
            name = re.sub(pat, "", name, flags=re.I)
    return name.strip(" -_·.") or path.stem


def guess_author(text: str, filename: str) -> str:
    m = re.search(r"作者\s*[:：]\s*([^\n（(]{2,20})", text[:4000])
    if m:
        return m.group(1).strip()
    m = re.search(r"[-_—]\s*([^-_—]{2,12})\s*[（(]?著", filename)
    if m:
        return m.group(1).strip()
    return ""


CHAPTER_NUM_RE = re.compile(
    r"^\s*(?:第\s*[〇零一二三四五六七八九十百千两0-9]{1,6}\s*[章回节篇卷部]"
    r"|(?:Chapter|CHAPTER|Part|PART|Book|BOOK)\b)",
)


def chapter_label(ch: dict) -> str:
    """章节展示名：标题自带编号就不再叠一层「第N章」。"""
    title = ch["title"].strip()
    if CHAPTER_NUM_RE.match(title):
        return title
    return f"第{ch['idx']}章 {title}"


def slugify(title: str, salt: str) -> str:
    base = re.sub(r"[\\/:*?\"<>|\s]+", "-", title).strip("-.")
    base = base[:40] or "book"
    return f"{base}-{salt[:6]}"


# ---------------------------------------------------------------- 写库

def build(src: Path, title: str, author: str, blocksize: int, force: bool) -> Path:
    raw, method = extract_text(src)
    text = reflow(normalize(raw))
    lines = text.split("\n")
    chars = len(re.sub(r"\s", "", text))

    chapters, split_method = detect_chapters(lines, blocksize)
    if len(chapters) < 2:
        return None  # 交给调用方报错

    salt = hashlib.sha1(
        (title + str(chars) + src.name).encode("utf-8")
    ).hexdigest()[:8]
    slug = slugify(title, salt)
    bookdir = BOOKS_ROOT / slug
    if bookdir.exists():
        if not force:
            print(f"[skip] 书库已存在：{bookdir}")
            print(f"       加 --force 覆盖重建。")
            return bookdir
        shutil.rmtree(bookdir)
    bookdir.mkdir(parents=True)

    # 先把原文切片缓存下来（start 是原文行号，0-based）
    raw_slices = []
    for n, ch in enumerate(chapters):
        ch["idx"] = n + 1
        src_end = (chapters[n + 1]["start"] - 1) if n + 1 < len(chapters) else len(lines) - 1
        body = lines[ch["start"]: src_end + 1]
        raw_slices.append(body)
        ch["chars"] = len(re.sub(r"\s", "", "\n".join(body)))

    # fulltext.md：单趟写出，同时记录 out 里的真实行号
    out = [
        f"<!-- yimu-book v1 | slug={slug} -->",
        f"# {title}",
        f"> 作者：{author or '未知'} ｜ 正文 {chars:,} 字 ｜ {len(chapters)} 章 ｜ "
        f"源格式 {src.suffix.lstrip('.')} ｜ 抽取 {method}",
        "",
        "<!-- 本文件为只读底稿：行号即引用坐标，请勿编辑、勿重排。 -->",
        "",
    ]
    for ch, body in zip(chapters, raw_slices):
        ch["start"] = len(out) + 1          # anchor 所在行（1-based）
        ch["body_start"] = ch["start"] + 4  # 正文首行（跳过 anchor/标题/注释/空行）
        out.append(f'<a id="c{ch["idx"]:02d}"></a>')
        out.append(f"## {chapter_label(ch)}")
        out.append(f"<!-- chapter={ch['idx']} chars={ch['chars']} -->")
        out.append("")
        out.extend(body)
        out.append("")
    for n, ch in enumerate(chapters):
        ch["end"] = (chapters[n + 1]["start"] - 4) if n + 1 < len(chapters) else len(out)

    fulltext = bookdir / "fulltext.md"
    fulltext.write_text("\n".join(out), "utf-8")
    total_lines = len(out)

    meta = {
        "skill": "yimu-wanhang-book",
        "slug": slug,
        "title": title,
        "author": author,
        "source_path": str(src),
        "source_format": src.suffix.lstrip("."),
        "extract_method": method,
        "split_method": split_method,
        "chars": chars,
        "chapter_count": len(chapters),
        "total_lines": total_lines,
        "ingested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fidelity": "full-text" if method != "plain" or src.suffix.lower() in (".txt", ".md") else "degraded",
        "chapters": [
            {"idx": c["idx"], "title": c["title"], "label": chapter_label(c),
             "start": c["start"], "body_start": c["body_start"],
             "end": c["end"], "chars": c["chars"]}
            for c in chapters
        ],
    }
    (bookdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")

    # outline.md
    o = [f"# 《{title}》结构", "", f"共 {len(chapters)} 章 / {chars:,} 字", "",
         "| # | 章节 | 字数 | 行号区间 |", "|---|---|---|---|"]
    for c in chapters:
        o.append(f"| {c['idx']} | {chapter_label(c)} | {c['chars']:,} | "
                 f"{c['start']}–{c['end']} |")
    (bookdir / "outline.md").write_text("\n".join(o) + "\n", "utf-8")

    # digest.md 骨架
    d = [f"# 《{title}》逐章精读笔记", "",
         "> 边读边填。每章五段：核心论点 / 骨架推进 / 关键论据 / 金句 / 我的评注。",
         "> 状态填写：`已读` / `部分` / `未读`。", ""]
    for c in chapters:
        d += [f"## {chapter_label(c)}",
              "- 状态：未读",
              "- 核心论点：",
              "- 骨架推进：",
              "- 关键论据：",
              "- 金句：",
              "- 评注：", ""]
    (bookdir / "digest.md").write_text("\n".join(d), "utf-8")

    (bookdir / "quotes.md").write_text(
        f"# 《{title}》金句库\n\n> 格式：`> 原句` — 第N章 · 行L（用途：…）\n",
        "utf-8")
    (bookdir / "glossary.md").write_text(
        f"# 《{title}》人物·概念索引\n\n"
        "> 格式：`词条 — 含义（第N章 · 行L）`\n",
        "utf-8")

    # 全局登记
    BOOKS_ROOT.mkdir(parents=True, exist_ok=True)
    index = BOOKS_ROOT / "INDEX.md"
    if not index.exists():
        index.write_text(
            "# 书目登记\n\n| slug | 书名 | 作者 | 章数 | 字数 | 源格式 | 建档时间 |\n"
            "|---|---|---|---|---|---|---|\n", "utf-8")
    row = (f"| {slug} | {title} | {author or '—'} | {len(chapters)} | "
           f"{chars:,} | {src.suffix.lstrip('.')} | {time.strftime('%Y-%m-%d %H:%M')} |\n")
    with index.open("a", encoding="utf-8") as f:
        f.write(row)

    print(f"[ok] 《{title}》 → {bookdir}")
    print(f"     字数 {chars:,} ｜ {len(chapters)} 章 ｜ 切分方式 {split_method}")
    print(f"     下一篇该做的：读 outline.md，然后按章填 digest.md")
    return bookdir


def main() -> int:
    ap = argparse.ArgumentParser(description="一目万行读书要义 · 建档")
    ap.add_argument("source", help="电子书文件路径")
    ap.add_argument("--title", default="", help="书名（默认从文件名推断）")
    ap.add_argument("--author", default="", help="作者")
    ap.add_argument("--blocksize", type=int, default=9000,
                    help="识别不到章节时按多少字切块（默认 9000）")
    ap.add_argument("--force", action="store_true", help="已存在则覆盖重建")
    args = ap.parse_args()

    src = Path(args.source).expanduser().resolve()
    if not src.exists():
        print(f"[error] 文件不存在：{src}", file=sys.stderr)
        return 2
    if not src.is_file():
        print(f"[error] 不是文件：{src}", file=sys.stderr)
        return 2

    try:
        peek_raw, _ = extract_text(src) if src.suffix.lower() in (".pdf", ".epub", ".docx", ".html", ".htm", ".xhtml") else (read_unknown_encoding(src), "")
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3

    title = args.title.strip() or DOC_META.get("title") or guess_title(src)
    author = (args.author.strip() or DOC_META.get("author")
              or guess_author(peek_raw, src.name))
    try:
        result = build(src, title, author, args.blocksize, args.force)
    except RuntimeError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 3
    if result is None:
        print("[error] 全文过短或抽取为空，无法建档（可能整本都是扫描图/加密）。", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
