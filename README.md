# 一目万行读书要义 · yimu-wanhang-book

一个 Agent Skill：**通读整本书 → 落成本地可检索书库 → 先给全书精华 → 之后任何追问都能回到原文回答。**

没时间精读，但又需要真的「读过」一本书：知道它讲什么，被追问细节时答得出、指得回原句。

---

## 为什么不是"总结一下这本书"

三件事是硬要求，缺一不可：

1. **通读** —— 不是看目录猜内容，是把全文逐章过一遍。
2. **记住** —— 记忆落在磁盘上，换会话、换项目都还在。
3. **可追问** —— 回答能定位到 `第N章 · 行L`；书里没有的，明确说没有。

一本 30 万字的书塞不进上下文窗口。硬塞就是截断，然后编。所以这个 Skill 的做法是：
**全文存底稿 → 按章节分批读 → 笔记写进文件 → 追问时按行号回查原文。**

和 `book-essence` 之类"评分 + 简介摘要"的技能不同：那些不落全文，答不了细节；这个能回到逐字原文。

---

## 两条路线

| | 路线 A · 全文（推荐） | 路线 B · 仅书名 |
|---|---|---|
| 输入 | EPUB / TXT / MD / DOCX / HTML / PDF 文件 | 只报一个书名 |
| 依据 | 抽取到的全书正文 | 联网检索到的公开资料 |
| 能否记住全书 | 能，落本地书库 | 不能 |
| 能否给逐字原文 / 行号 | 能 | **不能，也不给** |
| 输出标注 | `全文路线` | `本次无全文，细节不可核实` |

路线 B 会明确告诉你「这是二手资料综合」，并主动建议补文件。它不编引文。

---

## 安装

```bash
git clone https://github.com/richa-yuan/yimu-wanhang-book.git \
  ~/.workbuddy/skills/yimu-wanhang-book
```

WorkBuddy / Claude Code 等按 `SKILL.md` 加载的技能环境都能用。仓库根目录就是技能目录，
`SKILL.md` 在顶层，clone 完即可触发。

依赖：**Python 3.10+，纯标准库**，无需 pip install。

PDF 抽取需要 `workbuddy-pdf` CLI（WorkBuddy 内置）。其他环境任选其一：

- 先把 PDF 转成 `.txt`，再对 txt 建档（最省事）；
- 或让 `workbuddy-pdf` 出现在 PATH 上；
- 或设置 `WORKBUDDY_PDF_SKILL` 指向含 `scripts/run.sh` 的技能目录。

`mobi` / `azw3` 没有内置解析器，请先转成 EPUB 或 TXT。

---

## 快速开始

```bash
SK=~/.workbuddy/skills/yimu-wanhang-book/scripts

# 1. 建档（会打印书名、字数、章节数）
python3 $SK/ingest.py ~/Downloads/某本书.epub

# 2. 看章节行号表，然后按批逐章通读
python3 $SK/lookup.py 某本书 --toc

# 3. 追问时定位原文
python3 $SK/lookup.py 某本书 身份认同 复利 -C 3
python3 $SK/lookup.py 某本书 --chapter 7 --max 0

# 随时看进度 / 列书库
python3 $SK/lookup.py 某本书 --status
python3 $SK/lookup.py --list
```

在 Agent 里更简单的用法：**把电子书拖进对话，说「帮我读一下」**。Skill 会自动建档、通读、出要义，
之后直接问「作者在第七章怎么论证的」即可。

---

## 书库结构

```
~/.workbuddy/books/            # 可用 YIMU_BOOKS_ROOT 覆盖
├── INDEX.md                    全局书目登记
└── <书名>-<哈希>/
    ├── fulltext.md             全文底稿（只读，行号即引用坐标）
    ├── meta.json               元数据 + 章节行号索引
    ├── outline.md              章节结构表
    ├── digest.md               逐章精读笔记（边读边填，五段式）
    ├── quotes.md               金句库
    ├── glossary.md             人物·概念索引
    └── essence.md              最终要义成稿
```

`fulltext.md` 是唯一真相源，生成后不要编辑、不要重排 —— 所有引用都靠它的行号。

引用格式统一为：

```
> 「原文原句」 —— 《书名》第N章 · 行 L
```

行号只对本地这份电子版有效，跨版本不能混用。

---

## 仓库内容

```
├── SKILL.md                        技能主文件：五步工作流 + 三条铁律
├── README.md                       本文件
├── LICENSE                         MIT
├── scripts/
│   ├── ingest.py                   抽取 + 章节切分 + 建档
│   └── lookup.py                   检索 / 章节 / 状态 / 列书目
└── references/
    ├── essence-template.md         要义成稿模板（含反面示例）
    └── qa-rules.md                 追问应答纪律
```

`ingest.py` 做的事：编码嗅探（UTF-8 / GB18030 / Big5 / UTF-16）、EPUB spine 顺序解析、
EPUB/DOCX 内嵌书名作者、PDF 硬换行折回段落、中英文章节标题识别、识别不到时按字数切块、
生成章节行号索引。

`lookup.py` 做的事：多关键词 OR 检索（带行号与上下文）、命中簇归并、整章输出、精读进度、
书名/slug/目录路径三种寻址。

---

## 隐私与版权

**这个仓库只包含代码和文档，不包含任何书籍正文。** 你的全文底稿留在本地
`~/.workbuddy/books/`，不会随 Skill 上传，也不该被提交到公开仓库 —— `.gitignore` 已屏蔽
`books/` 和常见电子书格式。

请使用你有权使用的电子书文件。受版权保护的全书文本不要提交到任何公开仓库，也不要贴进公开
Issue / PR。Skill 生成的引用是短句摘录，用于个人阅读与讨论。

---

## 已知限制

- 扫描版 PDF 无 OCR 时会抽取失败或字数极少，脚本会明确报错，不假装读到了。
- 诗歌、代码、表格在折行合并时可能被拼坏，引用前请目测是否完整句子。
- 章节标题极不规范的书会退化成「区块 N」，引用坐标相应变成 `区块 N · 行 L`。
- 行号只在本地这一份电子版内有效；换译本、换版本需重新建档。
- 超长书允许跨多轮读完，靠 `digest.md` + `--status` 续接；未读章节会在要义里如实标注。

---

## License

MIT © 2026 richa-yuan
