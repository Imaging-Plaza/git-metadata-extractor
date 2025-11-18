# JSON ↔ JSON-LD Conversion Guide

## Quick Start

### Installation

Make sure you're in the project directory and have the dependencies installed:

```bash
cd /home/rmfranken/git-metadata-extractor
# If using uv (recommended)
uv sync
# Or with pip
pip install -e .
```

### Basic Usage

#### Convert JSON to JSON-LD

```bash
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld
```

**With base URL (recommended):**
```bash
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld \
    --base-url https://github.com/your-org/your-repo
```

#### Convert JSON-LD to JSON

```bash
python scripts/convert_json_jsonld.py to-json input.jsonld output.json
```

## Detailed Examples

### Example 1: Convert Repository Metadata to JSON-LD

**Input file** (`my_repo.json`):
```json
{
  "name": "My Research Software",
  "description": "A tool for scientific computing",
  "codeRepository": ["https://github.com/example/my-repo"],
  "license": "https://spdx.org/licenses/MIT",
  "author": [
    {
      "type": "Person",
      "name": "Jane Doe",
      "orcid": "0000-0002-1234-5678",
      "affiliations": ["EPFL"]
    }
  ],
  "repositoryType": "software",
  "repositoryTypeJustification": ["Contains source code and documentation"],
  "discipline": ["Biology", "Computer Engineering"],
  "disciplineJustification": ["Computational biology tools", "Software engineering"]
}
```

**Command:**
```bash
python scripts/convert_json_jsonld.py to-jsonld my_repo.json my_repo.jsonld \
    --base-url https://github.com/example/my-repo
```

**Output** (`my_repo.jsonld`):
```json
{
  "@context": {
    "schema": "http://schema.org/",
    "sd": "https://w3id.org/okn/o/sd#",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "md4i": "http://w3id.org/nfdi4ing/metadata4ing#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dcterms": "http://purl.org/dc/terms/",
    "wd": "http://www.wikidata.org/entity/"
  },
  "@graph": [
    {
      "@id": "https://github.com/example/my-repo",
      "@type": "schema:SoftwareSourceCode",
      "schema:name": "My Research Software",
      "schema:description": "A tool for scientific computing",
      "schema:codeRepository": [
        {"@id": "https://github.com/example/my-repo"}
      ],
      "schema:license": "https://spdx.org/licenses/MIT",
      "schema:author": [
        {
          "@type": "schema:Person",
          "schema:name": "Jane Doe",
          "md4i:orcidId": {"@id": "https://orcid.org/0000-0002-1234-5678"},
          "schema:affiliation": ["EPFL"]
        }
      ],
      "pulse:repositoryType": "pulse:Software",
      "pulse:justification": [
        "Contains source code and documentation",
        "Computational biology tools",
        "Software engineering"
      ],
      "pulse:discipline": ["Biology", "Computer Engineering"]
    }
  ]
}
```

### Example 2: Convert JSON-LD Back to JSON

```bash
python scripts/convert_json_jsonld.py to-json my_repo.jsonld my_repo_restored.json
```

This will convert the JSON-LD back to the Pydantic JSON format.

## Using in Python Code

You can also use the conversion functions directly in Python:

### Convert to JSON-LD

```python
from src.data_models.repository import SoftwareSourceCode
from src.data_models.models import Person, RepositoryType
from src.data_models.conversion import convert_pydantic_to_jsonld
import json

# Create a Pydantic model
repo = SoftwareSourceCode(
    name="My Research Software",
    description="A tool for scientific computing",
    codeRepository=["https://github.com/example/my-repo"],
    license="https://spdx.org/licenses/MIT",
    author=[
        Person(
            name="Jane Doe",
            orcid="0000-0002-1234-5678",
            affiliations=["EPFL"]
        )
    ],
    repositoryType=RepositoryType.SOFTWARE,
    repositoryTypeJustification=["Contains source code"]
)

# Convert to JSON-LD
jsonld = convert_pydantic_to_jsonld(
    repo,
    base_url="https://github.com/example/my-repo"
)

# Save to file
with open('output.jsonld', 'w') as f:
    json.dump(jsonld, f, indent=2)
```

### Convert from JSON-LD

```python
from src.data_models.conversion import convert_jsonld_to_pydantic
import json

# Load JSON-LD
with open('input.jsonld', 'r') as f:
    jsonld_data = json.load(f)

# Extract graph
graph = jsonld_data.get("@graph", [jsonld_data])

# Convert to Pydantic
software = convert_jsonld_to_pydantic(graph)

# Access properties
print(f"Name: {software.name}")
print(f"Authors: {[a.name for a in software.author]}")

# Convert back to dict/JSON
data = software.model_dump(exclude_none=True)
```

## Working with Existing Files

### Convert Your Output File

If you already have an output file from the metadata extractor:

```bash
python scripts/convert_json_jsonld.py to-jsonld \
    src/files/output_file.json \
    src/files/output_file.jsonld \
    --base-url https://github.com/your-org/your-repo
```

### Batch Conversion

Convert multiple files:

```bash
# Create a simple bash script
for json_file in data/*.json; do
    base_name=$(basename "$json_file" .json)
    python scripts/convert_json_jsonld.py to-jsonld \
        "$json_file" \
        "data/${base_name}.jsonld"
done
```

Or in Python:

