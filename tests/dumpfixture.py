"""Build tiny, synthetic Wikivoyage dumps for tests. No real article text is copied."""

import bz2
from pathlib import Path
from xml.sax.saxutils import escape

NS = "http://www.mediawiki.org/xml/export-0.11/"


def page(
    title, text="", *, page_id=1, ns=0, redirect=None, revision=100, ts="2026-10-01T00:00:00Z"
):
    redirect_xml = f'<redirect title="{escape(redirect)}" />' if redirect else ""
    return (
        f"<page><title>{escape(title)}</title><ns>{ns}</ns><id>{page_id}</id>{redirect_xml}"
        f"<revision><id>{revision}</id><timestamp>{ts}</timestamp>"
        f'<text xml:space="preserve">{escape(text)}</text></revision></page>'
    )


def write_dump(path: Path, pages: list[str]) -> Path:
    xml = f'<mediawiki xmlns="{NS}"><siteinfo><sitename>Wikivoyage</sitename></siteinfo>'
    xml += "".join(pages) + "</mediawiki>"
    path.write_bytes(bz2.compress(xml.encode("utf-8")))
    return path
