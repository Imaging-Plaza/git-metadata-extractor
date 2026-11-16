"""Conservative primary-paper references from fetched repository documents.

These are citation IRIs, not inferred ScholarlyArticle entities: a reference
does not supply a publication date or a resolvable Person author. Never scan
the whole README for DOI strings; incidental bibliographies are not project
citations. A title explicitly requested in a citation section may, however,
be matched exactly to a linked bibliography paragraph elsewhere in that file.
"""

from __future__ import annotations

import html
import re
from typing import Any

from git_metadata_extractor.canonicalization.doi import doi_iri
from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.parsers.citation_cff import parse_citation_cff

_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+")
_ARXIV = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE,
)
_ENTRY = re.compile(r"@([a-z]+)\s*\{", re.IGNORECASE)
_FIELD = re.compile(r"([a-z][a-z0-9_-]*)\s*=\s*", re.IGNORECASE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+)$", re.MULTILINE)
_CITATION_HEADING = re.compile(
    r"^(?:how to cite\b|citing\b|citations?\b)", re.IGNORECASE,
)
_REQUEST = re.compile(
    r"(?:^\s*\*{0,2}citation\*{0,2}\s*:|"
    r"\b(?:this|the)\s+(?:repo(?:sitory)?|code|program|software|toolbox)\b[^\n]*"
    r"(?:\bplease cite\b|\baccompan\w*\b[^\n]*\bpaper\b))",
    re.IGNORECASE,
)


class ProjectCitation(InternalModel):
    iri: str
    title: str | None = None
    source: str


def _reference(text: str) -> str | None:
    """Prefer a DOI to the arXiv mirror in one bibliographic reference."""
    for match in _DOI.finditer(text):
        value = match.group().rstrip(".,;")
        while value.endswith(")") and value.count(")") > value.count("("):
            value = value[:-1]
        # Software/data archive identifiers do not establish a paper.
        if value.lower().startswith("10.5281/zenodo."):
            continue
        if value.lower().endswith((".svg", ".png")):
            continue
        if iri := doi_iri(value):
            return iri
    match = _ARXIV.search(text)
    return f"https://arxiv.org/abs/{match.group(1)}" if match else None


def _balanced_value(text: str, start: int) -> tuple[str, int]:
    """Read a BibTeX braced, quoted or bare value, retaining nested braces."""
    opening = text[start : start + 1]
    if opening not in {"{", '"'}:
        end = re.search(r"[,\n}]", text[start:])
        stop = start + end.start() if end else len(text)
        return text[start:stop].strip(), stop
    depth = 1
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if opening == '"' and char == '"':
            return text[start + 1 : index], index + 1
        if opening == "{":
            depth += (char == "{") - (char == "}")
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return "", len(text)


def _bibtex(text: str) -> list[dict[str, str]]:
    text = re.sub(r"(?m)^\s*%.*$", "", text)
    records: list[dict[str, str]] = []
    index = 0
    while entry := _ENTRY.search(text, index):
        # Advance over the entire balanced entry, including unsupported kinds.
        # An @article example nested inside @comment is not a separate record.
        body, index = _balanced_value(text, entry.end() - 1)
        kind = entry.group(1).lower()
        if kind not in {"article", "inproceedings", "conference", "misc"}:
            continue
        fields = _bibtex_fields(body.partition(",")[2])
        is_arxiv = fields.get("archiveprefix", "").lower() == "arxiv" and _arxiv_eprint(fields)
        if fields.get("title") and (kind != "misc" or is_arxiv):
            records.append(fields)
    return records


def _bibtex_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(text):
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        field = _FIELD.match(text, index)
        if field is None:
            break
        value, index = _balanced_value(text, field.end())
        fields[field.group(1).lower()] = re.sub(
            r"\s+", " ", value.replace("{", "").replace("}", ""),
        ).strip()
    return fields


def _arxiv_eprint(record: dict[str, str]) -> str | None:
    eprint = record.get("eprint", "")
    if record.get("archiveprefix", "").lower() == "arxiv" and re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", eprint):
        return f"https://arxiv.org/abs/{eprint}"
    return None


