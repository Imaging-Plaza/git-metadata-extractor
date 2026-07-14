from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rdflib import Graph

pyshacl_validate: Any | None
try:
    from pyshacl import validate as _pyshacl_validate  # type: ignore[import-untyped]
    pyshacl_validate = _pyshacl_validate
except ModuleNotFoundError:  # pragma: no cover - runtime dependency
    pyshacl_validate = None

SHACL_RESULTS_QUERY = """
PREFIX sh: <http://www.w3.org/ns/shacl#>

SELECT ?focusNode ?resultPath ?value ?message ?severity ?sourceShape
WHERE {
    ?result a sh:ValidationResult ;
            sh:focusNode ?focusNode ;
            sh:resultSeverity ?severity .
    OPTIONAL { ?result sh:resultPath ?resultPath }
    OPTIONAL { ?result sh:value ?value }
    OPTIONAL { ?result sh:resultMessage ?message }
    OPTIONAL { ?result sh:sourceShape ?sourceShape }
}
"""


@dataclass(slots=True)
class SHACLValidationResult:
    conforms: bool
    violations: list[dict[str, str | None]] = field(default_factory=list)
    warnings: list[dict[str, str | None]] = field(default_factory=list)


class SHACLRuntimeUnavailableError(RuntimeError):
    """Raised when SHACL runtime dependency is unavailable."""


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


class SHACLValidator:
    """Run SHACL validation against an RDF graph."""

    def validate_graph(
        self,
        graph: Graph,
        shapes_graph: Graph,
    ) -> SHACLValidationResult:
        if pyshacl_validate is None:
            message = "pyshacl is not installed; skipping SHACL validation gate"
            raise SHACLRuntimeUnavailableError(message)

        # Include ontology triples in the data graph so sh:class checks can resolve
        # enum instances defined in the ontology itself.
        validation_graph = graph + shapes_graph

        conforms, results_graph, _ = pyshacl_validate(
            validation_graph,
            shacl_graph=shapes_graph,
            ont_graph=shapes_graph,
            inference="rdfs",
            abort_on_first=False,
            allow_infos=True,
            allow_warnings=True,
            meta_shacl=False,
            advanced=True,
            js=False,
            debug=False,
        )

        violations: list[dict[str, str | None]] = []
        warnings: list[dict[str, str | None]] = []
        for row in results_graph.query(SHACL_RESULTS_QUERY):
            issue = {
                "focusNode": _as_optional_text(row.focusNode),
                "path": _as_optional_text(row.resultPath),
                "value": _as_optional_text(row.value),
                "message": _as_optional_text(row.message),
                "sourceShape": _as_optional_text(row.sourceShape),
            }
            severity = _as_optional_text(row.severity) or ""
            if "Violation" in severity:
                violations.append(issue)
                continue
            if "Warning" in severity:
                warnings.append(issue)

        return SHACLValidationResult(
            conforms=bool(conforms),
            violations=violations,
            warnings=warnings,
        )
