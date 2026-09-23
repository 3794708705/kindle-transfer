"""Helper to create a minimal, valid, DRM-free EPUB file for testing.

The EPUB is self-contained: no external fonts, images, or network resources.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def create_minimal_epub(output_path: Path, title: str = "KindleTransfer Integration Test",
                         author: str = "KindleTransfer") -> Path:
    """Create a minimal valid EPUB 2.0 file at the given path.

    The EPUB contains:
    - mimetype file (must be first in ZIP, uncompressed)
    - META-INF/container.xml
    - content.opf (metadata + manifest + spine)
    - toc.ncx (NCX table of contents)
    - Two XHTML chapters (English + Chinese content)

    Args:
        output_path: Where to write the EPUB file.
        title: Book title.
        author: Book author.

    Returns:
        The path to the created EPUB file.
    """
    book_id = "kindle_transfer_test_001"

    # ── mimetype ──
    mimetype = "application/epub+zip"

    # ── META-INF/container.xml ──
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    # ── content.opf ──
    content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:creator opf:role="aut">{author}</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier id="book-id">{book_id}</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter1"/>
    <itemref idref="chapter2"/>
  </spine>
</package>"""

    # ── toc.ncx ──
    toc_ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>{title}</text>
  </docTitle>
  <navMap>
    <navPoint id="navpoint-1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
    <navPoint id="navpoint-2" playOrder="2">
      <navLabel><text>Chapter 2</text></navLabel>
      <content src="chapter2.xhtml"/>
    </navPoint>
  </navMap>
</ncx>"""

    # ── chapter1.xhtml ──
    chapter1 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 1</title>
</head>
<body>
  <h1>Chapter One</h1>
  <p>Hello KindleTransfer.</p>
  <p>This is a minimal EPUB file created for integration testing.</p>
  <p>你好，KindleTransfer。</p>
  <p>这是一个用于集成测试的最小 EPUB 文件。</p>
</body>
</html>"""

    # ── chapter2.xhtml ──
    chapter2 = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Chapter 2</title>
</head>
<body>
  <h1>Chapter Two</h1>
  <p>这里是中文测试。</p>
  <p>This is chapter two content.</p>
  <p>KindleTransfer V0.1.2 integration test.</p>
</body>
</html>"""

    # ── Build the EPUB ZIP ──
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype MUST be first, stored (not compressed)
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            mimetype,
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("content.opf", content_opf)
        zf.writestr("toc.ncx", toc_ncx)
        zf.writestr("chapter1.xhtml", chapter1)
        zf.writestr("chapter2.xhtml", chapter2)

    return output_path