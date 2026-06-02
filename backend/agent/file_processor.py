"""File processing pipeline for IM attachments.

When a user sends a file (PDF, TXT, etc.) via IM, this module:

1. Detects file attachments in the message
2. Downloads and extracts text content
3. Stores extracted content in session context for follow-up questions
4. Returns a structured summary for the agent to use

Design principles:
- Don't rely on keyword detection - always process file attachments
- Store extracted content so follow-up questions work
- Handle multiple files in one message
- Graceful degradation if extraction fails
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import httpx
from loguru import logger

if TYPE_CHECKING:
    from ..gateways.base import Attachment

# Max file size to download (10 MB)
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
# Max text to extract per file
MAX_TEXT_PER_FILE = 8000
# Max files to process per message
MAX_FILES_PER_MESSAGE = 5

# File type categories
_PDF_EXTENSIONS = {".pdf"}
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".java", ".cpp", ".c", ".go", ".rs",
    ".html", ".htm", ".css", ".xml", ".toml", ".ini",
    ".sh", ".bash", ".sql", ".log", ".env",
}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}


@dataclass(slots=True)
class ExtractedFile:
    """Result of extracting content from a single file."""
    name: str
    url: str
    mime_type: str
    content: str = ""
    error: str = ""
    success: bool = False
    char_count: int = 0


@dataclass(slots=True)
class FileProcessingResult:
    """Result of processing all files in a message."""
    files: list[ExtractedFile] = field(default_factory=list)
    has_files: bool = False
    all_text: str = ""  # Combined text from all files

    @property
    def success_count(self) -> int:
        return sum(1 for f in self.files if f.success)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.files if not f.success and f.error)


def _get_extension(url: str, name: str) -> str:
    """Get file extension from URL or filename."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if not ext and name:
        ext = Path(name).suffix.lower()
    return ext


def _extract_pdf(content: bytes) -> str:
    """Extract text from PDF bytes."""
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(content))
        pages_text: list[str] = []
        for i, page in enumerate(reader.pages[:20]):  # limit to 20 pages
            text = page.extract_text()
            if text:
                pages_text.append(f"--- 第 {i + 1} 页 ---\n{text}")
        return "\n\n".join(pages_text)
    except ImportError:
        return "[PDF 提取需要 pypdf 库]"
    except Exception as exc:
        return f"[PDF 提取失败: {exc}]"


def _extract_text(content: bytes, ext: str) -> str:
    """Extract text from text-based files."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = content.decode("gbk")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
    return text


async def _download_file(url: str) -> tuple[bytes, str]:
    """Download file from URL. Returns (content, error)."""
    try:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, trust_env=True,
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return b"", f"下载失败: HTTP {resp.status_code}"
            content = resp.content
            if len(content) > MAX_DOWNLOAD_BYTES:
                return b"", f"文件太大: {len(content)} 字节"
            return content, ""
    except Exception as exc:
        return b"", f"下载失败: {exc}"


async def process_attachments(
    attachments: list["Attachment"],
    *,
    workspace_dir: Optional[Path] = None,
) -> FileProcessingResult:
    """Process all file attachments in a message.

    Downloads files, extracts text content, and returns structured results.
    """
    result = FileProcessingResult()

    # Filter to processable file attachments
    file_attachments = [
        att for att in attachments
        if att.kind in ("file", "image") and att.url
    ][:MAX_FILES_PER_MESSAGE]

    if not file_attachments:
        return result

    result.has_files = True

    for att in file_attachments:
        ext = _get_extension(att.url or "", att.name or "")
        extracted = ExtractedFile(
            name=att.name or "未命名文件",
            url=att.url or "",
            mime_type=att.mime_type or "",
        )

        # Skip images - use vision model instead
        if ext in _IMAGE_EXTENSIONS:
            extracted.content = "[图片文件 - 请使用视觉模型识别]"
            extracted.success = True
            result.files.append(extracted)
            continue

        # Download file
        content, error = await _download_file(att.url or "")
        if error:
            extracted.error = error
            result.files.append(extracted)
            continue

        # Extract content based on type
        if ext in _PDF_EXTENSIONS:
            extracted.content = _extract_pdf(content)
        elif ext in _TEXT_EXTENSIONS:
            extracted.content = _extract_text(content, ext)
        else:
            # Try as text
            try:
                extracted.content = _extract_text(content, ext)
            except Exception:
                extracted.error = f"不支持的文件格式: {ext}"
                result.files.append(extracted)
                continue

        # Truncate if too long
        if len(extracted.content) > MAX_TEXT_PER_FILE:
            extracted.content = extracted.content[:MAX_TEXT_PER_FILE] + "\n[...截断]"

        extracted.char_count = len(extracted.content)
        extracted.success = True
        result.files.append(extracted)

    # Build combined text
    all_texts: list[str] = []
    for f in result.files:
        if f.success and f.content:
            all_texts.append(f"=== {f.name} ===\n{f.content}")
    result.all_text = "\n\n".join(all_texts)

    return result


def build_agent_prompt(result: FileProcessingResult) -> str:
    """Build a prompt for the agent based on extracted file content.

    This prompt tells the agent what files were processed and gives it
    the extracted content to work with.
    """
    if not result.has_files:
        return ""

    parts: list[str] = ["[系统提示：用户发送了以下文件，已自动提取内容]"]

    for f in result.files:
        if f.success:
            parts.append(f"\n## 文件: {f.name}")
            parts.append(f"格式: {f.mime_type}")
            parts.append(f"内容:\n{f.content}")
        else:
            parts.append(f"\n## 文件: {f.name} - 提取失败: {f.error}")

    parts.append("\n[请根据以上文件内容回答用户的问题。如果用户没有指定问题，请总结文件内容。]")

    return "\n".join(parts)


def build_followup_context(extracted_files: list[ExtractedFile]) -> str:
    """Build context for follow-up questions about previously extracted files.

    Stored in session context so the agent can reference file content
    in subsequent turns.
    """
    if not extracted_files:
        return ""

    parts: list[str] = ["[之前提取的文件内容:]"]
    for f in extracted_files:
        if f.success and f.content:
            # Store a shorter version for context
            short_content = f.content[:2000] + "..." if len(f.content) > 2000 else f.content
            parts.append(f"\n## {f.name}\n{short_content}")

    return "\n".join(parts)
