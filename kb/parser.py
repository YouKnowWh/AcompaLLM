"""
kb/parser.py — 多格式文档解析器
================================
支持格式：
  .txt / .md   — 直接读取（自动检测编码）
  .pdf         — pypdf 提取文本
  .docx        — python-docx 提取段落
  url (http/https) — requests + BeautifulSoup 提取正文

公开接口：
  parse_file(path: str | Path) -> str
  parse_url(url: str) -> str
  default_name(path: str | Path) -> str   # 返回文件名去后缀作为默认集合名
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union


def default_name(path: Union[str, Path]) -> str:
    """从文件路径提取默认知识库名（文件名去后缀）。"""
    return Path(path).stem


def parse_file(path: Union[str, Path]) -> str:
    """
    解析本地文件，返回纯文本字符串。

    支持：.txt .md .pdf .docx
    对不支持的格式抛出 ValueError。
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in (".txt", ".md"):
        return _read_text(p)
    elif suffix == ".pdf":
        return _read_pdf(p)
    elif suffix == ".docx":
        return _read_docx(p)
    else:
        raise ValueError(f"不支持的文件格式：{suffix}（支持 .txt .md .pdf .docx）")


def parse_url(url: str) -> str:
    """
    抓取网页并提取正文文本。

    会优先提取 <article>/<main> 标签，退而其次取 <body>。
    """
    import requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (compatible; AIMemoryBot/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"网页抓取失败：{e}") from e

    soup = BeautifulSoup(resp.text, "html.parser")

    # 移除脚本、样式、导航等噪声标签
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()

    # 优先取语义化正文标签
    body = soup.find("article") or soup.find("main") or soup.find("body")
    if body is None:
        return soup.get_text(separator="\n", strip=True)

    # 提取纯文本并压缩多余空行
    raw = body.get_text(separator="\n", strip=True)
    return _compress_blank_lines(raw)


# ──────────────────────────────────────────────────────────────
# 私有辅助函数
# ──────────────────────────────────────────────────────────────

def _read_text(path: Path) -> str:
    """自动检测编码读取纯文本文件。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb2312", "big5"):
        try:
            text = path.read_text(encoding=enc)
            return _compress_blank_lines(text)
        except (UnicodeDecodeError, LookupError):
            continue
    # 最后兜底
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    """用 pypdf 逐页提取文本。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text)
    return _compress_blank_lines("\n\n".join(pages))


def _read_docx(path: Path) -> str:
    """用 python-docx 提取段落文本。"""
    from docx import Document

    doc = Document(str(path))
    paras = [p.text for p in doc.paragraphs if p.text.strip()]
    return _compress_blank_lines("\n\n".join(paras))


def _compress_blank_lines(text: str) -> str:
    """将连续空行压缩为单个空行。"""
    return re.sub(r'\n{3,}', '\n\n', text).strip()
