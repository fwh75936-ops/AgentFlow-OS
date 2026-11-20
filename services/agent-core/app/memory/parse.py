"""Document text extraction for knowledge imports.

Supported: .txt / .md (plain text), .pdf (pypdf), .docx (python-docx).
Anything else — or a file whose text cannot be extracted — raises
ValueError with a human reason; the import API reports it per file and
never pretends an unparseable file was indexed.
"""
import io

TEXT_EXTS = {".txt", ".md", ".markdown", ".text"}
SUPPORTED_EXTS = TEXT_EXTS | {".pdf", ".docx"}


def extract_text(filename: str, raw: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(tuple(TEXT_EXTS)):
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        raise ValueError("文本文件编码无法识别（已尝试 UTF-8 与 GBK）")
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("服务器缺少 PDF 解析组件 pypdf")
        try:
            reader = PdfReader(io.BytesIO(raw))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as e:
            raise ValueError(f"PDF 解析失败：{e}")
        text = "\n".join(pages).strip()
        if not text:
            raise ValueError("PDF 里没有可提取的文字（可能是扫描件图片）")
        return text
    if name.endswith(".docx"):
        try:
            import docx
        except ImportError:
            raise ValueError("服务器缺少 Word 解析组件 python-docx")
        try:
            doc = docx.Document(io.BytesIO(raw))
        except Exception as e:
            raise ValueError(f"Word 文档解析失败：{e}")
        parts = [p.text for p in doc.paragraphs]
        # Table cell text is real body content too: a document whose
        # text lives in tables must not come back "empty" or silently
        # lose the table contents.
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    parts.extend(p.text for p in cell.paragraphs)
        text = "\n".join(parts).strip()
        if not text:
            raise ValueError("Word 文档里没有正文文字")
        return text
    raise ValueError("不支持的文件格式，目前支持 txt、md、pdf、docx")
