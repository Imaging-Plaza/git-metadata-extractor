# Ontology v2 json response

The goal of this dev section is to design the API response to make it compatible with RDF semantics.


## Strategy A

This strategy is to split the task into small, highly focused tasks aiming to complete one class of the ontology. So at the end we will have one json per ontology shape with a hierarchy of identifies and uuid4.

### Results

#### a-001

**Summary**: Mock JSON files implementing hierarchical ID resolution for all ontology shapes.

##### Class Hierarchy & Connections

Each entity in the Open Pulse ontology is defined by an RDF class and validated by a SHACL shape. The table below shows how each class resolves its primary identifier through a priority-based hierarchy, falling back to less specific identifiers when preferred ones are unavailable. The "Connects To" column indicates which other shapes reference or are referenced by each class.

| Class | SHACL Shape | ID Hierarchy | Connects To |
|-------|-------------|--------------|-------------|
| `schema:Person` | `pulse:PersonShape` | `orcidIdentifier → infosciencePersonIdentifier → githubUsername → uuid` | MembershipShape, ContributionShape, RepositoryShape |
| `schema:SoftwareSourceCode` | `pulse:RepositoryShape` | `githubHandle → citation (doi?) → uuid` | PersonShape (author), OrganizationShape (owner) |
| `org:Organization` | `pulse:OrganizationShape` | `identifier (ror) → infoscienceOrganizationIdentifier → githubOrganizationHandle → uuid` | RepositoryShape (owns), OrganizationShape (units) |
| `org:Membership` | `pulse:MembershipShape` | `composite (personId_orgId) → uuid` | OrganizationShape |
| `pulse:Contribution` | `pulse:ContributionShape` | `composite (personId_repoId) → uuid` | RepositoryShape, PersonShape |
| `schema:ScholarlyArticle` | `pulse:ArticleShape` | `identifier (doi) → infoscienceArticleIdentifier → uuid` | PersonShape (author) |

##### Shape vs. Type

In RDF-based systems, it's important to distinguish between what an entity *is* and how it should be *validated*. The `type` field declares the semantic class of the entity (using standard vocabularies like schema.org or W3C ORG), while the `shacl` field references the SHACL shape that enforces property constraints and cardinalities specific to the Open Pulse ontology.

- **`type`**: The RDF class from the ontology (e.g., `schema:Person`, `org:Organization`)
- **`shacl`**: The SHACL shape that validates the instance (e.g., `pulse:PersonShape`)

The `type` defines *what* the entity is, while `shacl` defines the *validation rules* applied.

##### Identifier Strategy

To ensure interoperability and enable data linking across systems, each entity stores multiple identifiers but exposes a single canonical `id`. The identifier is resolved by checking a predefined hierarchy—preferring globally unique identifiers like ORCID or ROR over local ones. This approach ensures that entities can be reliably linked across different data sources while maintaining a consistent internal UUID for system operations.

All identifier keys use their full prefixed field names from the ontology (e.g., `pulse:orcid`, `schema:identifier`) to maintain consistency with RDF semantics.

```json
{
  "id": "<hierarchical-id>",
  "type": "<rdf-class>",
  "shacl": "<shacl-shape>",
  "identifiers": {
    "pulse:orcid": "<value-or-null>",
    "pulse:infosciencePersonIdentifier": "<value-or-null>",
    "pulse:githubUsername": "<value-or-null>",
    "uuid": "<fallback-uuid4>"
  },
  "idSource": "<prefixed-identifier-used-for-id>"
}
```

**Resolution Rules**:
1. The `id` field is set to the **first non-null** identifier in the hierarchy
2. `idSource` indicates which identifier type was used (with prefix, e.g., `pulse:orcid`)
3. `uuid` is always present as a fallback
4. Cross-references use hierarchical IDs, not internal UUIDs

##### Added Fields (non-ontology)

Beyond the properties defined in the ontology, each JSON object includes metadata fields to support the identifier resolution strategy. These fields are not part of the RDF data model but are essential for the API to communicate how identifiers were resolved and to enable clients to access alternative identifiers when needed.

```json
{
  "id": "...",           // Resolved hierarchical identifier
  "shacl": "...",        // SHACL shape reference for validation
  "identifiers": {},     // All available identifiers for this entity
  "idSource": "..."      // Which identifier was used for `id`
}
```

##### Files

The mock data is organized into separate JSON files, one per SHACL shape. Each file contains an array of instances representing realistic research software metadata scenarios, including EPFL researchers, GitHub repositories, and organizational affiliations.

