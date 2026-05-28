"""Test the aux-file forwarding in `_build_repo_context_summary`.

The function is a pure summariser over the `gathered_context` slice the
LLM refiners receive. We pin the contract:

  - When CITATION.cff / AUTHORS / CONTRIBUTING.md / publiccode.yml are
    present in `aux_files`, their (capped) content is forwarded to the
    LLM via the `aux_files` key in the summary.
  - Case-insensitive matching against the on-disk filename.
  - Alternate spellings (publiccode.yaml, CONTRIBUTION.md) are also
    recognised — they match the same slug.
  - Empty / missing slice → no `aux_files` key emitted (caller checks).
  - Long file contents are capped at `AUX_FILE_CONTEXT_MAX_CHARS`.
"""

from __future__ import annotations

from src.v2.pipeline.stages.refine_with_llm import (
    AUX_FILE_CONTEXT_MAX_CHARS,
    _build_repo_context_summary,
)


def _wrap_context(aux_files: dict[str, str] | None) -> dict[str, dict[str, object]]:
    return {
        "repository": {
            "metadata": {"name": "Hello-World", "full_name": "octocat/Hello-World"},
            "readme_content": "",
            "aux_files": aux_files,
        },
    }


def test_summary_forwards_all_aux_files_when_present():
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({
            "CITATION.cff": "cff-version: 1.2.0\nauthors:\n - given-names: Octo",
            "AUTHORS.md": "- Octo Cat\n- Alice",
            "CONTRIBUTING.md": "Open a PR.",
            "publiccode.yml": "publiccodeYmlVersion: '0.4'",
            "SECURITY.md": "Report vulnerabilities to security@example.com.",
        }),
    )
    aux = summary["aux_files"]
    assert set(aux.keys()) == {
        "citation_cff", "authors", "contributing", "publiccode", "security",
    }
    assert aux["citation_cff"].startswith("cff-version:")
    assert aux["authors"].startswith("- Octo")
    assert aux["contributing"] == "Open a PR."
    assert aux["publiccode"].startswith("publiccodeYmlVersion:")
    assert aux["security"].startswith("Report vulnerabilities")


def test_summary_case_insensitive_match():
    """Real GitHub responses preserve the file's casing. The lookup
    should still match `Citation.CFF` and `Authors.RST` etc."""
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({
            "Citation.CFF": "cff-version: 1.2.0",
            "Authors.RST": "Octo Cat",
        }),
    )
    aux = summary["aux_files"]
    assert "citation_cff" in aux
    assert "authors" in aux


def test_summary_recognises_alternate_publiccode_extension():
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({"publiccode.yaml": "publiccodeYmlVersion: '0.4'"}),
    )
    assert summary["aux_files"]["publiccode"].startswith("publiccodeYmlVersion:")


def test_summary_recognises_alternate_contribution_spelling():
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({"CONTRIBUTION.md": "be nice"}),
    )
    assert summary["aux_files"]["contributing"] == "be nice"


def test_summary_omits_aux_files_key_when_no_relevant_files():
    """Just a README and nothing else — the `aux_files` key shouldn't
    pollute the summary."""
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({"README.md": "Hello"}),
    )
    assert "aux_files" not in summary


def test_summary_handles_missing_aux_files_slice():
    """`aux_files` may be absent or None — the summariser must not raise."""
    summary = _build_repo_context_summary(
        gathered_context={"repository": {"metadata": {}, "readme_content": ""}},
    )
    assert "aux_files" not in summary


def test_summary_caps_long_aux_file_contents():
    big = "x" * (AUX_FILE_CONTEXT_MAX_CHARS + 1_000)
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({"CITATION.cff": big}),
    )
    assert len(summary["aux_files"]["citation_cff"]) == AUX_FILE_CONTEXT_MAX_CHARS


def test_summary_includes_parsed_publiccode_payload():
    """The LLM context summary should carry the *parsed* publiccode
    payload alongside the raw excerpt — the LLM gets typed fields
    (license, softwareType, contacts) without having to reparse YAML."""
    pc = (
        "publiccodeYmlVersion: '0.4.0'\n"
        "name: foo\n"
        "softwareType: standalone/web\n"
        "legal:\n  license: AGPL-3.0-or-later\n"
    )
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({"publiccode.yml": pc}),
    )
    assert summary["publiccode"]["name"] == "foo"
    assert summary["publiccode"]["softwareType"] == "standalone/web"
    assert summary["publiccode"]["legal"] == {"license": "AGPL-3.0-or-later"}
    # The raw excerpt is still there for verbatim quoting.
    assert "publiccode" in summary["aux_files"]


def test_summary_publiccode_payload_handled_for_yaml_extension():
    pc = "publiccodeYmlVersion: '0.4.0'\nname: bar\n"
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({"publiccode.yaml": pc}),
    )
    assert summary["publiccode"]["name"] == "bar"


def test_summary_omits_publiccode_key_when_payload_unparseable():
    """A malformed publiccode.yml leaves the raw excerpt in
    `aux_files` (caller may still want to show it) but does not pin a
    bogus `publiccode` key on the summary."""
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({"publiccode.yml": "foo: : not yaml"}),
    )
    assert "publiccode" not in summary
    # Raw excerpt still present.
    assert "publiccode" in summary["aux_files"]


def test_summary_picks_first_match_per_slug():
    """When both `authors` and `authors.md` are present, the first
    match in the candidate order wins — deterministic regardless of
    Python dict insertion order."""
    summary = _build_repo_context_summary(
        gathered_context=_wrap_context({
            "authors.md": "the md version",
            "AUTHORS": "the plain version",
        }),
    )
    # `authors` (no extension) comes first in the candidate tuple, so
    # it wins regardless of which file the dict iterates first.
    assert summary["aux_files"]["authors"] == "the plain version"
