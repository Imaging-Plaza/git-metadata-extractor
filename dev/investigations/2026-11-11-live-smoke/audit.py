"""Revalidate saved live outputs and summarize provenance without network calls."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from git_metadata_extractor.api_models import V2ExtractResponse
from git_metadata_extractor.pipeline.settings import jsonld_to_graph
from git_metadata_extractor.validation import SHACLValidator, load_ontology_shapes_graph

root = Path('dev/investigations/2026-11-11-live-smoke')
results = []
for path in sorted(root.glob('*-response.json')):
    response = json.loads(path.read_text())
    V2ExtractResponse.model_validate(response)
    output = response['output']
    graph = jsonld_to_graph(output)
    validation = SHACLValidator().validate_graph(graph, load_ontology_shapes_graph())
    provenance = (response.get('provenance') or {}).get('@graph', [])
    queries = [n for n in provenance if 'gme:Retrieval' in n.get('@type', [])]
    results.append({
        'file': path.name, 'response_schema_valid': True,
        'entity_types': dict(Counter(str(n.get('@type')) for n in output['@graph'])),
        'shacl_conforms': validation.conforms, 'violations': validation.violations,
        'provenance_nodes': len(provenance),
        'provenance_types': dict(Counter(str(n.get('@type')) for n in provenance)),
        'query_count': len(queries),
    })
root.joinpath('output-audit.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
