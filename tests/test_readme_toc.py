"""Guards for the README table of contents.

The README is rendered on both Forgejo (canonical) and GitHub (mirror), whose
heading sluggers disagree whenever a heading contains emoji or punctuation
(e.g. ``## 🌟 Overview`` becomes ``#overview`` on Forgejo but ``#-overview`` on
GitHub). ``scripts/update_toc.py`` therefore generates Forgejo-style slugs and
every non-plain heading must carry an explicit ``<a name="slug"></a>`` anchor so
the TOC links resolve on both forges. These tests keep the TOC fresh and the
anchors in sync with their headings.
"""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

_spec = importlib.util.spec_from_file_location("update_toc", ROOT / "scripts" / "update_toc.py")
update_toc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update_toc)

_TOC_ENTRY_RE = re.compile(r"^[*+\s-]+\[[^\]]+\]\(#(?P<frag>[^)]+)\)\s*$")
_ANCHOR_RE = re.compile(r"<a\s+(?:name|id)=\"(?P<name>[^\"]+)\"")
_OWN_ANCHOR_RE = re.compile(r"<a\s+(?:name|id)=\"[^\"]+\"\s*>\s*</a>")
_PLAIN_TITLE_RE = re.compile(r"^[A-Za-z0-9 -]+$")


def _toc_fragments(text: str) -> list[str]:
    in_toc = False
    frags = []
    for line in text.splitlines():
        if update_toc.TOC_START in line:
            in_toc = True
            continue
        if update_toc.TOC_END in line:
            break
        if in_toc:
            match = _TOC_ENTRY_RE.match(line)
            if match:
                frags.append(match.group("frag"))
    return frags


def test_toc_is_up_to_date():
    text = README.read_text(encoding="utf-8")
    assert (
        update_toc.update_text(text) == text
    ), "README.md table of contents is stale; run `python scripts/update_toc.py README.md`"


def test_every_heading_reachable_from_toc():
    text = README.read_text(encoding="utf-8")
    frags = _toc_fragments(text)
    anchors = set(_ANCHOR_RE.findall(text))
    slugs = {slug for _, _, slug in update_toc.headings(text)}

    assert frags, "no TOC links found between the markers"
    for frag in frags:
        assert frag in slugs or frag in anchors, f"TOC link #{frag} has no matching heading or anchor"


def test_non_plain_headings_have_explicit_anchors():
    """Headings whose title contains emoji or punctuation slug differently on
    each forge; they must carry an explicit ``<a name="{slug}">`` anchor so the
    TOC link resolves everywhere."""
    text = README.read_text(encoding="utf-8")
    lines = text.splitlines()
    for lineno, _, raw_title in update_toc.iter_headings(text):
        visible = _OWN_ANCHOR_RE.sub("", raw_title)
        if _PLAIN_TITLE_RE.match(visible.strip()):
            continue
        slug = update_toc.slugify(visible)
        assert re.search(
            rf'<a\s+name="{re.escape(slug)}"', lines[lineno]
        ), f'heading needs an explicit anchor: <a name="{slug}"></a> in {lines[lineno].strip()!r}'


def test_slugify_matches_forgejo():
    """Slugs must match what the canonical host (Forgejo) generates."""
    assert update_toc.slugify("🌟 Overview") == "overview"
    assert update_toc.slugify("📢 Social & federated") == "social-federated"
    assert update_toc.slugify("⚙️ Configuration") == "configuration"
    assert update_toc.slugify("</> API") == "api"
    assert update_toc.slugify("Subsonic-compatible clients") == "subsonic-compatible-clients"
    assert update_toc.slugify("User-facing features and toggles") == "user-facing-features-and-toggles"
