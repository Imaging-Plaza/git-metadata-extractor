"""Store-side unification: substrate -> `graph:canonical`.

`PROVENANCE_ARCHITECTURE.md` phase 4. Split the way that document specifies —
a type-agnostic layer that does the same thing for every class, and a thin
per-type resolver that is the only part which varies:

    policy.py     per-type match keys + per-property dispositions (varies)
    cluster.py    union-find over those keys                      (agnostic)
    merge.py      union / select / drop, and why                  (agnostic)
    remap.py      rewrite refs + composite ids after a rename     (agnostic)
    provenance.py the decisions, as RDF-star in graph:prov        (I/O)
    runner.py     read the store, decide, write graph:canonical   (I/O)

**Why this is store-side at all.** Each `/v2/extract` is one repository's slice
and is cached per URL, so it cannot see one person across two repositories.
Unification needs the accumulated substrate, which only the store has — that is
the whole argument for moving it here, and `pipeline/stages/reconciliation.py`
keeps doing the intra-request version unchanged.
"""

from git_metadata_extractor.unify.cluster import (
    Cluster,
    Record,
    cluster_records,
)
from git_metadata_extractor.unify.merge import (
    MergedEntity,
    Selection,
    merge_cluster,
)
from git_metadata_extractor.unify.policy import (
    Disposition,
    MergePolicy,
    TypeResolver,
    default_policy,
)
from git_metadata_extractor.unify.provenance import (
    PROV_GRAPH,
    Annotation,
    ProvenanceReport,
    annotations_for,
    same_as_edges,
    write_provenance,
)
from git_metadata_extractor.unify.runner import (
    CANONICAL_GRAPH,
    UnifyReport,
    canonical_document,
    unify_records,
    unify_store,
)

__all__ = [
    "CANONICAL_GRAPH",
    "PROV_GRAPH",
    "Annotation",
    "Cluster",
    "Disposition",
    "MergePolicy",
    "MergedEntity",
    "ProvenanceReport",
    "Record",
    "Selection",
    "TypeResolver",
    "UnifyReport",
    "annotations_for",
    "canonical_document",
    "cluster_records",
    "default_policy",
    "merge_cluster",
    "same_as_edges",
    "unify_records",
    "unify_store",
    "write_provenance",
]
