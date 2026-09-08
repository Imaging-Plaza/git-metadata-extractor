from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from rdflib import Graph

ONTOLOGY_FILENAME = "open-pulse-ontology-v2.1.2.ttl"

# Runtime asset, shipped inside the package (the Dockerfile copies `src/`,
# so this resolves to /app/git_metadata_extractor/validation/<file> in the container).
_PACKAGED_PATH = Path(__file__).resolve().parent / ONTOLOGY_FILENAME

# Fallback for local source checkouts that still keep the ontology bundle
# under `dev/` (kept so a regenerated bundle there is honoured). NOT shipped
# in the image — `dev/` is excluded from the build context.
_DEV_FALLBACK_PATH = (
    Path(__file__).resolve().parents[2]
    / "dev"
    / "ontology-v2-json-response"
    / ONTOLOGY_FILENAME
)

# Env override so an operator can point at a newer ontology revision without
# a rebuild (e.g. GME_ONTOLOGY_TTL=/etc/open-pulse/ontology.ttl).
_ENV_VAR = "GME_ONTOLOGY_TTL"


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env = os.getenv(_ENV_VAR)
    if env and env.strip():
        candidates.append(Path(env.strip()).expanduser())
    candidates.append(_PACKAGED_PATH)
    candidates.append(_DEV_FALLBACK_PATH)
    return candidates


def ontology_ttl_path() -> Path:
    """Resolve the open-pulse ontology TTL, preferring (in order): the
    ``GME_ONTOLOGY_TTL`` env override, the packaged copy shipped inside
    ``git_metadata_extractor/validation/``, then a ``dev/`` source-checkout fallback.

    Returns the first existing candidate, or the packaged path when none
    exist (so callers get a stable, informative path in the error).
    """
    for candidate in _candidate_paths():
        if candidate.exists():
            return candidate
    return _PACKAGED_PATH


#: The v3 canonical layer, from the vendored ontology submodule. Four files,
#: not one: `sh:class` on every enumerated property needs the enumeration
#: *instance* triples to resolve, and the raw enumerations file extends
#: `pulse:PublicationTypeEnumeration` that the canonical shapes reference.
#: Loading only the shapes makes every platform, discipline and repository-type
#: value a violation.
_CANONICAL_FILES = (
    "ontology-shapes-canonical.ttl",
    "ontology-definitions-canonical.ttl",
    "ontology-enumerations-canonical.ttl",
    "ontology-enumerations-raw.ttl",
)

_SUBMODULE_ONTOLOGY_DIR = (
    Path(__file__).resolve().parents[2] / "vendor" / "open-pulse-ontology" / "src" / "ontology"
)


def canonical_shapes_available() -> bool:
    """Whether the vendored v3 canonical layer is present and prepared."""
    return all((_SUBMODULE_ONTOLOGY_DIR / name).exists() for name in _CANONICAL_FILES)


@lru_cache(maxsize=1)
def load_canonical_shapes_graph() -> Graph:
    """The v3 canonical shapes, for validating the projected output.

    Separate from `load_ontology_shapes_graph`, which serves the v2.1.2 bundle
    and still describes the intermediate `build_jsonld_output` produces. Once
    the canonical projection runs, the graph the caller receives is v3 and
    validating it against v2.1.2 shapes reports nothing useful — every profile
    node is an unknown class and every renamed property an unknown term.
    """
    graph = Graph()
    for name in _CANONICAL_FILES:
        graph.parse(str(_SUBMODULE_ONTOLOGY_DIR / name), format="turtle")
    return graph


@lru_cache(maxsize=1)
def load_ontology_shapes_graph() -> Graph:
    for candidate in _candidate_paths():
        if candidate.exists():
            graph = Graph()
            graph.parse(candidate, format="turtle")
            return graph
    attempted = ", ".join(str(c) for c in _candidate_paths())
    message = (
        f"open-pulse ontology TTL not found. Tried: {attempted}. "
        f"Ship it inside the package or set {_ENV_VAR}."
    )
    raise FileNotFoundError(message)