| File | Entities | Description |
|------|----------|-------------|
| `pulse:PersonShape.json` | 5 | Researchers with ORCID/GitHub/email-only identifiers |
| `pulse:RepositoryShape.json` | 4 | Software repositories owned by persons/orgs (incl. fork) |
| `pulse:OrganizationShape.json` | 5 | Universities, research institutions, projects |
| `pulse:MembershipShape.json` | 6 | Person-organization affiliations |
| `pulse:ContributionShape.json` | 9 | Person-repository contribution records |
| `pulse:ArticleShape.json` | 4 | Scholarly articles with DOI/Infoscience IDs |

##### Validation Scripts

Two validation scripts are provided in the `scripts/` folder to ensure data quality and consistency.

###### Schema Validation

Validates all JSON files against their corresponding JSON schemas (both strict and agent versions).

```bash
cd dev/ontology-v2-json-response
python scripts/validate_schemas.py
```

**Output** (in `a-001/test/`):
- `validation_results.json` - Combined validation summary
- `validation_strict.json` - Detailed strict schema results
- `validation_agent.json` - Detailed agent schema results

**What it checks:**
- Field types and formats (UUIDs, dates, URLs, DOIs)
- Required fields per SHACL shape constraints
- Enum values for controlled vocabularies
- Pattern matching for identifiers

###### JSON-LD Build & Consistency Check

Builds a combined JSON-LD document and validates internal cross-reference consistency.

```bash
cd dev/ontology-v2-json-response
python scripts/build_jsonld.py
```

**Output** (in `a-001/test/`):
- `jsonld_output.json` - Combined JSON-LD document with `@context`
- `consistency_results.json` - Cross-reference validation details

**What it checks:**
- All cross-references resolve to existing entities
- Bidirectional ownership references are consistent (e.g., `pulse:owns` ↔ `pulse:ownedBy`)
- Membership composite IDs match referenced persons
- No orphaned references

###### JSON-LD Graph Visualization

Generates an interactive HTML visualization of the JSON-LD graph using Sigma.js and Graphology.

```bash
cd dev/ontology-v2-json-response
python scripts/visualize_jsonld.py
```

**Output** (in `a-001/test/`):
- `visualization.html` - Standalone interactive graph visualization

**Features:**

| Feature | Description |
|---------|-------------|
| **Multiple Layouts** | Force-directed (animated), Circular, Radial, and Grid layouts |
| **Force Animation** | Custom physics simulation with node repulsion, edge attraction, and gravity |
| **Drag & Drop** | Toggle to manually reposition nodes in the graph |
| **Node Labels** | All nodes display labels with type badges above the node name |
| **Property Tooltips** | Hover over nodes to see all properties in a tooltip |
| **Entity Type Filters** | Toggle visibility of different entity types (Person, Repository, etc.) |
| **Search** | Search nodes by name or identifier |
| **Node Details Panel** | Click a node to see full details in the sidebar |
| **Data Tables** | Collapsible panel with tabbed tables for each entity type |
| **Collapsible Sidebar** | Toggle button to collapse/expand the sidebar for more graph space |
| **Collapsible Sections** | Each sidebar section (filters, legend, details) can be collapsed |
| **Zoom Controls** | Zoom in/out buttons plus smooth mouse wheel zoom |
| **Relationship Legend** | Color-coded legend for all relationship types |

**Graph Elements:**
- **Nodes**: Colored by entity type (Person=blue, Repository=green, Organization=orange, etc.)
- **Edges**: Colored by relationship type with directional arrows
- **Labels**: Type badge + node name displayed for all nodes

**Usage:**
1. Open the generated `visualization.html` in a browser
2. Select a layout from the dropdown (Force Atlas animates automatically)
3. Use filters to show/hide entity types
4. Hover nodes for property details, click for full details
5. Toggle drag mode to manually arrange nodes
6. Use the sidebar toggle (◀) to maximize graph space

**Serve locally:**

```bash
cd dev/ontology-v2-json-response/a-001/test
python -m http.server 8000
```

Then open http://localhost:8000/visualization.html in your browser.


---

## Strategy B

This strategy

3 Different agents paths.
1. Repository -> User -> Person  -> Organization
2. User -> Person -> RepositoryGH
3. Organization -> User -> Repository

### Results

#### 1. Repository -> User -> Person -> Organization

This agent path starts from a repository link and extract github users, different people from infoscience and finally it look for organizations.

Under development.
