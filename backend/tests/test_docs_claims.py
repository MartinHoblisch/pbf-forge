"""Bind documented claims to the code that makes them true.

A documentation pass rots at the next commit unless something fails when the
code moves out from under it. Each test here names a statement the docs make
and the place in the source that has to keep it true, so a change that
invalidates a claim fails CI instead of quietly shipping.

The pattern is "claim implies code": a doc may only say X while the source
still does X. Removing the sentence is always a valid way to make one of these
pass; so is fixing the code. Silently disagreeing is not.

CHANGELOG.md is exempt from the claim checks. It is a historical record and
deliberately quotes statements that are no longer true, marked as corrections.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend" / "index.html"

# Files whose factual claims must hold against the current source.
CLAIM_DOCS = [
    REPO / "README.md",
    REPO / "CONTRIBUTING.md",
    REPO / "SECURITY.md",
    REPO / "DEVELOPMENT.md",
    *sorted((REPO / "docs").glob("*.md")),
]

# Every tracked markdown file, for the checks that are about form, not claims.
ALL_DOCS = [
    *CLAIM_DOCS,
    REPO / "CHANGELOG.md",
    REPO / "CODE_OF_CONDUCT.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _existing(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p.is_file()]


def _code_blocks(text: str) -> list[str]:
    """Return the contents of every fenced code block.

    The fence may be indented: a code block inside a numbered list is, and that
    is exactly where the docs show what to type into the form.
    """
    return re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", text, re.MULTILINE | re.DOTALL)


# --------------------------------------------------------------------------
# The prefix trap
# --------------------------------------------------------------------------

_PREFIXED_EXPR = re.compile(r"^\s*(?:n|w|r|nwr)/\S*=", re.MULTILINE)


def prefixed_expression_offenders(text: str) -> list[str]:
    """Lines in fenced code blocks that offer an already-prefixed expression.

    Shell commands are exempt: they show what the tool builds, not what a user
    types. Line continuations are joined first, so the tail of a wrapped osmium
    invocation is not mistaken for an input line.
    """
    offenders = []
    for block in _code_blocks(text):
        for line in re.sub(r"\\\n\s*", " ", block).splitlines():
            stripped = line.strip()
            if stripped.startswith(("osmium", "$", "#", "docker", "git", "pip", "pytest")):
                continue
            if _PREFIXED_EXPR.match(line):
                offenders.append(stripped)
    return offenders


def test_no_prefixed_expression_offered_as_input():
    """Docs must not present a prefixed expression as something to type.

    _build_expressions() in filter_manager.py derives the n/, w/ and r/ prefix
    from job.geometry_types and prepends it to every expression, so an
    expression that already carries one is prefixed twice and matches nothing.
    """
    offenders = [
        f"{path.name}: {bad}"
        for path in _existing(CLAIM_DOCS)
        for bad in prefixed_expression_offenders(_read(path))
    ]
    assert not offenders, (
        "code blocks offer a prefixed expression as user input; the tool adds "
        "the prefix itself (filter_manager.py _build_expressions): " + "; ".join(offenders)
    )


def test_prefix_detector_catches_a_reintroduced_claim():
    """The guard above, watched failing. A guard never seen to fail is not one."""
    violating = "Type this:\n\n```\nw/highway=footway\n```\n"
    assert prefixed_expression_offenders(violating) == ["w/highway=footway"]

    # The same expression as part of a command is what the tool builds, not
    # what a user types, and must not be reported.
    command = "```\nosmium tags-filter in.pbf w/highway=footway -o out.pbf\n```\n"
    assert prefixed_expression_offenders(command) == []

    # Indented inside a numbered list, which is where the docs show input.
    indented = "1. Type this:\n\n   ```\n   r/route=bicycle\n   ```\n"
    assert prefixed_expression_offenders(indented) == ["r/route=bicycle"]

    assert prefixed_expression_offenders("```\nhighway=footway\n```\n") == []


def test_filter_manager_still_builds_the_prefix():
    """The premise of the test above. If this changes, the docs must change."""
    src = _read(REPO / "backend" / "filter_manager.py")
    assert 'prefix_map = {"nodes": "n", "ways": "w", "relations": "r"}' in src
    assert 'exprs.append(f"{p}/{tag}")' in src


# --------------------------------------------------------------------------
# GeoPackage layers
# --------------------------------------------------------------------------


def multi_layer_offenders(text: str) -> list[str]:
    """Paragraphs presenting the GDAL OSM driver's layer split as current.

    Paragraph granularity, not line: prose wraps, so a disclaimer and the names
    it disclaims are rarely on the same line. A paragraph saying the split is
    gone may name it.
    """
    banned = ("multilinestrings", "other_relations")
    historical = ("no longer", "up to version", "used to", "before ")
    offenders = []
    for para in re.split(r"\n\s*\n", text):
        if not any(word in para for word in banned):
            continue
        if any(marker in para.lower() for marker in historical):
            continue
        offenders.append(" ".join(para.split())[:90])
    return offenders


def test_multi_layer_detector_distinguishes_claim_from_history():
    current = "Output is split into points, lines, multilinestrings and other_relations."
    assert multi_layer_offenders(current)

    historical = (
        "Up to version 1.0.0 the output was split into points, lines,\n"
        "multilinestrings, multipolygons and other_relations; that is no\n"
        "longer the case."
    )
    assert multi_layer_offenders(historical) == []

    assert multi_layer_offenders("One layer, named after the output file.") == []


def test_no_multi_layer_geopackage_claim():
    """One layer named after the output file, not the GDAL OSM driver's five.

    filter_manager.py passes -nln out_file.stem. The old layer names must not
    reappear in documentation while that is what the code does.
    """
    offenders = [
        f"{path.name}: {bad}"
        for path in _existing(CLAIM_DOCS)
        for bad in multi_layer_offenders(_read(path))
    ]
    assert not offenders, (
        "docs name the GDAL OSM driver's layer split, but the export writes a "
        "single layer via -nln: " + "; ".join(offenders)
    )


def test_geopackage_layer_is_named_after_the_file():
    src = _read(REPO / "backend" / "filter_manager.py")
    assert '"-nln",' in src and "out_file.stem" in src


# --------------------------------------------------------------------------
# GeoJSON
# --------------------------------------------------------------------------


def test_rfc7946_claimed_only_if_requested_from_ogr():
    """RFC 7946 conformance has to be asked of ogr2ogr with -lco RFC7946=YES."""
    # Exclude the test tree: this file names the string it is looking for, and
    # would otherwise report that the code requests RFC 7946 because the guard
    # mentions it.
    backend_src = "\n".join(
        _read(p)
        for p in sorted((REPO / "backend").rglob("*.py"))
        if p.is_file() and "tests" not in p.parts
    )
    code_requests_it = "RFC7946" in backend_src
    docs_claim_it = [
        p.name for p in _existing(CLAIM_DOCS) if "RFC 7946" in _read(p) or "RFC7946" in _read(p)
    ]
    if not code_requests_it:
        assert not docs_claim_it, (
            "docs claim RFC 7946 conformance but no -lco RFC7946=YES is passed "
            "to ogr2ogr: " + ", ".join(docs_claim_it)
        )


def _geojson_threshold_mb() -> int:
    """The source-size threshold that triggers the GeoJSON warning."""
    html = _read(FRONTEND)
    match = re.search(r"geojsonChecked && sizeMB > (\d+)", html)
    assert match, "could not find the GeoJSON size guardrail in frontend/index.html"
    return int(match.group(1))


def test_documented_geojson_threshold_matches_the_frontend():
    threshold = _geojson_threshold_mb()
    limits = REPO / "docs" / "limits.md"
    if limits.is_file():
        text = _read(limits)
        assert f"{threshold} MB" in text, (
            f"docs/limits.md does not state the actual threshold of {threshold} MB"
        )


def test_no_invented_geojson_thresholds():
    """500 MB and 1 M features appear nowhere in the code."""
    offenders = [
        p.name
        for p in _existing(CLAIM_DOCS)
        if re.search(r"1 ?M features|1,000,000 features", _read(p))
    ]
    assert not offenders, "docs state a feature-count threshold that does not exist: " + ", ".join(
        offenders
    )


# --------------------------------------------------------------------------
# CI and security posture
# --------------------------------------------------------------------------


def test_windows_ci_not_described_as_the_full_suite():
    """ci.yml deselects markers on Windows, so 'full test suite' is untrue there."""
    ci = _read(REPO / ".github" / "workflows" / "ci.yml")
    windows_is_partial = "not docker and not posix and not integration" in ci
    if windows_is_partial:
        for path in _existing(CLAIM_DOCS):
            for line in _read(path).splitlines():
                if "Windows" in line and re.search(r"full (test )?suite", line):
                    pytest.fail(
                        f"{path.name} calls the Windows run a full suite while ci.yml "
                        f"excludes markers: {line.strip()}"
                    )


def test_trivy_described_with_its_ignore_unfixed_setting():
    """security.yml sets ignore-unfixed, so an unqualified 'build fails' misleads."""
    sec = _read(REPO / ".github" / "workflows" / "security.yml")
    if "ignore-unfixed: true" not in sec:
        return
    # Only where a doc describes the effect on the build. A bare mention in a
    # list of what CI runs makes no claim about severity handling.
    for path in _existing([REPO / "README.md", REPO / "SECURITY.md"]):
        text = _read(path)
        if "Trivy" not in text:
            continue
        assert "fix is available" in text or "fix available" in text, (
            f"{path.name} mentions Trivy without saying that only findings with an "
            "available fix break the build (ignore-unfixed: true)"
        )


def test_dependabot_ecosystem_count_matches_the_config():
    config = _read(REPO / ".github" / "dependabot.yml")
    ecosystems = set(re.findall(r"package-ecosystem:\s*[\"']?([a-z-]+)", config))
    readme = _read(REPO / "README.md")
    if "Dependabot" in readme:
        for name in ("pip", "GitHub Actions", "Docker", "pre-commit"):
            key = {"GitHub Actions": "github-actions", "Docker": "docker"}.get(name, name)
            if key in ecosystems:
                assert name in readme, f"README omits the {name} Dependabot ecosystem"


# --------------------------------------------------------------------------
# Things that no longer exist
# --------------------------------------------------------------------------


def test_no_discussions_links():
    offenders = [p.name for p in _existing(ALL_DOCS) if "/discussions" in _read(p)]
    assert not offenders, "Discussions are disabled: " + ", ".join(offenders)


def test_no_region_browser_claim():
    """The word does not occur in the frontend, and the feature is on the roadmap."""
    assert "region" not in _read(FRONTEND).lower().replace("regional", ""), (
        "a region feature appeared in the frontend; the docs may now describe one"
    )


def test_no_claim_that_extracts_are_offered_out_of_the_box():
    """CONTINENTAL_URLS is a fallback for filenames, not a catalogue.

    It has no endpoint and no control. The download list is built from the data
    directory, so on a fresh install it is empty. Documentation that promises
    ready-made entries sends a new reader looking for something that is not
    there.
    """
    banned = ("continental extract", "built-in extract")
    offenders = [
        f"{p.name}: {phrase}"
        for p in _existing(CLAIM_DOCS)
        for phrase in banned
        if phrase in _read(p).lower()
    ]
    assert not offenders, "the built-in URLs never reach the interface: " + ", ".join(offenders)


def test_the_download_url_field_names_no_host():
    """Any host works, so the label must not read as a restriction.

    _validate_url rejects only loopback and private addresses. Naming one host
    in the field label tells the reader the others are not allowed.
    """
    frontend = _read(FRONTEND)
    hints = re.findall(r"url_hint: '([^']*)'", frontend)
    assert hints, "url_hint is no longer a plain string in the translation tables"
    for hint in hints:
        label = hint.split("(")[0]  # the example after "(e.g. ...)" may name one
        assert "geofabrik" not in label.lower(), f"the URL field label names a host: {label!r}"


# --------------------------------------------------------------------------
# Form
# --------------------------------------------------------------------------


def test_no_absolute_windows_paths_in_docs():
    offenders = [
        f"{p.name}: {m}"
        for p in _existing(ALL_DOCS)
        for m in re.findall(r"[A-Z]:\\\\?[A-Za-z0-9_\-]+", _read(p))
        if m not in ("C:", "D:")
    ]
    allowed = ("D:\\osm-data", "C:\\Users")
    offenders = [o for o in offenders if not any(a in o for a in allowed)]
    assert not offenders, "absolute paths from one machine: " + "; ".join(offenders)


def test_no_emoji_in_headings():
    pattern = re.compile(r"^#{1,6} .*[\U0001F300-\U0001FAFF\u2600-\u27BF]", re.MULTILINE)
    offenders = [p.name for p in _existing(ALL_DOCS) if pattern.search(_read(p))]
    assert not offenders, "emoji in headings: " + ", ".join(offenders)


def test_relative_links_resolve():
    link = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    broken = []
    for path in _existing(ALL_DOCS):
        for target in link.findall(_read(path)):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                broken.append(f"{path.name} -> {target}")
    assert not broken, "broken relative links: " + "; ".join(broken)


def test_no_german_ui_strings_in_english_docs():
    """A German label pasted from the interface is a sign the doc was not checked."""
    blocked = [
        "OSM-Tags zum Ausschlie\u00dfen",
        "Keine Treffer",
        "wird gestoppt",
        "Gestoppt.",
    ]
    offenders = [
        f"{p.name}: {word}" for p in _existing(CLAIM_DOCS) for word in blocked if word in _read(p)
    ]
    assert not offenders, "German interface strings in English docs: " + "; ".join(offenders)


def test_presets_file_shape_is_json_if_present():
    """The preset store is JSON; docs describing it must not drift to another format."""
    presets = REPO / "config" / ".osm_tool_presets.json"
    if presets.is_file() and presets.stat().st_size:
        json.loads(_read(presets))


def test_dropped_feature_count_is_documented_and_written():
    """limits.md promises a "Dropped features" line in the report.

    The count comes from osmium's verbose summary, parsed by
    _parse_geometry_errors() and written by the report builder. If either goes,
    the promise in limits.md is empty.
    """
    docs = _read(REPO / "docs" / "limits.md")
    src = _read(REPO / "backend" / "filter_manager.py")
    assert "Dropped features" in docs, "limits.md no longer describes the count"
    assert '"Dropped features"' in src, (
        "limits.md promises a Dropped features line that the report no longer writes"
    )
    assert "_parse_geometry_errors" in src, (
        "nothing parses osmium's error count, so the report line can never fire"
    )