```python
from pathlib import Path
from src.data_models.conversion import convert_pydantic_to_jsonld
from src.data_models.repository import SoftwareSourceCode
import json

input_dir = Path("data/json")
output_dir = Path("data/jsonld")
output_dir.mkdir(exist_ok=True)

for json_file in input_dir.glob("*.json"):
    print(f"Converting {json_file.name}...")

    # Load and convert
    with open(json_file) as f:
        data = json.load(f)

    repo = SoftwareSourceCode(**data)
    jsonld = convert_pydantic_to_jsonld(repo)

    # Save
    output_file = output_dir / f"{json_file.stem}.jsonld"
    with open(output_file, 'w') as f:
        json.dump(jsonld, f, indent=2)

    print(f"  → {output_file}")
```

## Command Reference

### to-jsonld Command

Convert Pydantic JSON to JSON-LD format.

**Syntax:**
```bash
python scripts/convert_json_jsonld.py to-jsonld INPUT OUTPUT [--base-url URL]
```

**Arguments:**
- `INPUT`: Path to input JSON file (Pydantic format)
- `OUTPUT`: Path to output JSON-LD file
- `--base-url`: (Optional) Base URL for @id generation (typically the repository URL)

**Examples:**
```bash
# Basic conversion
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld

# With base URL
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld \
    --base-url https://github.com/user/repo

# Using absolute paths
python scripts/convert_json_jsonld.py to-jsonld \
    /path/to/input.json \
    /path/to/output.jsonld
```

### to-json Command

Convert JSON-LD to Pydantic JSON format.

**Syntax:**
```bash
python scripts/convert_json_jsonld.py to-json INPUT OUTPUT
```

**Arguments:**
- `INPUT`: Path to input JSON-LD file
- `OUTPUT`: Path to output JSON file (Pydantic format)

**Examples:**
```bash
# Basic conversion
python scripts/convert_json_jsonld.py to-json input.jsonld output.json

# Using absolute paths
python scripts/convert_json_jsonld.py to-json \
    /path/to/input.jsonld \
    /path/to/output.json
```

## Validation

### Validate JSON-LD Output

You can validate your JSON-LD output using online tools or libraries:

**Online Validators:**
- [JSON-LD Playground](https://json-ld.org/playground/)
- [RDF Translator](https://www.easyrdf.org/converter)

**Using Python:**
```python
from pyld import jsonld
import json

# Load your JSON-LD
with open('output.jsonld', 'r') as f:
    doc = json.load(f)

# Expand to see full URIs
expanded = jsonld.expand(doc)
print(json.dumps(expanded, indent=2))

# Convert to N-Quads (RDF)
nquads = jsonld.to_rdf(doc, {'format': 'application/n-quads'})
print(nquads)
```

### SHACL Validation

To validate against PULSE ontology SHACL shapes, you'll need a SHACL validator:

```python
from pyshacl import validate
import json

# Load your JSON-LD
with open('output.jsonld', 'r') as f:
    data_graph = f.read()

# Load PULSE SHACL shapes (you'll need the shapes file)
with open('pulse_shapes.ttl', 'r') as f:
    shacl_graph = f.read()

# Validate
conforms, results_graph, results_text = validate(
    data_graph=data_graph,
    data_graph_format='json-ld',
    shacl_graph=shacl_graph,
    shacl_graph_format='turtle'
)

print(f"Conforms: {conforms}")
if not conforms:
    print(results_text)
```

## Troubleshooting

### Common Issues

**Issue: "Module not found" error**
```bash
# Solution: Install dependencies
pip install -e .
# Or with uv
uv sync
```

**Issue: "No SoftwareSourceCode entity found"**
```bash
# Solution: Check your JSON-LD structure has @type: schema:SoftwareSourceCode
# and a @graph array
```

**Issue: "Invalid ORCID format"**
```bash
# Solution: Use format "0000-0002-1234-5678" or "https://orcid.org/0000-0002-1234-5678"
```

**Issue: Validation errors**
```bash
# Solution: Check required fields:
# - name (required)
# - description (required)
# - author (required, at least one)
# - repositoryType (required)
# - repositoryTypeJustification (required)
```

### Getting Help

```bash
# Show help message
python scripts/convert_json_jsonld.py --help

# Show detailed examples
python scripts/convert_json_jsonld.py to-jsonld --help
```

## Advanced Usage

### Custom Context

If you need to customize the JSON-LD context, modify `src/data_models/conversion.py`:

```python
# In convert_pydantic_to_jsonld function
context = {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    # Add your custom prefixes here
    "custom": "https://your-domain.com/ontology#",
}
```

### Converting Partial Models

You can convert individual models (Person, Organization, etc.):

```python
from src.data_models.models import Person
from src.data_models.conversion import convert_pydantic_to_jsonld

person = Person(
    name="Jane Doe",
    orcid="0000-0002-1234-5678"
)

jsonld = convert_pydantic_to_jsonld(person)
```

## Integration with API

To convert API responses:

```python
from src.api import extract_metadata
from src.data_models.conversion import convert_pydantic_to_jsonld
import json

# Extract metadata using API
result = extract_metadata(
    repo_url="https://github.com/user/repo",
    use_cache=True
)

# Convert to JSON-LD
jsonld = convert_pydantic_to_jsonld(
    result['data'],
    base_url="https://github.com/user/repo"
)

# Save
with open('output.jsonld', 'w') as f:
    json.dump(jsonld, f, indent=2)
```

## See Also

- [Full Mapping Documentation](./PYDANTIC_JSONLD_MAPPING.md)
- [Quick Reference Guide](./JSONLD_CONVERSION_SUMMARY.md)
- [PULSE Ontology](https://open-pulse.epfl.ch/ontology#)
- [JSON-LD Specification](https://www.w3.org/TR/json-ld11/)
