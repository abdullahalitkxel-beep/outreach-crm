"""Legal documents: fill in company details and render them as HTML (in the app) or plain text (installer)."""
import html
import re
import sys
from pathlib import Path

DOCS = {
    "terms": ("Terms of Use", "TERMS.md"),
    "privacy": ("Privacy Policy", "PRIVACY.md"),
    "acceptable-use": ("Acceptable Use Policy", "ACCEPTABLE_USE.md"),
}
PENDING = "(details pending)"


def legal_dir():
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return here / "legal"


def app_version():
    p = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "version.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "dev"


def values(cfg):
    lg = cfg.get("legal") or {}
    return {
        "company_name": lg.get("company_name") or "BlacSwanken",
        "address": lg.get("address") or PENDING,
        "contact_email": lg.get("contact_email") or PENDING,
        "version": str(lg.get("version") or "1.0"),
        "effective_date": str(lg.get("effective_date") or "2026-10-15"),
    }


def missing_details(cfg):
    lg = cfg.get("legal") or {}
    return [label for key, label in (("company_name", "registered company name"), ("address", "registered address"),
                                     ("contact_email", "contact email")) if not lg.get(key)]


def source(doc, cfg):
    text = (legal_dir() / DOCS[doc][1]).read_text(encoding="utf-8")
    for k, v in values(cfg).items():
        text = text.replace("{{" + k + "}}", v)
    return text


def _inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)

    def link(m):
        label, href = m.group(1), m.group(2)
        if href in DOCS or href == "third-party":
            return f'<a href="/legal/{href}">{label}</a>'
        return f'<a href="{href}" target="_blank" rel="noopener">{label}</a>'
    return re.sub(r"\[([^\[\]]+)\]\(([^)\s]+)\)", link, s)


def to_html(md):
    """Small Markdown subset: headings, paragraphs, bullet lists, tables, bold, code, links."""
    out, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline(line[level:].strip())}</h{level}>")
            i += 1
        elif line.lstrip().startswith("- "):
            items = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                items.append(f"<li>{_inline(lines[i].lstrip()[2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            out.append('<div class="scroll"><table class="tbl"><thead><tr>' + "".join(f"<th>{_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table></div>")
        else:
            para = []
            while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|")) and not lines[i].lstrip().startswith("- "):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{_inline(' '.join(para))}</p>")
    return "\n".join(out)


def to_text(md):
    """Plain text for the installer's licence page."""
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", md)
    t = re.sub(r"`(.+?)`", r"\1", t)
    t = re.sub(r"\[([^\[\]]+)\]\(([^)\s]+)\)", r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    return t


def third_party_text():
    p = legal_dir() / "THIRD_PARTY_NOTICES.txt"
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return "Third-party notices are generated when the app is built."


if __name__ == "__main__":
    # build helper: python legal.py <config.yaml> <out_dir>  -> writes EULA.txt for the installer
    import yaml
    cfg_path, out = sys.argv[1], Path(sys.argv[2])
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    text = "\n\n".join(to_text(source(d, cfg)) for d in ("terms", "acceptable-use", "privacy"))
    (out / "EULA.txt").write_text(text, encoding="utf-8")
    print("wrote", out / "EULA.txt")