def _unambiguous_reference(text: str) -> str | None:
    candidates = {
        iri for pattern in (_DOI, _ARXIV) for match in pattern.finditer(text)
        if (iri := _reference(match.group()))
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _title_reference(title: str, paragraphs: list[str]) -> str | None:
    title_key = _title_key(title)
    for paragraph in paragraphs:
        if not title_key or title_key not in _title_key(paragraph):
            continue
        # A separate line can distinguish adjacent bibliography items. If the
        # title wraps, accept the paragraph only when it has one identifier.
        for line in paragraph.splitlines():
            if title_key in _title_key(line) and (iri := _unambiguous_reference(line)):
                return iri
        if iri := _unambiguous_reference(paragraph):
            return iri
    return None


def _title_key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", html.unescape(text).lower()))


def _citation_sections(readme: str, paragraphs: list[str]) -> list[str]:
    sections: list[str] = []
    headings = list(_HEADING.finditer(readme))
    for index, heading in enumerate(headings):
        if _CITATION_HEADING.search(heading.group(1)):
            end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(readme)
            )
            sections.append(readme[heading.end() : end])
    for index, paragraph in enumerate(paragraphs):
        if _REQUEST.search(paragraph):
            sections.append(paragraph)
            # A request ending with ':' commonly introduces a separate quote.
            if paragraph.rstrip().endswith(":") and index + 1 < len(paragraphs):
                sections.append(paragraphs[index + 1])

    return sections


def _record_citation(
    record: dict[str, str], source: str, paragraphs: list[str],
) -> ProjectCitation | None:
    title = record["title"]
    iri = _reference(record.get("doi", "") + " " + record.get("url", ""))
    if not iri:
        iri = _arxiv_eprint(record) or _title_reference(title, paragraphs)
    return ProjectCitation(iri=iri, title=title, source=source) if iri else None


def _readme_citations(readme: str) -> list[ProjectCitation]:
    paragraphs = re.split(r"\n\s*\n", readme)
    result: list[ProjectCitation] = []
    for section in _citation_sections(readme, paragraphs):
        records = _bibtex(section)
        result.extend(
            citation
            for record in records
            if (citation := _record_citation(record, "README", paragraphs))
        )
        # A non-paper BibTeX entry (e.g. @software) must not fall back to
        # untyped DOI scanning and turn into a requested scholarly reference.
        if not records and not re.search(r"@\w+\s*\{", section):
            result.extend(
                ProjectCitation(iri=iri, source="README")
                for paragraph in re.split(r"\n\s*\n", section)
                if (iri := _reference(paragraph))
            )
    return result


def _aux_citations(filename: str, content: str) -> list[ProjectCitation]:
    if filename.lower() == "citation.cff":
        parsed = parse_citation_cff(content) or {}
        preferred = parsed.get("preferred-citation") or {}
        if preferred.get("type") in {"article", "conference-paper"}:
            iri = _reference(
                str(preferred.get("doi", "")) + " " + str(preferred.get("url", "")),
            )
            if iri:
                return [
                    ProjectCitation(
                        iri=iri, title=preferred.get("title"), source=filename,
                    ),
                ]
    elif filename.lower() == "citation.bib":
        return [
            citation
            for record in _bibtex(content)
            if (citation := _record_citation(record, filename, []))
        ]
    return []


def project_citations(readme: Any, aux_files: Any) -> list[dict[str, Any]]:
    """Return deduplicated primary citation facts; never fetch linked pages."""
    result: list[ProjectCitation] = []
    if isinstance(aux_files, dict):
        for filename, content in aux_files.items():
            if not isinstance(filename, str) or not isinstance(content, str):
                continue
            result.extend(_aux_citations(filename, content))
    if isinstance(readme, str):
        result.extend(_readme_citations(readme))
    unique: dict[str, ProjectCitation] = {}
    for citation in result:
        unique.setdefault(citation.iri.casefold(), citation)
    return [citation.model_dump() for citation in unique.values()]
