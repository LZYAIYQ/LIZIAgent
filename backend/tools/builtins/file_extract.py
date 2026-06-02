"""file_extract: extract text content from uploaded files.

Supports common file formats:
- PDF (.pdf) — uses pypdf (already a transitive dependency)
- Text (.txt, .md, .csv, .json, .yaml, .yml) — direct read
- Code (.py, .js, .ts, .java, .cpp, .c, .go, .rs) — direct read
- Images (.jpg, .png, .gif, .webp) — returns placeholder (use vision model)

This tool is designed for files uploaded via IM gateways. The file
must be accessible via URL or local path.

Permission tier: SAFE (read-only extraction).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from ..base import Tool, ToolPermission, ToolResult

# Max file size to download (10 MB)
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
# Max text output chars
MAX_OUTPUT_CHARS = 8000

# Supported text file extensions
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cpp", ".c",
    ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala",
    ".html", ".htm", ".css", ".scss", ".less",
    ".xml", ".toml", ".ini", ".cfg", ".conf",
    ".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1",
    ".sql", ".graphql", ".proto",
    ".rst", ".tex", ".bib",
    ".log", ".env", ".gitignore", ".dockerignore",
    ".makefile", ".cmake", ".gradle",
})

# Image extensions (return placeholder, use vision model instead)
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"})

# PDF extension
_PDF_EXTENSIONS = frozenset({".pdf"})


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
                pages_text.append(f"--- Page {i + 1} ---\n{text}")
        return "\n\n".join(pages_text)
    except ImportError:
        return "[PDF 提取需要 pypdf 库，请安装: pip install pypdf]"
    except Exception as exc:
        return f"[PDF 提取失败: {exc}]"


class FileExtractTool(Tool):
    name = "file_extract"
    description = (
        "Extract text content from a file URL or local path. Supports PDF,"
        " text files, code files, and common formats. For images, use the"
        " vision model directly instead. Returns up to 8000 chars of"
        " extracted text."
    )
    permission = ToolPermission.SAFE
    is_read_only = True
    is_concurrency_safe = True
    is_destructive = False
    max_result_chars = MAX_OUTPUT_CHARS
    search_hint = "file extract pdf text read document 文件 提取 解析"
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP(S) URL of the file to extract.",
            },
            "path": {
                "type": "string",
                "description": "Local file path (relative to workspace).",
            },
        },
        "required": [],
    }

    def __init__(self, workspace_dir: Any = None) -> None:
        self._workspace_dir = Path(workspace_dir) if workspace_dir else None

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        url = str(arguments.get("url") or "").strip()
        path = str(arguments.get("path") or "").strip()

        if not url and not path:
            return ToolResult(ok=False, content="", error="必须提供 url 或 path 参数")

        try:
            if url:
                return await self._extract_from_url(url)
            else:
                return self._extract_from_path(path)
        except Exception as exc:
            logger.exception("[file_extract] extraction failed")
            return ToolResult(ok=False, content="", error=f"文件提取失败: {exc}")

    async def _extract_from_url(self, url: str) -> ToolResult:
        """Download and extract from URL."""
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, trust_env=True,
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return ToolResult(
                    ok=False, content="",
                    error=f"下载失败: HTTP {resp.status_code}",
                )
            content = resp.content
            if len(content) > MAX_DOWNLOAD_BYTES:
                return ToolResult(
                    ok=False, content="",
                    error=f"文件太大（{len(content)} > {MAX_DOWNLOAD_BYTES} 字节）",
                )
            # Guess extension from URL
            from urllib.parse import urlparse
            parsed = urlparse(url)
            ext = Path(parsed.path).suffix.lower()
            return self._extract_bytes(content, ext, url)

    def _extract_from_path(self, path: str) -> ToolResult:
        """Extract from local file path."""
        if self._workspace_dir:
            file_path = self._workspace_dir / path
        else:
            file_path = Path(path)

        # Security: prevent path traversal
        try:
            resolved = file_path.resolve()
            if self._workspace_dir and not resolved.is_relative_to(self._workspace_dir.resolve()):
                return ToolResult(ok=False, content="", error="路径超出工作目录范围")
        except Exception:
            pass

        if not file_path.exists():
            return ToolResult(ok=False, content="", error=f"文件不存在: {path}")
        if not file_path.is_file():
            return ToolResult(ok=False, content="", error=f"不是文件: {path}")

        ext = file_path.suffix.lower()
        try:
            content = file_path.read_bytes()
        except Exception as exc:
            return ToolResult(ok=False, content="", error=f"读取失败: {exc}")

        return self._extract_bytes(content, ext, str(file_path))

    def _extract_bytes(self, content: bytes, ext: str, source: str) -> ToolResult:
        """Extract text from file bytes based on extension."""
        # Image — return placeholder
        if ext in _IMAGE_EXTENSIONS:
            return ToolResult(
                ok=True,
                content=(
                    f"[图片文件: {source}]\n"
                    "图片内容需要通过视觉模型识别。请直接在对话中发送图片，"
                    "或使用支持 vision 的 LLM 模型。"
                ),
            )

        # PDF
        if ext in _PDF_EXTENSIONS:
            text = _extract_pdf(content)
            if len(text) > MAX_OUTPUT_CHARS:
                text = text[:MAX_OUTPUT_CHARS] + "\n[...截断]"
            return ToolResult(ok=True, content=f"[PDF: {source}]\n\n{text}")

        # Text files
        if ext in _TEXT_EXTENSIONS:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text = content.decode("gbk")
                except UnicodeDecodeError:
                    text = content.decode("latin-1")
            if len(text) > MAX_OUTPUT_CHARS:
                text = text[:MAX_OUTPUT_CHARS] + "\n[...截断]"
            return ToolResult(ok=True, content=f"[文件: {source}]\n\n{text}")

        # Unknown — try as text
        try:
            text = content.decode("utf-8")
            if len(text) > MAX_OUTPUT_CHARS:
                text = text[:MAX_OUTPUT_CHARS] + "\n[...截断]"
            return ToolResult(ok=True, content=f"[文件: {source}]\n\n{text}")
        except UnicodeDecodeError:
            return ToolResult(
                ok=False, content="",
                error=f"不支持的文件格式: {ext}（二进制文件无法提取文本）",
            )
