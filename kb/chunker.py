"""
kb/chunker.py — 智能文本分块器
================================
策略：
  - 统一块大小：500 汉字（对应约 300+ token，覆盖 bge-small 感受野）
  - 重叠：60 字（保证跨块语义连续）
  - 优先在段落边界（\\n\\n）、句子边界（。！？；）处分割
  - 返回块列表，每块附带 chunk_index 用于元数据
"""

from __future__ import annotations

import re
from typing import List

# 可配置参数
CHUNK_SIZE: int = 4000  # 目标块大小（字符数）
OVERLAP: int = 200      # 相邻块重叠字符数
MIN_CHUNK: int = 100    # 低于此长度的块丢弃（避免碎片）

# 句子级分割标点（中英文均覆盖）
_SENT_END = re.compile(r'(?<=[。！？；…\.\!\?;])\s*')


def _split_by_paragraphs(text: str) -> List[str]:
    """按段落（连续空行）初步切分，保留段落顺序。"""
    paras = re.split(r'\n{2,}', text)
    return [p.strip() for p in paras if p.strip()]


def _sentences(para: str) -> List[str]:
    """将一段文本切成句子列表。"""
    parts = _SENT_END.split(para)
    return [s.strip() for s in parts if s.strip()]


def chunk_text(text: str) -> List[str]:
    """
    将长文本切分为若干块。

    Args:
        text: 原始文本内容

    Returns:
        块字符串列表（已去除空白碎片）
    """
    paragraphs = _split_by_paragraphs(text)

    chunks: List[str] = []
    buf = ""          # 当前积累的文本缓冲

    for para in paragraphs:
        sentences = _sentences(para)
        for sent in sentences:
            # 单句超过 CHUNK_SIZE，强制按字符截断
            if len(sent) > CHUNK_SIZE:
                # 先把缓冲区已有内容存档
                if len(buf) >= MIN_CHUNK:
                    chunks.append(buf)
                buf = ""
                # 对超长句按 CHUNK_SIZE 截断
                for i in range(0, len(sent), CHUNK_SIZE - OVERLAP):
                    piece = sent[i: i + CHUNK_SIZE]
                    if len(piece) >= MIN_CHUNK:
                        chunks.append(piece)
                # 最后一片作为新缓冲起点
                last_start = ((len(sent) - 1) // (CHUNK_SIZE - OVERLAP)) * (CHUNK_SIZE - OVERLAP)
                buf = sent[max(0, last_start - OVERLAP):]
                continue

            # 正常句：追加到缓冲区
            if buf:
                candidate = buf + sent
            else:
                candidate = sent

            if len(candidate) <= CHUNK_SIZE:
                buf = candidate
            else:
                # 缓冲区已满，存档并带重叠开新块
                if len(buf) >= MIN_CHUNK:
                    chunks.append(buf)
                # 重叠：取 buf 末尾 OVERLAP 字符作为新块起点
                overlap_prefix = buf[-OVERLAP:] if len(buf) > OVERLAP else buf
                buf = overlap_prefix + sent

        # 段落间不强制切块，让内容跨段自然积累至 CHUNK_SIZE

    # 处理残留
    if len(buf) >= MIN_CHUNK:
        chunks.append(buf)

    return chunks
