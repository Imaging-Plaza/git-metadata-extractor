#!/usr/bin/env python3
"""
Generate an interactive HTML visualization of JSON-LD graph data using Sigma.js.

This script reads the JSON-LD output produced by build_jsonld.py and, optionally,
the validation results from validate_schemas.py, then generates a self-contained
HTML file that visualises the Open Pulse ontology graph in the browser.

Inputs (read from a-001/test/):
    - jsonld_output.json        — JSON-LD graph built by build_jsonld.py  (required)
    - validation_results.json   — Schema validation report from
                                  validate_schemas.py                     (optional)

Output:
    - a-001/test/visualization.html — Standalone interactive visualisation

Features:
    Layout
        - Four switchable layouts: Force Atlas (animated force-directed),
          Circular, Radial (hierarchical rings by entity type), and Grid.
        - Nodes are sized proportionally to their connection count.

    Interaction
        - Search bar with instant filtering across node labels and IDs.
        - Entity-type checkboxes to show/hide node categories.
        - Click a node to highlight it and its direct neighbours; click the
          stage background to reset.
        - Collapsible JSON-LD editor panel (collapsed by default) between the
          sidebar and graph canvas.
        - "Edit in JSON-LD" action in node details to jump to and highlight
          node-related source lines (node block + references).
        - Apply edited JSON-LD to rebuild the graph in-browser and download the
          edited JSON-LD text.
        - Hover tooltips with truncated property previews.
        - Drag mode (toggle) to reposition individual nodes.
        - Zoom, fit-to-view, and full reset controls.

    Validation overlay (when validation_results.json is present)
        - Per-node badge: green ✓ (all schemas pass), red ✗ (failure),
          or grey ? (no validation data).
        - Tooltip and sidebar detail panels show Strict / Agent pass/fail
          status with full error messages.
        - Dedicated "Validation" tab in the bottom data-panel table listing
          every entity with its schema, status, and error count.
        - Stats bar displays the overall valid/total count colour-coded
          green or red.

    Data tables
        - Bottom panel with per-type tabs showing entity properties in a
          sortable, scrollable table. Clicking a row selects and zooms to
          the corresponding node.

    Edge rendering
        - Bidirectional relationships are rendered with curvature so both
          directions remain visible.
        - Edge legend in the sidebar maps colours to relationship types.

Dependencies (loaded from CDN at runtime inside the HTML):
    - graphology  0.25.4
    - sigma.js    2.4.0

Usage:
    # Activate the project virtual environment first
    source .venv/bin/activate

    # Generate the JSON-LD (prerequisite)
    python3 scripts/build_jsonld.py

    # Optionally generate validation results
    python3 scripts/validate_schemas.py

    # Produce the visualisation
    python3 scripts/visualize_jsonld.py
"""

import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
TEST_OUTPUT_DIR = BASE_DIR / "a-001" / "test"
JSONLD_FILE = TEST_OUTPUT_DIR / "jsonld_output.json"
VALIDATION_FILE = TEST_OUTPUT_DIR / "validation_results.json"
OUTPUT_FILE = TEST_OUTPUT_DIR / "visualization.html"

# Entity type configuration
ENTITY_CONFIG = {
    "schema:Person": {
        "color": "#3b82f6",  # Blue
        "label": "Person",
        "ring": 1,  # Inner ring after center
        "size_base": 12,
    },
    "schema:SoftwareSourceCode": {
        "color": "#22c55e",  # Green
        "label": "Repository",
        "ring": 2,
        "size_base": 14,
    },
    "org:Organization": {
        "color": "#f97316",  # Orange
        "label": "Organization",
        "ring": 0,  # Center
        "size_base": 16,
    },
    "org:Membership": {
        "color": "#a855f7",  # Purple
        "label": "Membership",
        "ring": 3,
        "size_base": 8,
    },
    "pulse:Contribution": {
        "color": "#06b6d4",  # Cyan
        "label": "Contribution",
        "ring": 3,
        "size_base": 8,
    },
    "schema:ScholarlyArticle": {
        "color": "#ef4444",  # Red
        "label": "Article",
        "ring": 2,
        "size_base": 10,
    },
}

# Edge relationship configuration
EDGE_CONFIG = {
    "org:hasMembership": {"label": "has membership", "color": "#a855f7"},
    "pulse:hasContribution": {"label": "has contribution", "color": "#06b6d4"},
    "pulse:owns": {"label": "owns", "color": "#22c55e"},
    "pulse:ownedBy": {"label": "owned by", "color": "#22c55e"},
    "schema:author": {"label": "author", "color": "#3b82f6"},
    "pulse:contributionTo": {"label": "contributes to", "color": "#06b6d4"},
    "org:organization": {"label": "organization", "color": "#f97316"},
    "org:hasUnit": {"label": "has unit", "color": "#f97316"},
    "org:unitOf": {"label": "unit of", "color": "#f97316"},
    "pulse:isForkOf": {"label": "fork of", "color": "#94a3b8"},
    "schema:sourceOrganization": {"label": "source org", "color": "#f97316"},
}

# Cross-reference fields to extract edges from
CROSS_REF_FIELDS = [
    "org:hasMembership",
    "pulse:hasContribution",
    "pulse:owns",
    "pulse:ownedBy",
    "schema:author",
    "pulse:contributionTo",
    "org:organization",
    "org:hasUnit",
    "org:unitOf",
    "pulse:isForkOf",
    "schema:sourceOrganization",
]


def load_jsonld() -> tuple[dict, str]:
    """Load JSON-LD file and return both parsed data and original raw text."""
    raw_text = JSONLD_FILE.read_text(encoding="utf-8")
    return json.loads(raw_text), raw_text


def load_validation() -> dict[str, dict]:
    """
    Load validation results and build a lookup by instance ID.

    Returns a dict mapping instance_id -> {
        "strict": bool, "agent": bool,
        "strict_errors": list, "agent_errors": list,
        "shape": str
    }
    """
    if not VALIDATION_FILE.exists():
        return {}

    with open(VALIDATION_FILE, encoding="utf-8") as f:
        data = json.load(f)

    lookup: dict[str, dict] = {}
    for mode in ("strict", "agent"):
        mode_data = data.get(mode, {})
        for shape_name, shape_result in mode_data.items():
            for instance in shape_result.get("instances", []):
                inst_id = instance.get("id", "")
                if inst_id not in lookup:
                    lookup[inst_id] = {
                        "strict": True,
                        "agent": True,
                        "strict_errors": [],
                        "agent_errors": [],
                        "shape": shape_name,
                    }
                key_valid = f"{mode}"
                key_errors = f"{mode}_errors"
                lookup[inst_id][key_valid] = instance.get("valid", True)
                lookup[inst_id][key_errors] = instance.get("errors", [])

    return lookup


def build_id_index(graph: list[dict]) -> dict[str, dict]:
    """
    Build an index mapping all possible identifiers to their nodes.
    This handles the hierarchical ID resolution strategy.
    """
    index = {}

    for node in graph:
        node_id = node.get("@id", "")
        node_type = node.get("@type", "")

        # Primary ID (from @id)
        index[node_id] = node

        # Extract the short ID from @id (e.g., "pulse:person/0000-0001-2345-6789" -> "0000-0001-2345-6789")
        if "/" in node_id:
            short_id = node_id.split("/", 1)[-1]
            if short_id not in index:
                index[short_id] = node

        # Index by various identifier fields
        identifier_fields = [
            "pulse:orcidIdentifier",
            "pulse:orcid",
            "pulse:githubUsername",
            "pulse:githubRepositoryHandle",
            "pulse:githubOrganizationHandle",
            "pulse:infosciencePersonIdentifier",
            "pulse:infoscienceOrganizationIdentifier",
            "pulse:ror",
            "schema:identifier",
            "pulse:composite",
            "uuid",
        ]

        for field in identifier_fields:
            value = node.get(field)
            if value and value not in index:
                index[value] = node

    return index


def extract_label(node: dict) -> str:
    """Extract a display label for a node."""
    # Try schema:name first
    name = node.get("schema:name")
    if name:
        return name

    # Fall back to @id, extracting the meaningful part
    node_id = node.get("@id", "unknown")
    if "/" in node_id:
        return node_id.split("/")[-1]
    return node_id


def get_entity_type_key(node: dict) -> str:
    """Get the entity type key for configuration lookup."""
    node_type = node.get("@type", "")
    return node_type if node_type in ENTITY_CONFIG else "schema:Person"


def calculate_positions(
    nodes_by_type: dict[str, list],
) -> dict[str, tuple[float, float]]:
    """
    Calculate node positions using hierarchical radial layout.
    Organizations at center, then persons, repositories, and outer entities.
    """
    positions = {}
    ring_radius = {0: 0, 1: 150, 2: 300, 3: 450}  # Distance from center per ring

    # Group nodes by ring
    nodes_by_ring: dict[int, list] = defaultdict(list)
    for entity_type, nodes in nodes_by_type.items():
        config = ENTITY_CONFIG.get(entity_type, ENTITY_CONFIG["schema:Person"])
        ring = config["ring"]
        for node in nodes:
            nodes_by_ring[ring].append((node, entity_type))

    # Position nodes in each ring
    for ring, ring_nodes in nodes_by_ring.items():
        if not ring_nodes:
            continue

        radius = ring_radius.get(ring, 450)
        count = len(ring_nodes)

        if ring == 0 and count == 1:
            # Single center node
            node, _ = ring_nodes[0]
            positions[node["@id"]] = (0, 0)
        else:
            # Distribute nodes evenly around the ring
            for i, (node, _) in enumerate(ring_nodes):
                angle = (2 * math.pi * i / count) - (math.pi / 2)  # Start from top
                x = radius * math.cos(angle)
                y = radius * math.sin(angle)
                positions[node["@id"]] = (x, y)

    return positions


def extract_edges(graph: list[dict], id_index: dict) -> list[dict]:
    """Extract edges from cross-reference fields."""
    edges = []
    edge_set = set()  # Track unique edges
    # Collect all (source, target) pairs first for bidirectional detection
    directed_pairs: set[tuple[str, str]] = set()

    # First pass: collect all unique edges and directed pairs
    for node in graph:
        source_id = node.get("@id", "")

        for field in CROSS_REF_FIELDS:
            values = node.get(field)
            if values is None:
                continue

            # Normalize to list
            if not isinstance(values, list):
                values = [values]

            for target_ref in values:
                if target_ref is None:
                    continue

                # Resolve target reference
                target_node = id_index.get(target_ref)
                if target_node is None:
                    continue

                target_id = target_node.get("@id", "")

                # Create unique edge key
                edge_key = (source_id, target_id, field)
                if edge_key in edge_set:
                    continue
                edge_set.add(edge_key)
                directed_pairs.add((source_id, target_id))

                edge_config = EDGE_CONFIG.get(
                    field,
                    {"label": field, "color": "#94a3b8"},
                )

                edges.append(
                    {
                        "source": source_id,
                        "target": target_id,
                        "label": edge_config["label"],
                        "color": edge_config["color"],
                        "type": field,
                    },
                )

    # Second pass: set curvature for bidirectional edges
    for edge in edges:
        has_reverse = (edge["target"], edge["source"]) in directed_pairs
        edge["curvature"] = 0.3 if has_reverse else 0

    return edges


def _match_validation(
    node_id: str,
    validation_lookup: dict[str, dict],
) -> dict | None:
    """Match a graph node ID to its validation result."""
    # Try full ID
    if node_id in validation_lookup:
        return validation_lookup[node_id]
    # Try short ID (after last /)
    if "/" in node_id:
        short_id = node_id.split("/", 1)[-1]
        if short_id in validation_lookup:
            return validation_lookup[short_id]
    return None


def build_graph_data(
    jsonld: dict,
    validation_lookup: dict[str, dict] | None = None,
) -> dict:
    """Build the graph data structure for Sigma.js."""
    graph = jsonld.get("@graph", [])
    id_index = build_id_index(graph)
    validation_lookup = validation_lookup or {}

    # Group nodes by type
    nodes_by_type: dict[str, list] = defaultdict(list)
    for node in graph:
        entity_type = node.get("@type", "")
        if entity_type in ENTITY_CONFIG:
            nodes_by_type[entity_type].append(node)

    # Calculate positions
    positions = calculate_positions(nodes_by_type)

    # Build nodes
    nodes = []
    connection_counts = defaultdict(int)

    # First pass: count connections for sizing
    edges = extract_edges(graph, id_index)
    for edge in edges:
        connection_counts[edge["source"]] += 1
        connection_counts[edge["target"]] += 1

    # Validation counters
    valid_count = 0
    invalid_count = 0
    no_validation_count = 0

    # Second pass: build node objects
    for node in graph:
        node_id = node.get("@id", "")
        node_type = node.get("@type", "")

        if node_type not in ENTITY_CONFIG:
            continue

        config = ENTITY_CONFIG[node_type]
        pos = positions.get(node_id, (0, 0))

        # Scale size by connection count
        conn_count = connection_counts.get(node_id, 0)
        size = config["size_base"] + min(conn_count * 2, 10)

        # Build properties for tooltip
        properties = {}
        skip_keys = {"@id", "@type"}
        for key, value in node.items():
            if key not in skip_keys and value is not None:
                # Clean up key for display
                display_key = key.split(":")[-1] if ":" in key else key
                properties[display_key] = value

        # Match validation results
        val_result = _match_validation(node_id, validation_lookup)
        if val_result is not None:
            validation_status = {
                "strict": val_result["strict"],
                "agent": val_result["agent"],
                "strict_errors": val_result["strict_errors"],
                "agent_errors": val_result["agent_errors"],
                "shape": val_result["shape"],
            }
            if val_result["strict"] and val_result["agent"]:
                valid_count += 1
            else:
                invalid_count += 1
        else:
            validation_status = None
            no_validation_count += 1

        nodes.append(
            {
                "id": node_id,
                "label": extract_label(node),
                "x": pos[0],
                "y": pos[1],
                "size": size,
                "color": config["color"],
                "nodeType": config["label"],
                "entityType": node_type,
                "properties": properties,
                "validation": validation_status,
            },
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": {
            "generated": datetime.now().isoformat(),
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "entityTypes": list(ENTITY_CONFIG.keys()),
            "validation": {
                "valid": valid_count,
                "invalid": invalid_count,
                "noData": no_validation_count,
                "total": valid_count + invalid_count + no_validation_count,
            },
        },
    }


def generate_html(graph_data: dict, jsonld_text: str) -> str:
    """Generate the complete HTML visualization."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Open Pulse Ontology - JSON-LD Graph Visualization</title>

    <!-- Sigma.js and Graphology from CDN -->
    <script src="https://cdn.jsdelivr.net/npm/graphology@0.25.4/dist/graphology.umd.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/sigma@2.4.0/build/sigma.min.js"></script>
    <!-- ForceAtlas2 layout - use specific bundle that exposes global -->
    <script src="https://cdn.jsdelivr.net/npm/graphology-layout-forceatlas2@0.10.1/build/graphology-layout-forceatlas2.umd.min.js"></script>
    <!-- CodeMirror JSON editor -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/codemirror.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/codemirror/5.65.16/mode/javascript/javascript.min.js"></script>

    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #f8fafc;
            color: #1e293b;
        }}

        #app {{
            display: flex;
            flex-direction: column;
            height: 100vh;
            overflow: hidden;
        }}

        #main-content {{
            display: flex;
            flex: 1;
            min-height: 0;
            min-width: 0;
            position: relative;
            overflow: hidden;
        }}

        /* Sidebar */
        #sidebar {{
            width: 320px;
            min-width: 320px;
            background: white;
            border-right: 1px solid #e2e8f0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            transition: width 0.3s ease, min-width 0.3s ease;
            flex-shrink: 0;
        }}

        #sidebar.collapsed {{
            width: 0;
            min-width: 0;
            border-right: none;
        }}

        #sidebar.collapsed > * {{
            display: none;
        }}

        /* Sidebar toggle wrapper to position button correctly */
        #sidebar-wrapper {{
            position: relative;
            display: flex;
        }}

        #sidebar-toggle {{
            position: absolute;
            left: 320px;
            top: 50%;
            transform: translateY(-50%);
            width: 20px;
            height: 50px;
            background: white;
            border: 1px solid #e2e8f0;
            border-left: none;
            border-radius: 0 6px 6px 0;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            color: #64748b;
            z-index: 100;
            transition: left 0.3s ease, background-color 0.2s;
            box-shadow: 2px 0 4px rgba(0,0,0,0.05);
        }}

        #sidebar-toggle:hover {{
            background: #f1f5f9;
            color: #0f172a;
        }}

        #sidebar-toggle.sidebar-collapsed {{
            left: 0;
        }}

        #sidebar-header {{
            padding: 20px;
            border-bottom: 1px solid #e2e8f0;
        }}

        #sidebar-header-top {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
        }}

        #sidebar-header h1 {{
            font-size: 18px;
            font-weight: 600;
            color: #0f172a;
            margin-bottom: 4px;
        }}

        #sidebar-header p {{
            font-size: 12px;
            color: #64748b;
        }}

        .sidebar-header-action {{
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            background: #f8fafc;
            color: #334155;
            font-size: 12px;
            font-weight: 600;
            padding: 6px 10px;
            cursor: pointer;
            white-space: nowrap;
            transition: background 0.2s, color 0.2s, border-color 0.2s;
        }}

        .sidebar-header-action:hover {{
            background: #e2e8f0;
            color: #0f172a;
            border-color: #94a3b8;
        }}

        /* Search */
        #search-container {{
            padding: 16px 20px;
            border-bottom: 1px solid #e2e8f0;
        }}

        #search-input {{
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            font-size: 14px;
            outline: none;
            transition: border-color 0.2s;
        }}

        #search-input:focus {{
            border-color: #3b82f6;
        }}

        #search-results {{
            max-height: 200px;
            overflow-y: auto;
            margin-top: 8px;
        }}

        .search-result {{
            padding: 8px 12px;
            cursor: pointer;
            border-radius: 6px;
            font-size: 13px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .search-result:hover {{
            background: #f1f5f9;
        }}

        .search-result-dot {{
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }}

        /* Collapsible sidebar sections */
        .sidebar-section {{
            border-bottom: 1px solid #e2e8f0;
            overflow: hidden;
        }}

        .sidebar-section-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 20px;
            cursor: pointer;
            user-select: none;
            background: #f8fafc;
            transition: background 0.2s;
        }}

        .sidebar-section-header:hover {{
            background: #f1f5f9;
        }}

        .sidebar-section-header h3 {{
            font-size: 12px;
            font-weight: 600;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin: 0;
        }}

        .sidebar-section-toggle {{
            font-size: 12px;
            color: #94a3b8;
            transition: transform 0.2s;
        }}

        .sidebar-section.collapsed .sidebar-section-toggle {{
            transform: rotate(-90deg);
        }}

        .sidebar-section-content {{
            padding: 12px 20px;
            transition: max-height 0.3s ease, padding 0.3s ease, opacity 0.2s ease;
            max-height: 500px;
            opacity: 1;
        }}

        .sidebar-section.collapsed .sidebar-section-content {{
            max-height: 0;
            padding-top: 0;
            padding-bottom: 0;
            opacity: 0;
        }}

        /* Resize handle between sections */
        .sidebar-resize-handle {{
            height: 6px;
            background: transparent;
            cursor: ns-resize;
            position: relative;
            border-bottom: 1px solid #e2e8f0;
        }}

        .sidebar-resize-handle:hover {{
            background: #e2e8f0;
        }}

        .sidebar-resize-handle::after {{
            content: '';
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            width: 30px;
            height: 3px;
            border-radius: 2px;
            background: #cbd5e1;
        }}

        /* Filters */
        #filters-container {{
        }}

        #filters-container h3 {{}}

        .filter-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 6px 0;
            cursor: pointer;
        }}

        .filter-checkbox {{
            width: 16px;
            height: 16px;
            cursor: pointer;
        }}

        .filter-color {{
            width: 12px;
            height: 12px;
            border-radius: 3px;
        }}

        .filter-label {{
            font-size: 14px;
            flex: 1;
        }}

        .filter-count {{
            font-size: 12px;
            color: #94a3b8;
        }}

        /* Legend */
        #legend-container {{
        }}

        #legend-container h3 {{}}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 4px 0;
            font-size: 13px;
        }}

        .legend-line {{
            width: 20px;
            height: 3px;
            border-radius: 2px;
        }}

        /* Node details */
        #details-container {{
            flex: 1;
            overflow-y: auto;
        }}

        #details-container h3 {{}}

        #details-placeholder {{
            color: #94a3b8;
            font-size: 13px;
            font-style: italic;
        }}

        #node-details {{
            display: none;
        }}

        #node-details.active {{
            display: block;
        }}

        #node-title {{
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 4px;
            word-break: break-word;
        }}

        #node-type {{
            display: inline-block;
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            margin-bottom: 16px;
        }}

        #node-actions {{
            display: flex;
            gap: 8px;
            margin-bottom: 12px;
        }}

        .node-action-btn {{
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            background: #f8fafc;
            color: #334155;
            font-size: 12px;
            font-weight: 600;
            padding: 6px 10px;
            cursor: pointer;
            transition: background 0.2s, color 0.2s, border-color 0.2s;
        }}

        .node-action-btn:hover:not(:disabled) {{
            background: #dbeafe;
            color: #1e3a8a;
            border-color: #93c5fd;
        }}

        .node-action-btn:disabled {{
            opacity: 0.55;
            cursor: not-allowed;
        }}

        .property-item {{
            padding: 8px 0;
            border-bottom: 1px solid #f1f5f9;
        }}

        .property-key {{
            font-size: 11px;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            margin-bottom: 2px;
        }}

        .property-value {{
            font-size: 13px;
            word-break: break-word;
        }}

        .property-value a {{
            color: #3b82f6;
            text-decoration: none;
        }}

        .property-value a:hover {{
            text-decoration: underline;
        }}

        /* JSON-LD editor panel */
        #jsonld-editor-wrapper {{
            position: relative;
            display: flex;
            width: 440px;
            min-width: 440px;
            border-right: 1px solid #e2e8f0;
            background: white;
            transition: width 0.3s ease, min-width 0.3s ease, border-color 0.3s ease;
            flex-shrink: 0;
            z-index: 30;
        }}

        #jsonld-editor-wrapper.collapsed {{
            width: 0;
            min-width: 0;
            border-right-color: transparent;
        }}

        #jsonld-editor-panel {{
            width: 100%;
            min-width: 0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            transition: opacity 0.2s ease;
        }}

        #jsonld-editor-wrapper.collapsed #jsonld-editor-panel {{
            opacity: 0;
            pointer-events: none;
        }}

        #jsonld-editor-toggle {{
            position: absolute;
            left: 100%;
            top: 50%;
            transform: translateY(-50%);
            width: 20px;
            height: 50px;
            background: white;
            border: 1px solid #e2e8f0;
            border-left: none;
            border-radius: 0 6px 6px 0;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            color: #64748b;
            z-index: 120;
            box-shadow: 2px 0 4px rgba(0, 0, 0, 0.05);
            transition: background 0.2s, color 0.2s;
        }}

        #jsonld-editor-toggle:hover {{
            background: #f1f5f9;
            color: #0f172a;
        }}

        #jsonld-editor-header {{
            padding: 12px 14px;
            border-bottom: 1px solid #e2e8f0;
            background: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }}

        #jsonld-editor-title {{
            font-size: 12px;
            font-weight: 700;
            color: #334155;
            letter-spacing: 0.02em;
            text-transform: uppercase;
        }}

        #jsonld-editor-actions {{
            display: flex;
            gap: 6px;
        }}

        .jsonld-action-btn {{
            border: 1px solid #cbd5e1;
            border-radius: 7px;
            background: white;
            color: #334155;
            font-size: 12px;
            font-weight: 600;
            padding: 5px 9px;
            cursor: pointer;
            transition: background 0.2s, color 0.2s, border-color 0.2s;
        }}

        .jsonld-action-btn:hover {{
            background: #e2e8f0;
            border-color: #94a3b8;
            color: #0f172a;
        }}

        #jsonld-editor-toolbar {{
            padding: 8px 12px;
            border-bottom: 1px solid #e2e8f0;
            background: #f8fafc;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .jsonld-toolbar-btn {{
            border: 1px solid #cbd5e1;
            border-radius: 7px;
            background: white;
            color: #334155;
            font-size: 12px;
            font-weight: 600;
            padding: 5px 9px;
            cursor: pointer;
            transition: background 0.2s, color 0.2s, border-color 0.2s;
        }}

        .jsonld-toolbar-btn:hover:not(:disabled) {{
            background: #e2e8f0;
            border-color: #94a3b8;
            color: #0f172a;
        }}

        .jsonld-toolbar-btn.active {{
            background: #dbeafe;
            border-color: #93c5fd;
            color: #1e3a8a;
        }}

        .jsonld-toolbar-btn:disabled {{
            opacity: 0.55;
            cursor: not-allowed;
        }}

        #jsonld-editor-status {{
            min-height: 36px;
            padding: 9px 12px;
            border-bottom: 1px solid #e2e8f0;
            font-size: 12px;
            line-height: 1.4;
            color: #334155;
            background: #f8fafc;
        }}

        #jsonld-editor-status.success {{
            color: #166534;
            background: #f0fdf4;
            border-bottom-color: #bbf7d0;
        }}

        #jsonld-editor-status.error {{
            color: #991b1b;
            background: #fef2f2;
            border-bottom-color: #fecaca;
        }}

        #jsonld-editor-container {{
            flex: 1;
            min-height: 0;
            display: flex;
            min-width: 0;
        }}

        #jsonld-editor-main {{
            flex: 1;
            min-width: 0;
            min-height: 0;
        }}

        #jsonld-editor-textarea {{
            width: 100%;
            height: 100%;
        }}

        #jsonld-editor-container .CodeMirror {{
            height: 100%;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 12px;
        }}

        #jsonld-editor-container .cm-node-line {{
            background: rgba(59, 130, 246, 0.16);
        }}

        #jsonld-editor-container .cm-node-ref-line {{
            background: rgba(14, 165, 233, 0.14);
        }}

        #jsonld-minimap {{
            width: 64px;
            min-width: 64px;
            border-left: 1px solid #e2e8f0;
            background: #f8fafc;
            position: relative;
            display: none;
            cursor: pointer;
        }}

        #jsonld-minimap.visible {{
            display: block;
        }}

        #jsonld-minimap-track {{
            position: absolute;
            inset: 0;
            background: linear-gradient(to bottom, rgba(148, 163, 184, 0.16), rgba(148, 163, 184, 0.08));
        }}

        #jsonld-minimap-markers {{
            position: absolute;
            inset: 0;
        }}

        .minimap-marker {{
            position: absolute;
            left: 8px;
            right: 8px;
            min-height: 2px;
            border-radius: 2px;
            opacity: 0.95;
        }}

        .minimap-marker-node {{
            background: rgba(37, 99, 235, 0.95);
        }}

        .minimap-marker-ref {{
            background: rgba(14, 116, 144, 0.85);
        }}

        #jsonld-minimap-viewport {{
            position: absolute;
            left: 4px;
            right: 4px;
            border: 1px solid rgba(100, 116, 139, 0.6);
            background: rgba(148, 163, 184, 0.16);
            border-radius: 4px;
            pointer-events: none;
            min-height: 10px;
        }}

        /* Graph container */
        #graph-area {{
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 0;
            min-width: 0;
            overflow: hidden;
        }}

        #graph-container {{
            flex: 1;
            position: relative;
            min-height: 0;
        }}

        #sigma-container {{
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: #f8fafc;
        }}

        /* Controls */
        #controls {{
            position: absolute;
            top: 20px;
            right: 20px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            z-index: 60;
        }}

        .control-btn {{
            width: 40px;
            height: 40px;
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
            color: #64748b;
            transition: all 0.2s;
        }}

        .control-btn:hover {{
            background: #f1f5f9;
            color: #1e293b;
        }}

        .control-btn.active {{
            background: #dbeafe;
            color: #3b82f6;
            border-color: #3b82f6;
        }}

        /* Layout dropdown menu — anchored to layout-toggle button */
        #layout-toggle-wrapper {{
            position: relative;
        }}

        #layout-menu {{
            position: absolute;
            top: 0;
            right: 48px;
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 4px;
            z-index: 100;
            display: none;
            flex-direction: column;
            gap: 2px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}

        #layout-menu.visible {{
            display: flex;
        }}

        .layout-menu-item {{
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 500;
            background: transparent;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            color: #64748b;
            transition: all 0.2s;
            text-align: left;
            white-space: nowrap;
        }}

        .layout-menu-item:hover {{
            background: #f1f5f9;
            color: #1e293b;
        }}

        .layout-menu-item.active {{
            background: #dbeafe;
            color: #3b82f6;
            font-weight: 600;
        }}

        /* Drag indicator */
        #drag-hint {{
            position: absolute;
            bottom: 60px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0, 0, 0, 0.7);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 12px;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.3s;
        }}

        #drag-hint.visible {{
            opacity: 1;
        }}

        /* Stats */
        #stats {{
            position: absolute;
            bottom: 20px;
            right: 20px;
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 12px 16px;
            font-size: 12px;
            color: #64748b;
        }}

        #stats span {{
            color: #1e293b;
            font-weight: 600;
        }}

        /* Tooltip */
        #tooltip {{
            position: absolute;
            background: rgba(255, 255, 255, 0.97);
            border: 1px solid #cbd5e1;
            border-radius: 10px;
            padding: 12px 14px;
            box-shadow: 0 18px 32px -24px rgba(15, 23, 42, 0.55), 0 8px 16px -12px rgba(15, 23, 42, 0.35);
            backdrop-filter: blur(6px);
            pointer-events: none;
            opacity: 0;
            visibility: hidden;
            transform: translateY(4px);
            transition: opacity 0.14s ease, transform 0.14s ease;
            max-width: 360px;
            z-index: 1000;
        }}

        #tooltip.visible {{
            opacity: 1;
            visibility: visible;
            transform: translateY(0);
        }}

        #tooltip-title {{
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 4px;
        }}

        #tooltip-type {{
            display: inline-block;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
            margin-bottom: 8px;
        }}

        #tooltip-edge {{
            font-size: 12px;
            color: #64748b;
        }}

        #tooltip-properties {{
            font-size: 12px;
            max-height: 200px;
            overflow-y: auto;
            border-top: 1px solid #e2e8f0;
            padding-top: 8px;
            margin-top: 8px;
        }}

        .tooltip-prop {{
            padding: 3px 0;
            display: flex;
            gap: 8px;
        }}

        .tooltip-prop-key {{
            color: #64748b;
            font-weight: 500;
            min-width: 80px;
            flex-shrink: 0;
        }}

        .tooltip-prop-value {{
            color: #1e293b;
            word-break: break-word;
        }}

        /* Tooltip for graph control buttons */
        #ui-tooltip {{
            position: absolute;
            left: 0;
            top: 0;
            background: rgba(255, 255, 255, 0.97);
            border: 1px solid #cbd5e1;
            border-radius: 8px;
            box-shadow: 0 12px 28px -24px rgba(15, 23, 42, 0.65), 0 6px 16px -14px rgba(15, 23, 42, 0.4);
            color: #0f172a;
            font-size: 12px;
            font-weight: 500;
            line-height: 1.35;
            padding: 7px 10px;
            max-width: 280px;
            white-space: normal;
            pointer-events: none;
            opacity: 0;
            visibility: hidden;
            transform: translateY(2px);
            transition: opacity 0.12s ease, transform 0.12s ease;
            z-index: 1200;
        }}

        #ui-tooltip.visible {{
            opacity: 1;
            visibility: visible;
            transform: translateY(0);
        }}

        /* Data Panel (Bottom) - inside graph area */
        #data-panel {{
            background: white;
            border-top: 1px solid #e2e8f0;
            transition: max-height 0.3s ease;
            max-height: 280px;
            overflow: hidden;
            flex-shrink: 0;
            position: relative;
            z-index: 50;
        }}

        #data-panel.collapsed {{
            max-height: 36px;
        }}

        #data-panel-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 20px;
            background: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
            cursor: pointer;
        }}

        #data-panel-header h3 {{
            font-size: 13px;
            font-weight: 600;
            color: #475569;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        #data-panel-toggle {{
            font-size: 18px;
            color: #64748b;
            transition: transform 0.3s;
        }}

        #data-panel.collapsed #data-panel-toggle {{
            transform: rotate(180deg);
        }}

        #data-panel-tabs {{
            display: flex;
            gap: 0;
            padding: 0 20px;
            background: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
            overflow-x: auto;
        }}

        .data-tab {{
            padding: 10px 16px;
            font-size: 12px;
            font-weight: 500;
            color: #64748b;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            white-space: nowrap;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .data-tab:hover {{
            color: #1e293b;
        }}

        .data-tab.active {{
            color: #3b82f6;
            border-bottom-color: #3b82f6;
        }}

        .data-tab-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
        }}

        .data-tab-count {{
            background: #e2e8f0;
            padding: 2px 6px;
            border-radius: 10px;
            font-size: 10px;
        }}

        #data-panel-content {{
            height: 200px;
            padding: 0;
            overflow: hidden;
            position: relative;
        }}

        .data-table-wrapper {{
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            overflow: auto;
        }}

        .data-table {{
            border-collapse: collapse;
            font-size: 12px;
        }}

        .data-table th {{
            position: sticky;
            top: 0;
            background: #f8fafc;
            padding: 10px 12px;
            text-align: left;
            font-weight: 600;
            color: #475569;
            border-bottom: 1px solid #e2e8f0;
            white-space: nowrap;
        }}

        .data-table td {{
            padding: 8px 12px;
            border-bottom: 1px solid #f1f5f9;
            white-space: nowrap;
        }}

        .data-table tr {{
            cursor: pointer;
        }}

        .data-table tr:hover {{
            background: #f1f5f9;
        }}

        .data-table tr.selected {{
            background: #dbeafe;
        }}

        /* Info modal overlay */
        #info-modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(15, 23, 42, 0.4);
            backdrop-filter: blur(6px);
            -webkit-backdrop-filter: blur(6px);
            z-index: 200;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 1;
            transition: opacity 0.25s ease;
        }}

        #info-modal-overlay.hidden {{
            opacity: 0;
            pointer-events: none;
        }}

        #info-modal {{
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
            max-width: 520px;
            width: 90%;
            max-height: 85vh;
            overflow-y: auto;
            padding: 32px;
            position: relative;
        }}

        #info-modal h2 {{
            font-size: 20px;
            font-weight: 700;
            color: #0f172a;
            margin-bottom: 4px;
        }}

        #info-modal .modal-subtitle {{
            font-size: 13px;
            color: #64748b;
            margin-bottom: 20px;
        }}

        #info-modal .feature-list {{
            list-style: none;
            padding: 0;
            margin: 0 0 20px 0;
        }}

        #info-modal .feature-list li {{
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 8px 0;
            font-size: 13px;
            color: #334155;
            line-height: 1.5;
        }}

        #info-modal .feature-icon {{
            font-size: 16px;
            flex-shrink: 0;
            width: 24px;
            text-align: center;
        }}

        #info-modal .feature-title {{
            font-weight: 600;
            color: #0f172a;
        }}

        #info-close-btn {{
            position: absolute;
            top: 16px;
            right: 16px;
            width: 32px;
            height: 32px;
            border-radius: 8px;
            border: 1px solid #e2e8f0;
            background: #f8fafc;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            color: #64748b;
            transition: all 0.2s;
        }}

        #info-close-btn:hover {{
            background: #f1f5f9;
            color: #0f172a;
        }}

        #info-dismiss-btn {{
            display: block;
            width: 100%;
            padding: 10px;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }}

        #info-dismiss-btn:hover {{
            background: #2563eb;
        }}

        .kbd {{
            display: inline-block;
            padding: 1px 5px;
            font-size: 11px;
            font-family: monospace;
            background: #f1f5f9;
            border: 1px solid #e2e8f0;
            border-radius: 4px;
            color: #475569;
        }}
    </style>
</head>
<body>
    <!-- Info modal -->
    <div id="info-modal-overlay">
        <div id="info-modal">
            <button id="info-close-btn" title="Close">✕</button>
            <h2>Open Pulse Ontology</h2>
            <p class="modal-subtitle">Interactive JSON-LD Graph Visualization</p>
            <ul class="feature-list">
                <li>
                    <span class="feature-icon">⊞</span>
                    <span><span class="feature-title">Layouts</span> — Switch between Force Atlas, Circular, Radial, and Grid layouts using the <span class="kbd">⊞</span> button (top-right).</span>
                </li>
                <li>
                    <span class="feature-icon">🔍</span>
                    <span><span class="feature-title">Search &amp; Filter</span> — Use the sidebar search to find nodes by name. Toggle entity types on/off with the checkboxes.</span>
                </li>
                <li>
                    <span class="feature-icon">✓</span>
                    <span><span class="feature-title">Validation Badges</span> — Each node shows a green ✓ (pass) or red ✗ (fail) badge for schema validation. Click a node to see full details.</span>
                </li>
                <li>
                    <span class="feature-icon">✋</span>
                    <span><span class="feature-title">Drag Mode</span> — Enable with the <span class="kbd">✋</span> button, then click and drag nodes to reposition them.</span>
                </li>
                <li>
                    <span class="feature-icon">🔗</span>
                    <span><span class="feature-title">Table Sync</span> — When enabled (top-left <span class="kbd">🔗</span>), clicking a node auto-opens the matching data table tab and highlights the row.</span>
                </li>
                <li>
                    <span class="feature-icon">💬</span>
                    <span><span class="feature-title">Tooltips</span> — Hover any node or edge for a quick property preview. Toggle with the <span class="kbd">💬</span> button.</span>
                </li>
                <li>
                    <span class="feature-icon">📊</span>
                    <span><span class="feature-title">Data Tables</span> — Expand the bottom panel to browse all entities by type, or switch to the Validation tab for a full status overview.</span>
                </li>
            </ul>
            <button id="info-dismiss-btn">Got it, explore the graph</button>
        </div>
    </div>

    <div id="app">
        <div id="main-content">
            <div id="sidebar-wrapper">
                <div id="sidebar">
                    <div id="sidebar-header">
                        <div id="sidebar-header-top">
                            <div>
                                <h1>Open Pulse Ontology</h1>
                                <p>JSON-LD Graph Visualization</p>
                            </div>
                            <button id="open-jsonld-editor-btn" class="sidebar-header-action" title="Open JSON-LD editor">Edit JSON-LD</button>
                        </div>
                    </div>

                <div id="search-container">
                    <input type="text" id="search-input" placeholder="Search nodes...">
                    <div id="search-results"></div>
                </div>

                <div class="sidebar-section" id="filters-section">
                    <div class="sidebar-section-header">
                        <h3>Entity Types</h3>
                        <span class="sidebar-section-toggle">▼</span>
                    </div>
                    <div class="sidebar-section-content">
                        <div id="filters"></div>
                    </div>
                </div>

                <div class="sidebar-section" id="legend-section">
                    <div class="sidebar-section-header">
                        <h3>Relationships</h3>
                        <span class="sidebar-section-toggle">▼</span>
                    </div>
                    <div class="sidebar-section-content">
                        <div id="edge-legend"></div>
                    </div>
                </div>

                <div class="sidebar-section" id="details-section">
                    <div class="sidebar-section-header">
                        <h3>Node Details</h3>
                        <span class="sidebar-section-toggle">▼</span>
                    </div>
                    <div class="sidebar-section-content" id="details-container">
                        <p id="details-placeholder">Click a node to see details</p>
                        <div id="node-details">
                            <div id="node-title"></div>
                            <span id="node-type"></span>
                            <div id="node-actions">
                                <button id="edit-node-jsonld-btn" class="node-action-btn" disabled title="Open editor and highlight this node in JSON-LD">Edit in JSON-LD</button>
                            </div>
                            <div id="node-properties"></div>
                        </div>
                    </div>
                </div>
            </div>
            <button id="sidebar-toggle" title="Toggle Sidebar">◀</button>
            </div>

            <div id="jsonld-editor-wrapper" class="collapsed">
                <div id="jsonld-editor-panel">
                    <div id="jsonld-editor-header">
                        <span id="jsonld-editor-title">JSON-LD Editor</span>
                        <div id="jsonld-editor-actions">
                            <button id="jsonld-apply-btn" class="jsonld-action-btn" title="Apply JSON-LD changes to rebuild graph">Apply</button>
                            <button id="jsonld-download-btn" class="jsonld-action-btn" title="Download edited JSON-LD">Download</button>
                        </div>
                    </div>
                    <div id="jsonld-editor-toolbar">
                        <button id="jsonld-toggle-minimap-btn" class="jsonld-toolbar-btn" title="Show or hide editor minimap">Show Minimap</button>
                    </div>
                    <div id="jsonld-editor-status">Editor ready. Select a node and use "Edit in JSON-LD" to jump to related lines.</div>
                    <div id="jsonld-editor-container">
                        <div id="jsonld-editor-main">
                            <textarea id="jsonld-editor-textarea"></textarea>
                        </div>
                        <div id="jsonld-minimap" aria-hidden="true">
                            <div id="jsonld-minimap-track"></div>
                            <div id="jsonld-minimap-markers"></div>
                            <div id="jsonld-minimap-viewport"></div>
                        </div>
                    </div>
                </div>
                <button id="jsonld-editor-toggle" title="Toggle JSON-LD editor">▶</button>
            </div>

            <div id="graph-area">
                <div id="graph-container">
                    <div id="sigma-container"></div>

                    <div id="controls">
                        <button class="control-btn" id="zoom-in" title="Zoom In">+</button>
                        <button class="control-btn" id="zoom-out" title="Zoom Out">−</button>
                        <button class="control-btn" id="zoom-fit" title="Fit to View">⊡</button>
                        <button class="control-btn" id="reset-view" title="Reset view and filters">↺</button>
                        <button class="control-btn" id="toggle-drag" title="Toggle Drag Mode — click and drag nodes to reposition">✋</button>
                        <button class="control-btn active" id="toggle-tooltip" title="Toggle Tooltips — show property previews on hover">💬</button>
                        <button class="control-btn active" id="toggle-sync" title="Toggle table sync — auto-switch tab and highlight row on node click">🔗</button>
                        <div id="layout-toggle-wrapper">
                            <button class="control-btn" id="layout-toggle" title="Change graph layout">⊞</button>
                            <div id="layout-menu">
                                <button class="layout-menu-item active" data-layout="force" title="Animated force-directed layout">Force Atlas</button>
                                <button class="layout-menu-item" data-layout="circular" title="Arrange nodes in a circle grouped by type">Circular</button>
                                <button class="layout-menu-item" data-layout="radial" title="Hierarchical rings by entity type">Radial</button>
                                <button class="layout-menu-item" data-layout="grid" title="Arrange nodes in a grid sorted by type">Grid</button>
                            </div>
                        </div>
                        <button class="control-btn" id="stop-layout" title="Stop layout animation" style="display:none;">⏹</button>
                        <button class="control-btn" id="info-btn" title="Show help and feature guide">ⓘ</button>
                    </div>

                    <div id="ui-tooltip"></div>

                    <div id="drag-hint">Drag mode: Click and drag nodes to reposition them</div>

                    <div id="stats">
                        <span id="node-count">0</span> nodes · <span id="edge-count">0</span> edges · <span id="validation-count"></span>
                    </div>

                    <div id="tooltip">
                        <div id="tooltip-title"></div>
                        <div id="tooltip-type"></div>
                        <div id="tooltip-edge"></div>
                        <div id="tooltip-properties"></div>
                    </div>
                </div>

                <!-- Data Panel inside graph area -->
                <div id="data-panel" class="collapsed">
                    <div id="data-panel-header">
                        <h3>📊 Data Tables</h3>
                        <span id="data-panel-toggle">▼</span>
                    </div>
                    <div id="data-panel-tabs"></div>
                    <div id="data-panel-content"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Graph/source data injected from Python
        let graphData = {json.dumps(graph_data, indent=2)};
        const INITIAL_JSONLD_TEXT = {json.dumps(jsonld_text)};
        const ENTITY_TYPE_CONFIG = {json.dumps(ENTITY_CONFIG, indent=2)};
        const EDGE_TYPE_CONFIG = {json.dumps(EDGE_CONFIG, indent=2)};
        const CROSS_REF_FIELDS = {json.dumps(CROSS_REF_FIELDS, indent=2)};

        // Entity configuration for tabs/filters (label-keyed)
        const ENTITY_CONFIG = {json.dumps({v["label"]: {"color": v["color"]} for v in ENTITY_CONFIG.values()}, indent=2)};

        // Edge legend configuration (label-keyed)
        const EDGE_CONFIG = {json.dumps({v["label"]: {"color": v["color"]} for v in EDGE_CONFIG.values()}, indent=2)};

        // Initialize the graph
        const graph = new graphology.Graph({{ multi: true }});

        // State
        let selectedNode = null;
        let hoveredNode = null;
        let highlightedNodes = new Set();
        let highlightedEdges = new Set();
        let hiddenTypes = new Set();
        let layoutRunning = false;
        let tooltipsEnabled = true;
        let tableSyncEnabled = true;
        let nodesByType = {{}};
        let activeDataTab = null;

        // JSON-LD editor state
        let jsonldEditor = null;
        let sourceIndex = {{}};
        let highlightedSourceLines = [];

        function addGraphDataToGraph(data) {{
            graph.clear();
            data.nodes.forEach(node => {{
                graph.addNode(node.id, {{
                    label: node.label,
                    x: node.x,
                    y: node.y,
                    size: node.size,
                    color: node.color,
                    nodeType: node.nodeType,
                    entityType: node.entityType,
                    properties: node.properties,
                    validation: node.validation,
                    originalColor: node.color,
                    originalSize: node.size,
                }});
            }});

            data.edges.forEach((edge, index) => {{
                if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {{
                    graph.addEdgeWithKey(`edge-${{index}}`, edge.source, edge.target, {{
                        label: edge.label,
                        color: edge.color,
                        size: 2,
                        edgeType: edge.type,
                        originalColor: edge.color,
                        curvature: edge.curvature,
                    }});
                }}
            }});
        }}

        function buildValidationLookupFromGraphData(data) {{
            const lookup = {{}};
            (data.nodes || []).forEach(node => {{
                if (node.validation !== undefined && node.validation !== null) {{
                    lookup[node.id] = node.validation;
                    if (node.id.includes("/")) {{
                        lookup[node.id.split("/").slice(-1)[0]] = node.validation;
                    }}
                }}
            }});
            return lookup;
        }}

        const validationLookupById = buildValidationLookupFromGraphData(graphData);
        addGraphDataToGraph(graphData);

        // Custom label renderer with type badge
        function drawLabel(context, data, settings) {{
            if (!data.label) return;

            const fontSize = settings.labelSize || 12;
            const fontWeight = settings.labelWeight || "normal";
            const fontColor = settings.labelColor.color || "#1e293b";

            // Get node type for badge
            const nodeType = data.nodeType || "Unknown";
            const typeColor = data.color || "#64748b";

            const x = data.x;
            const y = data.y;
            const size = data.size;

            // Draw type badge above node
            const badgeFontSize = Math.max(8, fontSize - 3);
            context.font = `bold ${{badgeFontSize}}px ${{settings.labelFont || "sans-serif"}}`;
            const badgeWidth = context.measureText(nodeType).width + 8;
            const badgeHeight = badgeFontSize + 4;
            const badgeY = y - size - badgeHeight - 4;

            // Badge background
            context.fillStyle = typeColor + "30";
            context.strokeStyle = typeColor;
            context.lineWidth = 1;
            context.beginPath();
            const badgeRadius = 3;
            const badgeX = x - badgeWidth / 2;
            context.moveTo(badgeX + badgeRadius, badgeY);
            context.lineTo(badgeX + badgeWidth - badgeRadius, badgeY);
            context.quadraticCurveTo(badgeX + badgeWidth, badgeY, badgeX + badgeWidth, badgeY + badgeRadius);
            context.lineTo(badgeX + badgeWidth, badgeY + badgeHeight - badgeRadius);
            context.quadraticCurveTo(badgeX + badgeWidth, badgeY + badgeHeight, badgeX + badgeWidth - badgeRadius, badgeY + badgeHeight);
            context.lineTo(badgeX + badgeRadius, badgeY + badgeHeight);
            context.quadraticCurveTo(badgeX, badgeY + badgeHeight, badgeX, badgeY + badgeHeight - badgeRadius);
            context.lineTo(badgeX, badgeY + badgeRadius);
            context.quadraticCurveTo(badgeX, badgeY, badgeX + badgeRadius, badgeY);
            context.closePath();
            context.fill();
            context.stroke();

            // Badge text
            context.fillStyle = typeColor;
            context.textAlign = "center";
            context.textBaseline = "middle";
            context.fillText(nodeType, x, badgeY + badgeHeight / 2);

            // Draw main label below node
            context.font = `${{fontWeight}} ${{fontSize}}px ${{settings.labelFont || "sans-serif"}}`;
            context.fillStyle = fontColor;
            context.textAlign = "center";
            context.textBaseline = "top";

            // Truncate long labels
            let displayLabel = data.label;
            const maxWidth = 120;
            if (context.measureText(displayLabel).width > maxWidth) {{
                while (context.measureText(displayLabel + "...").width > maxWidth && displayLabel.length > 0) {{
                    displayLabel = displayLabel.slice(0, -1);
                }}
                displayLabel += "...";
            }}

            // Draw label with background for readability
            const labelWidth = context.measureText(displayLabel).width;
            context.fillStyle = "rgba(255, 255, 255, 0.85)";
            context.fillRect(x - labelWidth / 2 - 3, y + size + 3, labelWidth + 6, fontSize + 4);

            context.fillStyle = fontColor;
            context.fillText(displayLabel, x, y + size + 5);

            // Draw validation badge (top-right of node)
            const validation = data.validation;
            if (validation !== undefined) {{
                const badgeR = Math.max(4, size * 0.3);
                const bx = x + size * 0.7;
                const by = y - size * 0.7;
                context.beginPath();
                context.arc(bx, by, badgeR, 0, 2 * Math.PI);
                if (validation === null) {{
                    context.fillStyle = "#94a3b8"; // gray - no data
                }} else if (validation.strict && validation.agent) {{
                    context.fillStyle = "#22c55e"; // green - all pass
                }} else {{
                    context.fillStyle = "#ef4444"; // red - failures
                }}
                context.fill();
                context.strokeStyle = "white";
                context.lineWidth = 1.5;
                context.stroke();

                // Draw checkmark or X
                context.fillStyle = "white";
                context.font = `bold ${{Math.max(6, badgeR)}}px sans-serif`;
                context.textAlign = "center";
                context.textBaseline = "middle";
                if (validation === null) {{
                    context.fillText("?", bx, by);
                }} else if (validation.strict && validation.agent) {{
                    context.fillText("✓", bx, by);
                }} else {{
                    context.fillText("✗", bx, by);
                }}
            }}
        }}

        // Initialize Sigma with custom settings
        const sigmaSettings = {{
            renderEdgeLabels: false,
            enableEdgeEvents: true,
            // Show labels on ALL nodes (no threshold)
            labelRenderedSizeThreshold: 0,
            // Show all labels regardless of density
            labelDensity: 1,
            labelGridCellSize: 50,
            zIndex: true,
            minCameraRatio: 0.1,
            maxCameraRatio: 10,
            // Reduce zoom sensitivity (default is 1.7, lower = less sensitive)
            zoomingRatio: 1.2,
            labelRenderer: drawLabel,
            labelFont: "sans-serif",
            labelSize: 11,
            labelWeight: "normal",
            labelColor: {{ color: "#1e293b" }},
        }};

        const container = document.getElementById("sigma-container");

        // Check if libraries loaded
        if (typeof graphology === 'undefined') {{
            container.innerHTML = '<p style="padding: 20px; color: red;">Error: graphology library failed to load</p>';
            throw new Error('graphology not loaded');
        }}
        if (typeof Sigma === 'undefined') {{
            container.innerHTML = '<p style="padding: 20px; color: red;">Error: Sigma.js library failed to load</p>';
            throw new Error('Sigma not loaded');
        }}

        console.log('Graph nodes:', graph.order, 'edges:', graph.size);

        const sigma = new Sigma(graph, container, sigmaSettings);

        console.log('Sigma initialized');

        // Store original positions for reset
        const originalPositions = {{}};
        graph.forEachNode((nodeId, attrs) => {{
            originalPositions[nodeId] = {{ x: attrs.x, y: attrs.y }};
        }});

        // Current layout type
        let currentLayout = 'force';
        let forceAnimationId = null;
        let forceIterations = 0;
        const stopLayoutBtn = document.getElementById('stop-layout');

        // Stop any running force animation
        function stopForceAnimation() {{
            if (forceAnimationId) {{
                cancelAnimationFrame(forceAnimationId);
                forceAnimationId = null;
            }}
            stopLayoutBtn.style.display = 'none';
            layoutRunning = false;
        }}

        // Layout functions
        const layouts = {{
            force: function() {{
                // Stop any existing animation
                stopForceAnimation();

                forceIterations = 0;
                const maxIterations = 300;

                // Show stop button
                stopLayoutBtn.style.display = 'flex';
                layoutRunning = true;

                // Simple force-directed layout implementation
                // This works without external dependencies
                const nodes = [];
                const nodeIndex = {{}};
                let i = 0;
                graph.forEachNode((nodeId, attrs) => {{
                    nodeIndex[nodeId] = i;
                    nodes.push({{
                        id: nodeId,
                        x: attrs.x || Math.random() * 500 - 250,
                        y: attrs.y || Math.random() * 500 - 250,
                        vx: 0,
                        vy: 0,
                        size: attrs.size || 10
                    }});
                    i++;
                }});

                const edges = [];
                graph.forEachEdge((edgeId, attrs, source, target) => {{
                    edges.push({{
                        source: nodeIndex[source],
                        target: nodeIndex[target]
                    }});
                }});

                // Force simulation parameters
                const repulsion = 5000;
                const attraction = 0.01;
                const gravity = 0.1;
                const damping = 0.9;
                const minDistance = 50;

                function simulateStep() {{
                    // Reset velocities partially (damping)
                    nodes.forEach(node => {{
                        node.vx *= damping;
                        node.vy *= damping;
                    }});

                    // Repulsion between all nodes
                    for (let i = 0; i < nodes.length; i++) {{
                        for (let j = i + 1; j < nodes.length; j++) {{
                            const dx = nodes[j].x - nodes[i].x;
                            const dy = nodes[j].y - nodes[i].y;
                            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                            const force = repulsion / (dist * dist);

                            const fx = (dx / dist) * force;
                            const fy = (dy / dist) * force;

                            nodes[i].vx -= fx;
                            nodes[i].vy -= fy;
                            nodes[j].vx += fx;
                            nodes[j].vy += fy;
                        }}
                    }}

                    // Attraction along edges
                    edges.forEach(edge => {{
                        const source = nodes[edge.source];
                        const target = nodes[edge.target];
                        const dx = target.x - source.x;
                        const dy = target.y - source.y;
                        const dist = Math.sqrt(dx * dx + dy * dy) || 1;

                        const force = dist * attraction;
                        const fx = (dx / dist) * force;
                        const fy = (dy / dist) * force;

                        source.vx += fx;
                        source.vy += fy;
                        target.vx -= fx;
                        target.vy -= fy;
                    }});

                    // Gravity towards center
                    nodes.forEach(node => {{
                        node.vx -= node.x * gravity * 0.01;
                        node.vy -= node.y * gravity * 0.01;
                    }});

                    // Update positions
                    nodes.forEach(node => {{
                        node.x += node.vx;
                        node.y += node.vy;
                    }});
                }}

                // Animate the layout
                function animateForce() {{
                    // Run multiple iterations per frame for speed
                    const iterationsPerFrame = 3;
                    for (let i = 0; i < iterationsPerFrame && forceIterations < maxIterations; i++) {{
                        simulateStep();
                        forceIterations++;
                    }}

                    // Update graph positions
                    nodes.forEach(node => {{
                        graph.setNodeAttribute(node.id, 'x', node.x);
                        graph.setNodeAttribute(node.id, 'y', node.y);
                    }});

                    sigma.refresh();

                    // Continue if not done
                    if (forceIterations < maxIterations && layoutRunning) {{
                        forceAnimationId = requestAnimationFrame(animateForce);
                    }} else {{
                        stopLayoutBtn.style.display = 'none';
                        layoutRunning = false;
                        console.log('Force layout complete after', forceIterations, 'iterations');
                    }}
                }}

                // Start animation
                forceAnimationId = requestAnimationFrame(animateForce);
            }},

            circular: function() {{
                stopForceAnimation();
                // Group nodes by type and arrange in circles
                const nodesByType = {{}};
                graph.forEachNode((nodeId, attrs) => {{
                    const type = attrs.nodeType;
                    if (!nodesByType[type]) nodesByType[type] = [];
                    nodesByType[type].push(nodeId);
                }});

                const typeOrder = ['Organization', 'Person', 'Repository', 'Article', 'Membership', 'Contribution'];
                const sortedTypes = Object.keys(nodesByType).sort((a, b) => {{
                    const aIdx = typeOrder.indexOf(a);
                    const bIdx = typeOrder.indexOf(b);
                    return (aIdx === -1 ? 999 : aIdx) - (bIdx === -1 ? 999 : bIdx);
                }});

                let currentAngle = 0;
                const totalNodes = graph.order;
                const radius = Math.max(200, totalNodes * 15);

                sortedTypes.forEach(type => {{
                    const nodes = nodesByType[type];
                    const anglePerNode = (2 * Math.PI) / totalNodes;

                    nodes.forEach(nodeId => {{
                        const x = radius * Math.cos(currentAngle);
                        const y = radius * Math.sin(currentAngle);
                        graph.setNodeAttribute(nodeId, 'x', x);
                        graph.setNodeAttribute(nodeId, 'y', y);
                        currentAngle += anglePerNode;
                    }});
                }});
            }},

            radial: function() {{
                stopForceAnimation();
                // Hierarchical rings by entity type
                const nodesByType = {{}};
                graph.forEachNode((nodeId, attrs) => {{
                    const type = attrs.nodeType;
                    if (!nodesByType[type]) nodesByType[type] = [];
                    nodesByType[type].push(nodeId);
                }});

                const ringConfig = {{
                    'Organization': {{ ring: 0, radius: 0 }},
                    'Person': {{ ring: 1, radius: 200 }},
                    'Repository': {{ ring: 2, radius: 350 }},
                    'Article': {{ ring: 2, radius: 400 }},
                    'Membership': {{ ring: 3, radius: 500 }},
                    'Contribution': {{ ring: 3, radius: 550 }},
                }};

                Object.entries(nodesByType).forEach(([type, nodes]) => {{
                    const config = ringConfig[type] || {{ ring: 3, radius: 450 }};
                    const count = nodes.length;

                    if (config.radius === 0 && count > 0) {{
                        // Center nodes in a small cluster
                        const clusterRadius = count > 1 ? 50 : 0;
                        nodes.forEach((nodeId, i) => {{
                            const angle = (2 * Math.PI * i / count);
                            graph.setNodeAttribute(nodeId, 'x', clusterRadius * Math.cos(angle));
                            graph.setNodeAttribute(nodeId, 'y', clusterRadius * Math.sin(angle));
                        }});
                    }} else {{
                        nodes.forEach((nodeId, i) => {{
                            const angle = (2 * Math.PI * i / count) - Math.PI / 2;
                            const jitter = (Math.random() - 0.5) * 30;
                            graph.setNodeAttribute(nodeId, 'x', (config.radius + jitter) * Math.cos(angle));
                            graph.setNodeAttribute(nodeId, 'y', (config.radius + jitter) * Math.sin(angle));
                        }});
                    }}
                }});
            }},

            grid: function() {{
                stopForceAnimation();
                const nodes = [];
                graph.forEachNode((nodeId) => nodes.push(nodeId));

                // Sort by type then label
                nodes.sort((a, b) => {{
                    const typeA = graph.getNodeAttribute(a, 'nodeType');
                    const typeB = graph.getNodeAttribute(b, 'nodeType');
                    if (typeA !== typeB) return typeA.localeCompare(typeB);
                    const labelA = graph.getNodeAttribute(a, 'label');
                    const labelB = graph.getNodeAttribute(b, 'label');
                    return labelA.localeCompare(labelB);
                }});

                const cols = Math.ceil(Math.sqrt(nodes.length));
                const spacing = 150;

                nodes.forEach((nodeId, i) => {{
                    const col = i % cols;
                    const row = Math.floor(i / cols);
                    graph.setNodeAttribute(nodeId, 'x', col * spacing - (cols * spacing) / 2);
                    graph.setNodeAttribute(nodeId, 'y', row * spacing - (Math.ceil(nodes.length / cols) * spacing) / 2);
                }});
            }}
        }};

        // Apply layout
        function applyLayout(layoutName) {{
            currentLayout = layoutName;
            if (layouts[layoutName]) {{
                layouts[layoutName]();
                sigma.refresh();
            }}

            // Update active menu item
            document.querySelectorAll('.layout-menu-item').forEach(btn => {{
                btn.classList.toggle('active', btn.dataset.layout === layoutName);
            }});
        }}

        // Layout dropdown toggle
        const layoutMenu = document.getElementById('layout-menu');
        const layoutToggleBtn = document.getElementById('layout-toggle');
        const graphContainer = document.getElementById('graph-container');
        const controlTooltip = document.getElementById('ui-tooltip');
        const CONTROL_TOOLTIP_DELAY_MS = 250;
        let controlTooltipTimer = null;
        let controlTooltipTarget = null;

        function hideControlTooltip() {{
            clearTimeout(controlTooltipTimer);
            controlTooltip.classList.remove('visible');
            controlTooltipTarget = null;
        }}

        function positionControlTooltip(target) {{
            const containerRect = graphContainer.getBoundingClientRect();
            const targetRect = target.getBoundingClientRect();
            const tooltipWidth = controlTooltip.offsetWidth;
            const tooltipHeight = controlTooltip.offsetHeight;

            let left = targetRect.left - containerRect.left - tooltipWidth - 10;
            if (left < 8) {{
                left = targetRect.right - containerRect.left + 10;
            }}

            let top = targetRect.top - containerRect.top + (targetRect.height - tooltipHeight) / 2;
            top = Math.max(8, Math.min(top, containerRect.height - tooltipHeight - 8));

            controlTooltip.style.left = `${{left}}px`;
            controlTooltip.style.top = `${{top}}px`;
        }}

        function scheduleControlTooltip(target) {{
            const tooltipText = target.dataset.tooltip;
            if (!tooltipText) return;

            clearTimeout(controlTooltipTimer);
            controlTooltipTimer = setTimeout(() => {{
                controlTooltip.textContent = tooltipText;
                controlTooltip.classList.add('visible');
                positionControlTooltip(target);
                controlTooltipTarget = target;
            }}, CONTROL_TOOLTIP_DELAY_MS);
        }}

        // Replace native browser tooltips on graph controls with a styled tooltip.
        document.querySelectorAll('#graph-container .control-btn[title], #graph-container .layout-menu-item[title]').forEach(el => {{
            const tooltipText = el.getAttribute('title');
            if (tooltipText) {{
                el.dataset.tooltip = tooltipText;
                el.setAttribute('aria-label', tooltipText);
                el.removeAttribute('title');
            }}
        }});

        document.querySelectorAll('#graph-container .control-btn, #graph-container .layout-menu-item').forEach(el => {{
            el.addEventListener('mouseenter', () => scheduleControlTooltip(el));
            el.addEventListener('mouseleave', hideControlTooltip);
            el.addEventListener('focus', () => scheduleControlTooltip(el));
            el.addEventListener('blur', hideControlTooltip);
            el.addEventListener('click', hideControlTooltip);
        }});

        window.addEventListener('resize', () => {{
            if (controlTooltip.classList.contains('visible') && controlTooltipTarget) {{
                positionControlTooltip(controlTooltipTarget);
            }}
        }});

        layoutToggleBtn.addEventListener('click', (e) => {{
            e.stopPropagation();
            hideControlTooltip();
            layoutMenu.classList.toggle('visible');
        }});

        // Layout menu item handlers
        document.querySelectorAll('.layout-menu-item').forEach(btn => {{
            btn.addEventListener('click', (e) => {{
                e.stopPropagation();
                applyLayout(btn.dataset.layout);
                layoutMenu.classList.remove('visible');
            }});
        }});

        // Close layout menu when clicking elsewhere
        document.addEventListener('click', () => {{
            hideControlTooltip();
            layoutMenu.classList.remove('visible');
        }});

        // Run initial layout
        setTimeout(() => applyLayout('force'), 100);

        // Drag and drop functionality
        let draggedNode = null;
        let isDragging = false;
        let dragModeEnabled = false;
        const dragHint = document.getElementById('drag-hint');
        const toggleDragBtn = document.getElementById('toggle-drag');
        const toggleTooltipBtn = document.getElementById('toggle-tooltip');

        // Toggle drag mode
        toggleDragBtn.addEventListener('click', () => {{
            dragModeEnabled = !dragModeEnabled;
            toggleDragBtn.classList.toggle('active', dragModeEnabled);
            dragHint.classList.toggle('visible', dragModeEnabled);

            // Hide hint after 3 seconds
            if (dragModeEnabled) {{
                setTimeout(() => dragHint.classList.remove('visible'), 3000);
            }}
        }});

        // Toggle tooltip
        toggleTooltipBtn.addEventListener('click', () => {{
            tooltipsEnabled = !tooltipsEnabled;
            toggleTooltipBtn.classList.toggle('active', tooltipsEnabled);
            if (!tooltipsEnabled) {{
                tooltip.classList.remove('visible');
            }}
        }});

        // Toggle table sync
        const toggleSyncBtn = document.getElementById('toggle-sync');
        toggleSyncBtn.addEventListener('click', () => {{
            tableSyncEnabled = !tableSyncEnabled;
            toggleSyncBtn.classList.toggle('active', tableSyncEnabled);
        }});

        // Drag start
        sigma.on('downNode', (e) => {{
            if (!dragModeEnabled) return;
            isDragging = true;
            draggedNode = e.node;
            graph.setNodeAttribute(draggedNode, 'highlighted', true);
            sigma.getCamera().disable();
        }});

        // Drag move
        sigma.getMouseCaptor().on('mousemovebody', (e) => {{
            if (!isDragging || !draggedNode) return;

            // Get new position from mouse
            const pos = sigma.viewportToGraph(e);
            graph.setNodeAttribute(draggedNode, 'x', pos.x);
            graph.setNodeAttribute(draggedNode, 'y', pos.y);

            // Prevent sigma from refreshing too often
            e.preventSigmaDefault();
            e.original.preventDefault();
            e.original.stopPropagation();

            sigma.refresh();
        }});

        // Drag end
        sigma.getMouseCaptor().on('mouseup', () => {{
            if (draggedNode) {{
                graph.removeNodeAttribute(draggedNode, 'highlighted');
            }}
            isDragging = false;
            draggedNode = null;
            sigma.getCamera().enable();
        }});

        // Also handle mouse leaving the container
        sigma.getMouseCaptor().on('mouseleave', () => {{
            if (draggedNode) {{
                graph.removeNodeAttribute(draggedNode, 'highlighted');
            }}
            isDragging = false;
            draggedNode = null;
            sigma.getCamera().enable();
        }});

        const filtersContainer = document.getElementById("filters");
        const edgeLegend = document.getElementById("edge-legend");

        function regroupNodesByType() {{
            nodesByType = {{}};
            (graphData.nodes || []).forEach(node => {{
                if (!nodesByType[node.nodeType]) {{
                    nodesByType[node.nodeType] = [];
                }}
                nodesByType[node.nodeType].push(node);
            }});
        }}

        function updateStats() {{
            document.getElementById("node-count").textContent = graph.order;
            document.getElementById("edge-count").textContent = graph.size;
            const valSpan = document.getElementById("validation-count");
            const valStats = graphData.metadata.validation || {{}};
            if (valStats.total > 0) {{
                const allValid = valStats.invalid === 0;
                valSpan.style.color = allValid ? "#22c55e" : "#ef4444";
                valSpan.style.fontWeight = "600";
                valSpan.textContent = `${{valStats.valid}}/${{valStats.total}} valid`;
            }} else {{
                valSpan.textContent = "no validation";
                valSpan.style.color = "#94a3b8";
                valSpan.style.fontWeight = "500";
            }}
        }}

        function rebuildFilters() {{
            filtersContainer.innerHTML = "";
            const typeCounts = {{}};
            (graphData.nodes || []).forEach(node => {{
                typeCounts[node.nodeType] = (typeCounts[node.nodeType] || 0) + 1;
            }});

            Object.entries(ENTITY_CONFIG).forEach(([type, config]) => {{
                const count = typeCounts[type] || 0;
                if (count === 0) return;

                const item = document.createElement("label");
                item.className = "filter-item";
                item.innerHTML = `
                    <input type="checkbox" class="filter-checkbox" data-type="${{type}}" ${{hiddenTypes.has(type) ? "" : "checked"}}>
                    <span class="filter-color" style="background: ${{config.color}}"></span>
                    <span class="filter-label">${{type}}</span>
                    <span class="filter-count">${{count}}</span>
                `;
                filtersContainer.appendChild(item);
            }});
        }}

        function buildEdgeLegend() {{
            edgeLegend.innerHTML = "";
            Object.entries(EDGE_CONFIG).forEach(([label, config]) => {{
                const item = document.createElement("div");
                item.className = "legend-item";
                item.innerHTML = `
                    <span class="legend-line" style="background: ${{config.color}}"></span>
                    <span>${{label}}</span>
                `;
                edgeLegend.appendChild(item);
            }});
        }}

        function updateVisibility() {{
            graph.forEachNode((nodeId, attrs) => {{
                const hidden = hiddenTypes.has(attrs.nodeType);
                graph.setNodeAttribute(nodeId, "hidden", hidden);
            }});

            graph.forEachEdge((edgeId, attrs, source, target) => {{
                const sourceHidden = graph.getNodeAttribute(source, "hidden");
                const targetHidden = graph.getNodeAttribute(target, "hidden");
                graph.setEdgeAttribute(edgeId, "hidden", sourceHidden || targetHidden);
            }});

            sigma.refresh();
        }}

        // Filter functionality
        filtersContainer.addEventListener("change", (e) => {{
            if (e.target.classList.contains("filter-checkbox")) {{
                const type = e.target.dataset.type;
                if (e.target.checked) {{
                    hiddenTypes.delete(type);
                }} else {{
                    hiddenTypes.add(type);
                }}
                updateVisibility();
            }}
        }});

        // JSON-LD editor elements
        const jsonldEditorWrapper = document.getElementById("jsonld-editor-wrapper");
        const jsonldEditorToggle = document.getElementById("jsonld-editor-toggle");
        const openJsonldEditorBtn = document.getElementById("open-jsonld-editor-btn");
        const editNodeJsonldBtn = document.getElementById("edit-node-jsonld-btn");
        const jsonldApplyBtn = document.getElementById("jsonld-apply-btn");
        const jsonldDownloadBtn = document.getElementById("jsonld-download-btn");
        const jsonldToggleMinimapBtn = document.getElementById("jsonld-toggle-minimap-btn");
        const jsonldStatusEl = document.getElementById("jsonld-editor-status");
        const jsonldTextarea = document.getElementById("jsonld-editor-textarea");
        const jsonldMinimap = document.getElementById("jsonld-minimap");
        const jsonldMinimapMarkers = document.getElementById("jsonld-minimap-markers");
        const jsonldMinimapViewport = document.getElementById("jsonld-minimap-viewport");
        let minimapVisible = false;

        function setJsonldStatus(message, level = "info") {{
            jsonldStatusEl.classList.remove("success", "error");
            if (level === "success") {{
                jsonldStatusEl.classList.add("success");
            }} else if (level === "error") {{
                jsonldStatusEl.classList.add("error");
            }}
            jsonldStatusEl.textContent = message;
        }}

        function getJsonldText() {{
            return jsonldEditor ? jsonldEditor.getValue() : jsonldTextarea.value;
        }}

        function isJsonldEditorOpen() {{
            return !jsonldEditorWrapper.classList.contains("collapsed");
        }}

        function syncJsonldEditorToggleState() {{
            jsonldEditorToggle.textContent = isJsonldEditorOpen() ? "◀" : "▶";
        }}

        function openJsonldEditor() {{
            if (!isJsonldEditorOpen()) {{
                jsonldEditorWrapper.classList.remove("collapsed");
                syncJsonldEditorToggleState();
                setTimeout(() => {{
                    if (jsonldEditor) {{
                        jsonldEditor.refresh();
                        if (selectedNode) {{
                            highlightSourceForNode(selectedNode, true);
                        }}
                        updateMinimapMarkers();
                        updateMinimapViewport();
                    }}
                    sigma.refresh();
                }}, 350);
            }}
        }}

        function closeJsonldEditor() {{
            if (isJsonldEditorOpen()) {{
                jsonldEditorWrapper.classList.add("collapsed");
                syncJsonldEditorToggleState();
                setTimeout(() => sigma.refresh(), 350);
            }}
        }}

        function toggleJsonldEditor() {{
            if (isJsonldEditorOpen()) {{
                closeJsonldEditor();
            }} else {{
                openJsonldEditor();
            }}
        }}

        function setNodeEditButtonState(nodeId) {{
            const enabled = Boolean(nodeId && graph.hasNode(nodeId));
            editNodeJsonldBtn.disabled = !enabled;
            editNodeJsonldBtn.dataset.nodeId = enabled ? nodeId : "";
        }}

        function scrollEditorToLine(line, focus = false) {{
            if (!jsonldEditor) return;
            const maxLine = Math.max(0, jsonldEditor.lineCount() - 1);
            const safeLine = Math.max(0, Math.min(line, maxLine));
            jsonldEditor.scrollIntoView({{ line: safeLine, ch: 0 }}, 120);
            jsonldEditor.setCursor({{ line: safeLine, ch: 0 }});
            if (focus) {{
                jsonldEditor.focus();
            }}
            updateMinimapViewport();
        }}

        function setMinimapButtonState() {{
            jsonldToggleMinimapBtn.classList.toggle("active", minimapVisible);
            jsonldToggleMinimapBtn.textContent = minimapVisible ? "Hide Minimap" : "Show Minimap";
            jsonldToggleMinimapBtn.setAttribute("aria-pressed", minimapVisible ? "true" : "false");
        }}

        function updateMinimapViewport() {{
            if (!jsonldEditor || !minimapVisible) return;
            const scrollInfo = jsonldEditor.getScrollInfo();
            const fullHeight = Math.max(scrollInfo.height, 1);
            const viewHeight = Math.max(scrollInfo.clientHeight, 1);

            if (fullHeight <= viewHeight) {{
                jsonldMinimapViewport.style.top = "0%";
                jsonldMinimapViewport.style.height = "100%";
                return;
            }}

            const topRatio = Math.max(0, Math.min(1, scrollInfo.top / (fullHeight - viewHeight)));
            const heightRatio = Math.max(0.02, Math.min(1, viewHeight / fullHeight));

            jsonldMinimapViewport.style.top = `${{topRatio * 100}}%`;
            jsonldMinimapViewport.style.height = `${{heightRatio * 100}}%`;
        }}

        function updateMinimapMarkers() {{
            if (!minimapVisible) return;
            jsonldMinimapMarkers.innerHTML = "";
            if (!jsonldEditor) return;

            const lineCount = Math.max(1, jsonldEditor.lineCount());
            const markerMap = new Map();

            highlightedSourceLines.forEach(item => {{
                const kind = item.className === "cm-node-line" ? "node" : "ref";
                if (!markerMap.has(item.line) || kind === "node") {{
                    markerMap.set(item.line, kind);
                }}
            }});

            const fragment = document.createDocumentFragment();
            markerMap.forEach((kind, line) => {{
                const marker = document.createElement("div");
                marker.className = `minimap-marker minimap-marker-${{kind}}`;
                marker.style.top = `${{(line / lineCount) * 100}}%`;
                marker.title = `Line ${{line + 1}}`;
                marker.addEventListener("click", (event) => {{
                    event.stopPropagation();
                    scrollEditorToLine(line, true);
                }});
                fragment.appendChild(marker);
            }});
            jsonldMinimapMarkers.appendChild(fragment);
        }}

        function setMinimapVisibility(visible) {{
            minimapVisible = visible;
            jsonldMinimap.classList.toggle("visible", minimapVisible);
            setMinimapButtonState();
            if (jsonldEditor) {{
                setTimeout(() => {{
                    jsonldEditor.refresh();
                    updateMinimapMarkers();
                    updateMinimapViewport();
                }}, 0);
            }}
        }}

        function toggleMinimap() {{
            setMinimapVisibility(!minimapVisible);
        }}

        function buildLineStartOffsets(text) {{
            const offsets = [0];
            for (let i = 0; i < text.length; i += 1) {{
                if (text[i] === "\\n") {{
                    offsets.push(i + 1);
                }}
            }}
            return offsets;
        }}

        function findLineForOffset(offsets, index) {{
            let low = 0;
            let high = offsets.length - 1;
            while (low <= high) {{
                const mid = Math.floor((low + high) / 2);
                if (offsets[mid] <= index) {{
                    low = mid + 1;
                }} else {{
                    high = mid - 1;
                }}
            }}
            return Math.max(0, high);
        }}

        function getLineAndColumnForOffset(text, index) {{
            const offsets = buildLineStartOffsets(text);
            const line = findLineForOffset(offsets, index);
            return {{
                line: line + 1,
                column: index - offsets[line] + 1,
            }};
        }}

        function extractGraphObjectRanges(text) {{
            const graphMatch = /"@graph"\\s*:/.exec(text);
            if (!graphMatch) return [];

            let cursor = graphMatch.index + graphMatch[0].length;
            while (cursor < text.length && /\\s/.test(text[cursor])) {{
                cursor += 1;
            }}
            if (text[cursor] !== "[") return [];

            const arrayStart = cursor;
            let inString = false;
            let escaped = false;
            let bracketDepth = 0;
            let arrayEnd = -1;

            for (let i = arrayStart; i < text.length; i += 1) {{
                const ch = text[i];
                if (inString) {{
                    if (escaped) {{
                        escaped = false;
                    }} else if (ch === "\\\\") {{
                        escaped = true;
                    }} else if (ch === '"') {{
                        inString = false;
                    }}
                    continue;
                }}

                if (ch === '"') {{
                    inString = true;
                }} else if (ch === "[") {{
                    bracketDepth += 1;
                }} else if (ch === "]") {{
                    bracketDepth -= 1;
                    if (bracketDepth === 0) {{
                        arrayEnd = i;
                        break;
                    }}
                }}
            }}

            if (arrayEnd < 0) return [];

            const ranges = [];
            inString = false;
            escaped = false;
            let braceDepth = 0;
            let objectStart = -1;

            for (let i = arrayStart + 1; i < arrayEnd; i += 1) {{
                const ch = text[i];
                if (inString) {{
                    if (escaped) {{
                        escaped = false;
                    }} else if (ch === "\\\\") {{
                        escaped = true;
                    }} else if (ch === '"') {{
                        inString = false;
                    }}
                    continue;
                }}

                if (ch === '"') {{
                    inString = true;
                }} else if (ch === "{{") {{
                    if (braceDepth === 0) {{
                        objectStart = i;
                    }}
                    braceDepth += 1;
                }} else if (ch === "}}") {{
                    braceDepth -= 1;
                    if (braceDepth === 0 && objectStart >= 0) {{
                        ranges.push({{ start: objectStart, end: i }});
                        objectStart = -1;
                    }}
                }}
            }}

            return ranges;
        }}

        function buildSourceIndexFromText(text) {{
            const lineOffsets = buildLineStartOffsets(text);
            const lines = text.split(/\\r?\\n/);
            const ranges = extractGraphObjectRanges(text);
            const index = {{}};

            ranges.forEach(range => {{
                const blockText = text.slice(range.start, range.end + 1);
                const idMatch = /"@id"\\s*:\\s*"([^"\\\\]*(?:\\\\.[^"\\\\]*)*)"/.exec(blockText);
                if (!idMatch) return;
                let nodeId = idMatch[1];
                try {{
                    nodeId = JSON.parse(`"${{idMatch[1]}}"`);
                }} catch (err) {{
                    nodeId = idMatch[1];
                }}

                const startLine = findLineForOffset(lineOffsets, range.start);
                const endLine = findLineForOffset(lineOffsets, range.end);
                index[nodeId] = {{
                    block: {{ startLine, endLine }},
                    referenceLines: [],
                    allLines: [],
                }};
            }});

            Object.entries(index).forEach(([nodeId, entry]) => {{
                const quotedId = JSON.stringify(nodeId);
                const references = [];

                lines.forEach((line, lineNumber) => {{
                    if (line.includes(quotedId)) {{
                        references.push(lineNumber);
                    }}
                }});

                const all = new Set(references);
                for (let line = entry.block.startLine; line <= entry.block.endLine; line += 1) {{
                    all.add(line);
                }}

                entry.referenceLines = references.filter(
                    line => line < entry.block.startLine || line > entry.block.endLine,
                );
                entry.allLines = Array.from(all).sort((a, b) => a - b);
            }});

            return index;
        }}

        function clearSourceHighlights() {{
            if (!jsonldEditor) return;
            highlightedSourceLines.forEach(item => {{
                jsonldEditor.removeLineClass(item.line, "background", item.className);
            }});
            highlightedSourceLines = [];
            updateMinimapMarkers();
        }}

        function highlightSourceForNode(nodeId, scrollToFirst = true, suppressStatus = false) {{
            if (!jsonldEditor || !nodeId) return;
            clearSourceHighlights();

            const entry = sourceIndex[nodeId];
            if (!entry) {{
                if (!suppressStatus) {{
                    setJsonldStatus(`No JSON-LD block found for node "${{nodeId}}".`, "error");
                }}
                return;
            }}

            for (let line = entry.block.startLine; line <= entry.block.endLine; line += 1) {{
                jsonldEditor.addLineClass(line, "background", "cm-node-line");
                highlightedSourceLines.push({{ line, className: "cm-node-line" }});
            }}

            entry.referenceLines.forEach(line => {{
                if (line < entry.block.startLine || line > entry.block.endLine) {{
                    jsonldEditor.addLineClass(line, "background", "cm-node-ref-line");
                    highlightedSourceLines.push({{ line, className: "cm-node-ref-line" }});
                }}
            }});

            if (scrollToFirst) {{
                scrollEditorToLine(entry.block.startLine, true);
            }} else {{
                updateMinimapViewport();
            }}

            updateMinimapMarkers();

            if (!suppressStatus) {{
                setJsonldStatus(
                    `Highlighted ${{entry.block.endLine - entry.block.startLine + 1}} node lines and ${{entry.referenceLines.length}} reference lines for "${{nodeId}}".`,
                    "info",
                );
            }}
        }}

        function highlightSelectedNodeSource(scrollToFirst = true) {{
            if (!selectedNode || !isJsonldEditorOpen()) return;
            highlightSourceForNode(selectedNode, scrollToFirst);
        }}

        function getEntityTypeKey(node) {{
            const nodeType = node["@type"] || "";
            return ENTITY_TYPE_CONFIG[nodeType] ? nodeType : "schema:Person";
        }}

        function extractNodeLabel(node) {{
            if (node["schema:name"]) return node["schema:name"];
            const nodeId = node["@id"] || "unknown";
            if (nodeId.includes("/")) {{
                return nodeId.split("/").slice(-1)[0];
            }}
            return nodeId;
        }}

        function buildIdIndex(graphNodes) {{
            const index = {{}};
            const identifierFields = [
                "pulse:orcidIdentifier",
                "pulse:orcid",
                "pulse:githubUsername",
                "pulse:githubRepositoryHandle",
                "pulse:githubOrganizationHandle",
                "pulse:infosciencePersonIdentifier",
                "pulse:infoscienceOrganizationIdentifier",
                "pulse:ror",
                "schema:identifier",
                "pulse:composite",
                "uuid",
            ];

            graphNodes.forEach(node => {{
                const nodeId = node["@id"] || "";
                if (!nodeId) return;
                index[nodeId] = node;

                if (nodeId.includes("/")) {{
                    const shortId = nodeId.split("/").slice(-1)[0];
                    if (!index[shortId]) {{
                        index[shortId] = node;
                    }}
                }}

                identifierFields.forEach(field => {{
                    const value = node[field];
                    if (typeof value === "string" && !index[value]) {{
                        index[value] = node;
                    }}
                }});
            }});

            return index;
        }}

        function extractEdges(graphNodes, idIndex) {{
            const edges = [];
            const edgeSet = new Set();
            const directedPairs = new Set();

            graphNodes.forEach(node => {{
                const sourceId = node["@id"] || "";
                CROSS_REF_FIELDS.forEach(field => {{
                    let values = node[field];
                    if (values === undefined || values === null) return;
                    if (!Array.isArray(values)) values = [values];

                    values.forEach(targetRef => {{
                        if (targetRef === null || targetRef === undefined) return;
                        const targetNode = idIndex[targetRef];
                        if (!targetNode) return;

                        const targetId = targetNode["@id"] || "";
                        const edgeKey = `${{sourceId}}|${{targetId}}|${{field}}`;
                        if (edgeSet.has(edgeKey)) return;
                        edgeSet.add(edgeKey);
                        directedPairs.add(`${{sourceId}}|${{targetId}}`);

                        const edgeConfig = EDGE_TYPE_CONFIG[field] || {{
                            label: field,
                            color: "#94a3b8",
                        }};

                        edges.push({{
                            source: sourceId,
                            target: targetId,
                            label: edgeConfig.label,
                            color: edgeConfig.color,
                            type: field,
                        }});
                    }});
                }});
            }});

            edges.forEach(edge => {{
                const reverseKey = `${{edge.target}}|${{edge.source}}`;
                edge.curvature = directedPairs.has(reverseKey) ? 0.3 : 0;
            }});
            return edges;
        }}

        function calculateRadialPositions(nodesByTypeKey) {{
            const positions = {{}};
            const ringRadius = {{ 0: 0, 1: 150, 2: 300, 3: 450 }};
            const nodesByRing = {{}};

            Object.entries(nodesByTypeKey).forEach(([entityType, nodes]) => {{
                const config = ENTITY_TYPE_CONFIG[entityType] || ENTITY_TYPE_CONFIG["schema:Person"];
                const ring = config.ring;
                if (!nodesByRing[ring]) nodesByRing[ring] = [];
                nodes.forEach(node => nodesByRing[ring].push(node));
            }});

            Object.entries(nodesByRing).forEach(([ringKey, ringNodes]) => {{
                const ring = Number(ringKey);
                const radius = ringRadius[ring] ?? 450;
                const count = ringNodes.length;
                if (count === 0) return;

                if (ring === 0 && count === 1) {{
                    positions[ringNodes[0]["@id"]] = {{ x: 0, y: 0 }};
                    return;
                }}

                ringNodes.forEach((node, idx) => {{
                    const angle = (2 * Math.PI * idx / count) - (Math.PI / 2);
                    positions[node["@id"]] = {{
                        x: radius * Math.cos(angle),
                        y: radius * Math.sin(angle),
                    }};
                }});
            }});
            return positions;
        }}

        function matchValidation(nodeId, validationLookup) {{
            if (validationLookup[nodeId]) return validationLookup[nodeId];
            if (nodeId.includes("/")) {{
                const shortId = nodeId.split("/").slice(-1)[0];
                if (validationLookup[shortId]) return validationLookup[shortId];
            }}
            return null;
        }}

        function buildGraphDataFromJsonld(jsonld, validationLookup) {{
            const graphNodes = jsonld["@graph"];
            if (!Array.isArray(graphNodes)) {{
                throw new Error('Edited JSON-LD must include an "@graph" array.');
            }}

            const idIndex = buildIdIndex(graphNodes);
            const nodesByTypeKey = {{}};
            graphNodes.forEach(node => {{
                const nodeType = node["@type"];
                if (ENTITY_TYPE_CONFIG[nodeType]) {{
                    if (!nodesByTypeKey[nodeType]) nodesByTypeKey[nodeType] = [];
                    nodesByTypeKey[nodeType].push(node);
                }}
            }});

            const positions = calculateRadialPositions(nodesByTypeKey);
            const edges = extractEdges(graphNodes, idIndex);
            const connectionCounts = {{}};
            edges.forEach(edge => {{
                connectionCounts[edge.source] = (connectionCounts[edge.source] || 0) + 1;
                connectionCounts[edge.target] = (connectionCounts[edge.target] || 0) + 1;
            }});

            let validCount = 0;
            let invalidCount = 0;
            let noValidationCount = 0;
            const nodes = [];

            graphNodes.forEach(node => {{
                const nodeId = node["@id"] || "";
                const nodeType = node["@type"] || "";
                if (!ENTITY_TYPE_CONFIG[nodeType]) return;

                const config = ENTITY_TYPE_CONFIG[nodeType];
                const pos = positions[nodeId] || {{ x: 0, y: 0 }};
                const connCount = connectionCounts[nodeId] || 0;
                const size = config.size_base + Math.min(connCount * 2, 10);

                const properties = {{}};
                Object.entries(node).forEach(([key, value]) => {{
                    if ((key === "@id" || key === "@type") || value === null || value === undefined) {{
                        return;
                    }}
                    const displayKey = key.includes(":") ? key.split(":").slice(-1)[0] : key;
                    properties[displayKey] = value;
                }});

                const val = matchValidation(nodeId, validationLookup);
                let validation = null;
                if (val) {{
                    validation = {{
                        strict: Boolean(val.strict),
                        agent: Boolean(val.agent),
                        strict_errors: val.strict_errors || [],
                        agent_errors: val.agent_errors || [],
                        shape: val.shape || "",
                    }};
                    if (validation.strict && validation.agent) {{
                        validCount += 1;
                    }} else {{
                        invalidCount += 1;
                    }}
                }} else {{
                    noValidationCount += 1;
                }}

                nodes.push({{
                    id: nodeId,
                    label: extractNodeLabel(node),
                    x: pos.x,
                    y: pos.y,
                    size,
                    color: config.color,
                    nodeType: config.label,
                    entityType: nodeType,
                    properties,
                    validation,
                }});
            }});

            return {{
                nodes,
                edges,
                metadata: {{
                    generated: new Date().toISOString(),
                    nodeCount: nodes.length,
                    edgeCount: edges.length,
                    entityTypes: Object.keys(ENTITY_TYPE_CONFIG),
                    validation: {{
                        valid: validCount,
                        invalid: invalidCount,
                        noData: noValidationCount,
                        total: validCount + invalidCount + noValidationCount,
                    }},
                }},
            }};
        }}

        function applyGraphData(nextGraphData, preferredTab = null) {{
            graphData = nextGraphData;
            addGraphDataToGraph(graphData);
            rebuildSidebarAndTables(preferredTab);
            applyLayout(currentLayout);

            if (selectedNode && graph.hasNode(selectedNode)) {{
                selectNode(selectedNode);
                if (tableSyncEnabled) {{
                    const nodeType = graph.getNodeAttribute(selectedNode, "nodeType");
                    switchTab(nodeType);
                    highlightTableRow(selectedNode);
                }}
            }} else {{
                selectedNode = null;
                hideNodeDetails();
                clearTableSelection();
                clearSourceHighlights();
            }}
        }}

        function applyJsonldChanges() {{
            const text = getJsonldText();
            let parsed;
            try {{
                parsed = JSON.parse(text);
            }} catch (err) {{
                const msg = err && err.message ? err.message : "Invalid JSON.";
                const posMatch = /position\\s+(\\d+)/i.exec(msg);
                if (posMatch) {{
                    const pos = Number(posMatch[1]);
                    const lc = getLineAndColumnForOffset(text, pos);
                    setJsonldStatus(`Invalid JSON at line ${{lc.line}}, column ${{lc.column}}: ${{msg}}`, "error");
                }} else {{
                    setJsonldStatus(`Invalid JSON: ${{msg}}`, "error");
                }}
                return;
            }}

            try {{
                const nextGraphData = buildGraphDataFromJsonld(parsed, validationLookupById);
                sourceIndex = buildSourceIndexFromText(text);
                applyGraphData(nextGraphData, activeDataTab);
                if (selectedNode && isJsonldEditorOpen()) {{
                    highlightSourceForNode(selectedNode, false);
                }}
                setJsonldStatus(
                    `Applied JSON-LD successfully: ${{nextGraphData.metadata.nodeCount}} nodes, ${{nextGraphData.metadata.edgeCount}} edges.`,
                    "success",
                );
            }} catch (err) {{
                const message = err && err.message ? err.message : "Failed to apply JSON-LD.";
                setJsonldStatus(message, "error");
            }}
        }}

        function downloadJsonldText() {{
            const blob = new Blob([getJsonldText()], {{ type: "application/ld+json;charset=utf-8" }});
            const url = URL.createObjectURL(blob);
            const link = document.createElement("a");
            link.href = url;
            link.download = "jsonld_output.edited.json";
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(() => URL.revokeObjectURL(url), 0);
            setJsonldStatus("Downloaded edited JSON-LD file.", "success");
        }}

        function initJsonldEditor() {{
            if (typeof CodeMirror === "undefined") {{
                jsonldTextarea.value = INITIAL_JSONLD_TEXT;
                sourceIndex = buildSourceIndexFromText(INITIAL_JSONLD_TEXT);
                jsonldToggleMinimapBtn.disabled = true;
                setMinimapVisibility(false);
                setJsonldStatus("CodeMirror failed to load; using plain textarea editor.", "error");
                return;
            }}

            jsonldEditor = CodeMirror.fromTextArea(jsonldTextarea, {{
                mode: {{ name: "javascript", json: true }},
                lineNumbers: true,
                lineWrapping: false,
                tabSize: 2,
                viewportMargin: 20,
            }});
            jsonldEditor.setValue(INITIAL_JSONLD_TEXT);
            sourceIndex = buildSourceIndexFromText(INITIAL_JSONLD_TEXT);
            jsonldEditor.on("changes", () => {{
                sourceIndex = buildSourceIndexFromText(jsonldEditor.getValue());
                if (selectedNode && highlightedSourceLines.length > 0 && isJsonldEditorOpen()) {{
                    highlightSourceForNode(selectedNode, false, true);
                }} else {{
                    updateMinimapMarkers();
                }}
                updateMinimapViewport();
            }});
            jsonldEditor.on("scroll", () => {{
                updateMinimapViewport();
            }});
            setMinimapButtonState();
            setJsonldStatus("Editor ready. Select a node and click 'Edit in JSON-LD'.", "info");
        }}

        jsonldEditorToggle.addEventListener("click", () => {{
            toggleJsonldEditor();
        }});

        openJsonldEditorBtn.addEventListener("click", () => {{
            openJsonldEditor();
            if (selectedNode) {{
                highlightSourceForNode(selectedNode, true);
            }}
        }});

        editNodeJsonldBtn.addEventListener("click", () => {{
            const nodeId = editNodeJsonldBtn.dataset.nodeId;
            if (!nodeId) return;
            openJsonldEditor();
            highlightSourceForNode(nodeId, true);
        }});

        jsonldApplyBtn.addEventListener("click", () => {{
            applyJsonldChanges();
        }});

        jsonldDownloadBtn.addEventListener("click", () => {{
            downloadJsonldText();
        }});

        jsonldToggleMinimapBtn.addEventListener("click", () => {{
            toggleMinimap();
        }});

        jsonldMinimap.addEventListener("click", (event) => {{
            if (!jsonldEditor || !minimapVisible) return;
            const rect = jsonldMinimap.getBoundingClientRect();
            if (!rect.height) return;
            const ratio = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
            const line = Math.floor(ratio * Math.max(0, jsonldEditor.lineCount() - 1));
            scrollEditorToLine(line, true);
        }});

        // Search functionality
        const searchInput = document.getElementById("search-input");
        const searchResults = document.getElementById("search-results");

        searchInput.addEventListener("input", (e) => {{
            const query = e.target.value.toLowerCase().trim();
            searchResults.innerHTML = "";

            if (query.length < 2) return;

            const matches = [];
            graph.forEachNode((nodeId, attrs) => {{
                if (attrs.label.toLowerCase().includes(query) || nodeId.toLowerCase().includes(query)) {{
                    matches.push({{ id: nodeId, label: attrs.label, color: attrs.color, nodeType: attrs.nodeType }});
                }}
            }});

            matches.slice(0, 10).forEach(match => {{
                const div = document.createElement("div");
                div.className = "search-result";
                div.innerHTML = `
                    <span class="search-result-dot" style="background: ${{match.color}}"></span>
                    <span>${{match.label}}</span>
                `;
                div.addEventListener("click", () => {{
                    selectNode(match.id);
                    focusNode(match.id);
                    searchInput.value = "";
                    searchResults.innerHTML = "";
                }});
                searchResults.appendChild(div);
            }});
        }});

        // Highlight node and its connections (for hover)
        // When a node is selected, this adds the hovered node's connections to the highlight
        function highlightNodeConnections(nodeId, combineWithSelection = false) {{
            const hoverNodes = new Set();
            const hoverEdges = new Set();

            hoverNodes.add(nodeId);

            // Find connected nodes for hovered node
            graph.forEachEdge(nodeId, (edgeId, attrs, source, target) => {{
                hoverEdges.add(edgeId);
                hoverNodes.add(source);
                hoverNodes.add(target);
            }});

            // Combine with selected node's connections if needed
            const combinedNodes = new Set([...hoverNodes]);
            const combinedEdges = new Set([...hoverEdges]);

            if (combineWithSelection && selectedNode) {{
                combinedNodes.add(selectedNode);
                graph.forEachEdge(selectedNode, (edgeId, attrs, source, target) => {{
                    combinedEdges.add(edgeId);
                    combinedNodes.add(source);
                    combinedNodes.add(target);
                }});
            }}

            // Update node colors
            graph.forEachNode((id, attrs) => {{
                if (combinedNodes.has(id)) {{
                    graph.setNodeAttribute(id, "color", attrs.originalColor);
                    graph.setNodeAttribute(id, "zIndex", 1);
                }} else {{
                    graph.setNodeAttribute(id, "color", "#d1d5db");
                    graph.setNodeAttribute(id, "zIndex", 0);
                }}
            }});

            // Update edge colors
            graph.forEachEdge((id, attrs) => {{
                if (combinedEdges.has(id)) {{
                    graph.setEdgeAttribute(id, "color", attrs.originalColor);
                    graph.setEdgeAttribute(id, "size", 3);
                    graph.setEdgeAttribute(id, "zIndex", 1);
                }} else {{
                    graph.setEdgeAttribute(id, "color", "#e5e7eb");
                    graph.setEdgeAttribute(id, "size", 1);
                    graph.setEdgeAttribute(id, "zIndex", 0);
                }}
            }});

            sigma.refresh();
        }}

        // Restore selection highlight (when leaving a hovered node while one is selected)
        function restoreSelectionHighlight() {{
            if (!selectedNode) return;

            highlightedNodes.clear();
            highlightedEdges.clear();
            highlightedNodes.add(selectedNode);

            graph.forEachEdge(selectedNode, (edgeId, attrs, source, target) => {{
                highlightedEdges.add(edgeId);
                highlightedNodes.add(source);
                highlightedNodes.add(target);
            }});

            graph.forEachNode((id, attrs) => {{
                if (highlightedNodes.has(id)) {{
                    graph.setNodeAttribute(id, "color", attrs.originalColor);
                    graph.setNodeAttribute(id, "zIndex", 1);
                }} else {{
                    graph.setNodeAttribute(id, "color", "#d1d5db");
                    graph.setNodeAttribute(id, "zIndex", 0);
                }}
            }});

            graph.forEachEdge((id, attrs) => {{
                if (highlightedEdges.has(id)) {{
                    graph.setEdgeAttribute(id, "color", attrs.originalColor);
                    graph.setEdgeAttribute(id, "size", 3);
                    graph.setEdgeAttribute(id, "zIndex", 1);
                }} else {{
                    graph.setEdgeAttribute(id, "color", "#e5e7eb");
                    graph.setEdgeAttribute(id, "size", 1);
                    graph.setEdgeAttribute(id, "zIndex", 0);
                }}
            }});

            sigma.refresh();
        }}

        // Node selection and highlighting
        function selectNode(nodeId) {{
            if (!graph.hasNode(nodeId)) return;

            // Reset previous selection
            resetHighlight();

            selectedNode = nodeId;
            highlightedNodes.add(nodeId);

            // Find connected nodes
            graph.forEachEdge(nodeId, (edgeId, attrs, source, target) => {{
                highlightedEdges.add(edgeId);
                highlightedNodes.add(source);
                highlightedNodes.add(target);
            }});

            // Update node colors
            graph.forEachNode((id, attrs) => {{
                if (highlightedNodes.has(id)) {{
                    graph.setNodeAttribute(id, "color", attrs.originalColor);
                    graph.setNodeAttribute(id, "zIndex", 1);
                }} else {{
                    graph.setNodeAttribute(id, "color", "#d1d5db");
                    graph.setNodeAttribute(id, "zIndex", 0);
                }}
            }});

            // Update edge colors
            graph.forEachEdge((id, attrs) => {{
                if (highlightedEdges.has(id)) {{
                    graph.setEdgeAttribute(id, "color", attrs.originalColor);
                    graph.setEdgeAttribute(id, "size", 3);
                    graph.setEdgeAttribute(id, "zIndex", 1);
                }} else {{
                    graph.setEdgeAttribute(id, "color", "#e5e7eb");
                    graph.setEdgeAttribute(id, "size", 1);
                    graph.setEdgeAttribute(id, "zIndex", 0);
                }}
            }});

            // Show node details
            showNodeDetails(nodeId);
            setNodeEditButtonState(nodeId);
            if (isJsonldEditorOpen()) {{
                highlightSelectedNodeSource();
            }}

            sigma.refresh();
        }}

        function resetHighlight() {{
            selectedNode = null;
            highlightedNodes.clear();
            highlightedEdges.clear();

            graph.forEachNode((id, attrs) => {{
                graph.setNodeAttribute(id, "color", attrs.originalColor);
                graph.setNodeAttribute(id, "zIndex", 0);
            }});

            graph.forEachEdge((id, attrs) => {{
                graph.setEdgeAttribute(id, "color", attrs.originalColor);
                graph.setEdgeAttribute(id, "size", 2);
                graph.setEdgeAttribute(id, "zIndex", 0);
            }});

            setNodeEditButtonState(null);
            clearSourceHighlights();
            sigma.refresh();
        }}

        function focusNode(nodeId) {{
            const nodePosition = sigma.getNodeDisplayData(nodeId);
            if (nodePosition) {{
                sigma.getCamera().animate(
                    {{ x: nodePosition.x, y: nodePosition.y, ratio: 0.3 }},
                    {{ duration: 500 }}
                );
            }}
        }}

        // Node details panel
        function showNodeDetails(nodeId) {{
            const attrs = graph.getNodeAttributes(nodeId);

            document.getElementById("details-placeholder").style.display = "none";
            const detailsDiv = document.getElementById("node-details");
            detailsDiv.classList.add("active");

            document.getElementById("node-title").textContent = attrs.label;
            const typeSpan = document.getElementById("node-type");
            typeSpan.textContent = attrs.nodeType;
            typeSpan.style.background = attrs.originalColor + "20";
            typeSpan.style.color = attrs.originalColor;
            setNodeEditButtonState(nodeId);

            const propsDiv = document.getElementById("node-properties");
            propsDiv.innerHTML = "";

            // Validation section
            if (attrs.validation) {{
                const v = attrs.validation;
                const strictOk = v.strict;
                const agentOk = v.agent;
                const allOk = strictOk && agentOk;
                const valDiv = document.createElement("div");
                valDiv.style.cssText = `padding: 10px; margin-bottom: 12px; border-radius: 8px; background: ${{allOk ? "#f0fdf4" : "#fef2f2"}}; border: 1px solid ${{allOk ? "#bbf7d0" : "#fecaca"}};`;
                let valHtml = `<div style="font-weight: 600; font-size: 13px; margin-bottom: 6px; color: ${{allOk ? "#166534" : "#991b1b"}};">${{allOk ? "✓ Validation Passed" : "✗ Validation Failed"}}</div>`;
                valHtml += `<div style="font-size: 12px;"><span style="color: ${{strictOk ? "#22c55e" : "#ef4444"}};">Strict: ${{strictOk ? "Pass" : "Fail"}}</span> · <span style="color: ${{agentOk ? "#22c55e" : "#ef4444"}};">Agent: ${{agentOk ? "Pass" : "Fail"}}</span></div>`;
                if (v.shape) {{
                    valHtml += `<div style="font-size: 11px; color: #64748b; margin-top: 4px;">Shape: ${{v.shape}}</div>`;
                }}
                const allErrors = [...(v.strict_errors || []), ...(v.agent_errors || [])];
                if (allErrors.length > 0) {{
                    valHtml += `<div style="font-size: 12px; color: #991b1b; margin-top: 8px; font-weight: 500;">Errors (${{allErrors.length}}):</div>`;
                    allErrors.forEach(err => {{
                        const msg = typeof err === "string" ? err : (err.message || JSON.stringify(err));
                        valHtml += `<div style="font-size: 11px; color: #991b1b; padding: 2px 0;">&bull; ${{msg}}</div>`;
                    }});
                }}
                valDiv.innerHTML = valHtml;
                propsDiv.appendChild(valDiv);
            }} else if (attrs.validation === null) {{
                const valDiv = document.createElement("div");
                valDiv.style.cssText = "padding: 10px; margin-bottom: 12px; border-radius: 8px; background: #f8fafc; border: 1px solid #e2e8f0;";
                valDiv.innerHTML = '<div style="font-size: 12px; color: #94a3b8;">No validation data available</div>';
                propsDiv.appendChild(valDiv);
            }}

            // Add ID
            addPropertyItem(propsDiv, "ID", nodeId);

            // Add other properties
            if (attrs.properties) {{
                Object.entries(attrs.properties).forEach(([key, value]) => {{
                    if (Array.isArray(value)) {{
                        value = value.join(", ");
                    }} else if (typeof value === "object") {{
                        value = JSON.stringify(value);
                    }}
                    addPropertyItem(propsDiv, key, value);
                }});
            }}
        }}

        function addPropertyItem(container, key, value) {{
            const item = document.createElement("div");
            item.className = "property-item";

            let valueHtml = String(value);
            if (typeof value === "string" && (value.startsWith("http") || value.startsWith("https"))) {{
                valueHtml = `<a href="${{value}}" target="_blank">${{value}}</a>`;
            }}

            item.innerHTML = `
                <div class="property-key">${{key}}</div>
                <div class="property-value">${{valueHtml}}</div>
            `;
            container.appendChild(item);
        }}

        function hideNodeDetails() {{
            document.getElementById("details-placeholder").style.display = "block";
            document.getElementById("node-details").classList.remove("active");
            setNodeEditButtonState(null);
        }}

        // Sigma event handlers
        sigma.on("clickNode", ({{ node }}) => {{
            selectNode(node);

            // Auto-expand data panel, switch tab, and highlight row (if sync enabled)
            if (tableSyncEnabled) {{
                const nodeType = graph.getNodeAttribute(node, "nodeType");
                if (dataPanel.classList.contains("collapsed")) {{
                    dataPanel.classList.remove("collapsed");
                }}
                switchTab(nodeType);
                highlightTableRow(node);
            }}
        }});

        sigma.on("clickStage", () => {{
            resetHighlight();
            hideNodeDetails();
            clearTableSelection();
        }});

        // Tooltip
        const tooltip = document.getElementById("tooltip");
        const tooltipProps = document.getElementById("tooltip-properties");

        // Properties to hide in tooltips
        const hiddenProps = new Set(['uuid', 'composite', 'originalColor', 'originalSize']);

        sigma.on("enterNode", ({{ node }}) => {{
            const attrs = graph.getNodeAttributes(node);

            // Show tooltip only if enabled
            if (tooltipsEnabled) {{
                document.getElementById("tooltip-title").textContent = attrs.label;

                const typeSpan = document.getElementById("tooltip-type");
                typeSpan.textContent = attrs.nodeType;
                typeSpan.style.background = attrs.color + "30";
                typeSpan.style.color = attrs.color;

                document.getElementById("tooltip-edge").textContent = "";

                // Build properties HTML
                let propsHtml = "";

                // Validation status line
                if (attrs.validation) {{
                    const v = attrs.validation;
                    const strictIcon = v.strict ? "✓" : "✗";
                    const agentIcon = v.agent ? "✓" : "✗";
                    const strictColor = v.strict ? "#22c55e" : "#ef4444";
                    const agentColor = v.agent ? "#22c55e" : "#ef4444";
                    propsHtml += `<div class="tooltip-prop"><span class="tooltip-prop-key">Validation:</span><span class="tooltip-prop-value"><span style="color:${{strictColor}}">Strict ${{strictIcon}}</span> · <span style="color:${{agentColor}}">Agent ${{agentIcon}}</span></span></div>`;
                    const allErrors = [...(v.strict_errors || []), ...(v.agent_errors || [])];
                    if (allErrors.length > 0) {{
                        allErrors.slice(0, 3).forEach(err => {{
                            const msg = typeof err === "string" ? err : (err.message || JSON.stringify(err));
                            propsHtml += `<div class="tooltip-prop"><span class="tooltip-prop-key" style="color:#ef4444">Error:</span><span class="tooltip-prop-value" style="color:#ef4444">${{msg.slice(0, 60)}}</span></div>`;
                        }});
                    }}
                }} else if (attrs.validation === null) {{
                    propsHtml += `<div class="tooltip-prop"><span class="tooltip-prop-key">Validation:</span><span class="tooltip-prop-value" style="color:#94a3b8">No data</span></div>`;
                }}

                if (attrs.properties) {{
                    const propEntries = Object.entries(attrs.properties)
                        .filter(([key]) => !hiddenProps.has(key))
                        .slice(0, 8); // Limit to 8 properties

                    propEntries.forEach(([key, value]) => {{
                        let displayValue = value;
                        if (Array.isArray(value)) {{
                            displayValue = value.slice(0, 3).join(", ");
                            if (value.length > 3) displayValue += "...";
                        }} else if (typeof value === "object") {{
                            displayValue = JSON.stringify(value).slice(0, 50);
                        }} else if (typeof value === "string" && value.length > 40) {{
                            displayValue = value.slice(0, 40) + "...";
                        }}
                        propsHtml += `<div class="tooltip-prop"><span class="tooltip-prop-key">${{key}}:</span><span class="tooltip-prop-value">${{displayValue}}</span></div>`;
                    }});

                    if (Object.keys(attrs.properties).length > 8) {{
                        propsHtml += `<div class="tooltip-prop" style="color: #94a3b8; font-style: italic;">...and ${{Object.keys(attrs.properties).length - 8}} more</div>`;
                    }}
                }}
                tooltipProps.innerHTML = propsHtml;
                tooltipProps.style.display = propsHtml ? "block" : "none";

                tooltip.classList.add("visible");
            }}

            // Highlight hovered node and connections (always, regardless of tooltip)
            hoveredNode = node;
            if (selectedNode) {{
                // Combine hovered node connections with selected node connections
                highlightNodeConnections(node, true);
            }} else {{
                // Just highlight hovered node connections
                highlightNodeConnections(node, false);
            }}
        }});

        sigma.on("enterEdge", ({{ edge }}) => {{
            if (!tooltipsEnabled) return;
            const attrs = graph.getEdgeAttributes(edge);
            const source = graph.getSourceAttribute(edge, "label");
            const target = graph.getTargetAttribute(edge, "label");
            document.getElementById("tooltip-title").textContent = attrs.label;
            document.getElementById("tooltip-type").textContent = "";
            document.getElementById("tooltip-type").style.background = "";
            document.getElementById("tooltip-edge").textContent = `${{source}} → ${{target}}`;
            tooltipProps.innerHTML = "";
            tooltipProps.style.display = "none";
            tooltip.classList.add("visible");
        }});

        sigma.on("leaveNode", () => {{
            tooltip.classList.remove("visible");
            hoveredNode = null;
            // If a node is selected, restore its highlight; otherwise reset all
            if (selectedNode) {{
                restoreSelectionHighlight();
            }} else {{
                resetHighlight();
            }}
        }});

        sigma.on("leaveEdge", () => {{
            tooltip.classList.remove("visible");
        }});

        // Update tooltip position
        sigma.getMouseCaptor().on("mousemove", (e) => {{
            tooltip.style.left = e.x + 15 + "px";
            tooltip.style.top = e.y + 15 + "px";
        }});

        // Control buttons
        document.getElementById("zoom-in").addEventListener("click", () => {{
            const camera = sigma.getCamera();
            camera.animatedZoom({{ duration: 300 }});
        }});

        document.getElementById("zoom-out").addEventListener("click", () => {{
            const camera = sigma.getCamera();
            camera.animatedUnzoom({{ duration: 300 }});
        }});

        document.getElementById("zoom-fit").addEventListener("click", () => {{
            const camera = sigma.getCamera();
            camera.animatedReset({{ duration: 300 }});
        }});

        document.getElementById("reset-view").addEventListener("click", () => {{
            resetHighlight();
            hideNodeDetails();
            clearTableSelection();
            hiddenTypes.clear();
            document.querySelectorAll(".filter-checkbox").forEach(cb => cb.checked = true);
            updateVisibility();
            // Re-apply current layout
            applyLayout(currentLayout);
            sigma.getCamera().animatedReset({{ duration: 300 }});
        }});

        // Stop layout animation button
        stopLayoutBtn.addEventListener("click", () => {{
            stopForceAnimation();
        }});

        // Sidebar toggle
        const sidebar = document.getElementById("sidebar");
        const sidebarToggle = document.getElementById("sidebar-toggle");
        sidebarToggle.addEventListener("click", () => {{
            sidebar.classList.toggle("collapsed");
            sidebarToggle.classList.toggle("sidebar-collapsed");
            sidebarToggle.textContent = sidebar.classList.contains("collapsed") ? "▶" : "◀";
            // Refresh sigma after sidebar animation completes
            setTimeout(() => sigma.refresh(), 350);
        }});

        // Sidebar section collapse/expand
        document.querySelectorAll('.sidebar-section-header').forEach(header => {{
            header.addEventListener('click', () => {{
                const section = header.closest('.sidebar-section');
                section.classList.toggle('collapsed');
            }});
        }});

        // Data Panel functionality
        const dataPanel = document.getElementById("data-panel");
        const dataPanelHeader = document.getElementById("data-panel-header");
        const dataPanelTabs = document.getElementById("data-panel-tabs");
        const dataPanelContent = document.getElementById("data-panel-content");

        // Toggle panel collapse
        dataPanelHeader.addEventListener("click", () => {{
            dataPanel.classList.toggle("collapsed");
        }});

        function rebuildDataTabs(preferredType = null) {{
            dataPanelTabs.innerHTML = "";
            let firstTab = null;

            Object.entries(nodesByType).forEach(([type, nodes]) => {{
                const config = ENTITY_CONFIG[type] || {{ color: "#64748b" }};
                const tab = document.createElement("div");
                tab.className = "data-tab";
                tab.dataset.type = type;
                tab.innerHTML = `
                    <span class="data-tab-dot" style="background: ${{config.color}}"></span>
                    <span>${{type}}</span>
                    <span class="data-tab-count">${{nodes.length}}</span>
                `;
                tab.addEventListener("click", () => switchTab(type));
                dataPanelTabs.appendChild(tab);
                if (!firstTab) firstTab = type;
            }});

            const valMeta = graphData.metadata.validation || {{}};
            if (valMeta.total > 0) {{
                const valTab = document.createElement("div");
                valTab.className = "data-tab";
                valTab.dataset.type = "__validation__";
                const allValid = valMeta.invalid === 0;
                valTab.innerHTML = `
                    <span class="data-tab-dot" style="background: ${{allValid ? '#22c55e' : '#ef4444'}}"></span>
                    <span>Validation</span>
                    <span class="data-tab-count">${{valMeta.valid}}/${{valMeta.total}}</span>
                `;
                valTab.addEventListener("click", () => switchTab("__validation__"));
                dataPanelTabs.appendChild(valTab);
            }}

            const nextTab = preferredType && dataPanelTabs.querySelector(`[data-type="${{preferredType}}"]`)
                ? preferredType
                : (firstTab || "__validation__");

            if (nextTab && dataPanelTabs.querySelector(`[data-type="${{nextTab}}"]`)) {{
                switchTab(nextTab);
            }} else {{
                dataPanelContent.innerHTML = "<p style='padding: 20px; color: #94a3b8;'>No data available</p>";
            }}
        }}

        // Build validation table
        function buildValidationTable() {{
            let html = '<div class="data-table-wrapper"><table class="data-table"><thead><tr>';
            html += '<th>Entity</th><th>Type</th><th>Shape</th><th>Strict</th><th>Agent</th><th>Errors</th>';
            html += '</tr></thead><tbody>';

            graphData.nodes.forEach(node => {{
                const v = node.validation;
                const strictOk = v ? v.strict : null;
                const agentOk = v ? v.agent : null;
                const errCount = v ? (v.strict_errors || []).length + (v.agent_errors || []).length : 0;
                const rowBg = v === null ? "" : (strictOk && agentOk ? 'style="background: #f0fdf430;"' : 'style="background: #fef2f230;"');

                html += `<tr data-node-id="${{node.id}}" ${{rowBg}}>`;
                html += `<td title="${{node.id}}">${{node.label}}</td>`;
                html += `<td>${{node.nodeType}}</td>`;
                html += `<td>${{v ? v.shape : "-"}}</td>`;

                if (v === null) {{
                    html += '<td style="color:#94a3b8">-</td><td style="color:#94a3b8">-</td><td>-</td>';
                }} else {{
                    html += `<td style="color:${{strictOk ? '#22c55e' : '#ef4444'}}; font-weight:600;">${{strictOk ? "Pass" : "Fail"}}</td>`;
                    html += `<td style="color:${{agentOk ? '#22c55e' : '#ef4444'}}; font-weight:600;">${{agentOk ? "Pass" : "Fail"}}</td>`;
                    if (errCount > 0) {{
                        const allErrs = [...(v.strict_errors || []), ...(v.agent_errors || [])];
                        const firstErr = allErrs[0];
                        const msg = typeof firstErr === "string" ? firstErr : (firstErr.message || JSON.stringify(firstErr));
                        html += `<td style="color:#ef4444" title="${{msg.replace(/"/g, '&quot;')}}">${{errCount}} error${{errCount > 1 ? "s" : ""}}</td>`;
                    }} else {{
                        html += '<td style="color:#22c55e">None</td>';
                    }}
                }}
                html += '</tr>';
            }});

            html += '</tbody></table></div>';
            return html;
        }}

        // Build table for a type
        function buildTable(type) {{
            const nodes = nodesByType[type] || [];
            if (nodes.length === 0) return "<p style='padding: 20px; color: #94a3b8;'>No nodes of this type</p>";

            // Collect all property keys
            const allKeys = new Set(['name']);
            nodes.forEach(node => {{
                if (node.properties) {{
                    Object.keys(node.properties).forEach(key => {{
                        if (!hiddenProps.has(key)) allKeys.add(key);
                    }});
                }}
            }});

            // Prioritize certain columns
            const priorityKeys = ['name', 'email', 'url', 'identifier', 'githubUsername', 'orcidIdentifier', 'role', 'githubRepositoryHandle'];
            const sortedKeys = [...allKeys].sort((a, b) => {{
                const aIdx = priorityKeys.indexOf(a);
                const bIdx = priorityKeys.indexOf(b);
                if (aIdx >= 0 && bIdx >= 0) return aIdx - bIdx;
                if (aIdx >= 0) return -1;
                if (bIdx >= 0) return 1;
                return a.localeCompare(b);
            }}).slice(0, 8); // Limit columns

            let html = '<div class="data-table-wrapper"><table class="data-table"><thead><tr>';
            sortedKeys.forEach(key => {{
                html += `<th>${{key}}</th>`;
            }});
            html += '</tr></thead><tbody>';

            nodes.forEach(node => {{
                html += `<tr data-node-id="${{node.id}}">`;
                sortedKeys.forEach(key => {{
                    let value = node.properties ? node.properties[key] : "";
                    if (key === "name") value = node.label;
                    if (Array.isArray(value)) value = value.join(", ");
                    if (typeof value === "object") value = JSON.stringify(value);
                    if (typeof value === "string" && value.length > 50) value = value.slice(0, 50) + "...";
                    html += `<td title="${{String(value || '').replace(/"/g, '&quot;')}}">${{value || "-"}}</td>`;
                }});
                html += '</tr>';
            }});

            html += '</tbody></table></div>';
            return html;
        }}

        // Switch active tab
        function switchTab(type) {{
            activeDataTab = type;
            document.querySelectorAll(".data-tab").forEach(tab => {{
                tab.classList.toggle("active", tab.dataset.type === type);
            }});
            dataPanelContent.innerHTML = type === "__validation__" ? buildValidationTable() : buildTable(type);

            // Add click handlers to table rows
            document.querySelectorAll(".data-table tr[data-node-id]").forEach(row => {{
                row.addEventListener("click", () => {{
                    const nodeId = row.dataset.nodeId;
                    selectNode(nodeId);
                    focusNode(nodeId);
                    highlightTableRow(nodeId);
                }});
            }});
        }}

        // Highlight table row for selected node
        function highlightTableRow(nodeId) {{
            document.querySelectorAll(".data-table tr.selected").forEach(row => {{
                row.classList.remove("selected");
            }});
            const row = document.querySelector(`.data-table tr[data-node-id="${{nodeId}}"]`);
            if (row) {{
                row.classList.add("selected");
                row.scrollIntoView({{ behavior: "smooth", block: "nearest" }});
            }}
        }}

        // Clear table selection
        function clearTableSelection() {{
            document.querySelectorAll(".data-table tr.selected").forEach(row => {{
                row.classList.remove("selected");
            }});
        }}

        function rebuildSidebarAndTables(preferredTab = null) {{
            regroupNodesByType();
            rebuildFilters();
            rebuildDataTabs(preferredTab || activeDataTab);
            updateStats();
            updateVisibility();
        }}

        // Initial UI/data setup
        buildEdgeLegend();
        rebuildSidebarAndTables();
        initJsonldEditor();
        setNodeEditButtonState(null);
        syncJsonldEditorToggleState();

        // Info modal
        const infoOverlay = document.getElementById('info-modal-overlay');
        const infoCloseBtn = document.getElementById('info-close-btn');
        const infoDismissBtn = document.getElementById('info-dismiss-btn');
        const infoBtn = document.getElementById('info-btn');

        function closeInfoModal() {{
            infoOverlay.classList.add('hidden');
        }}

        function openInfoModal() {{
            infoOverlay.classList.remove('hidden');
        }}

        infoCloseBtn.addEventListener('click', closeInfoModal);
        infoDismissBtn.addEventListener('click', closeInfoModal);
        infoOverlay.addEventListener('click', (e) => {{
            if (e.target === infoOverlay) closeInfoModal();
        }});
        document.addEventListener('keydown', (e) => {{
            if (e.key === 'Escape' && !infoOverlay.classList.contains('hidden')) {{
                closeInfoModal();
            }}
        }});
        infoBtn.addEventListener('click', openInfoModal);
    </script>
</body>
</html>
"""


def main():
    """Main entry point."""
    print(f"Loading JSON-LD from: {JSONLD_FILE}")

    # Check if input file exists
    if not JSONLD_FILE.exists():
        print(f"Error: JSON-LD file not found at {JSONLD_FILE}")
        print("Run 'python scripts/build_jsonld.py' first to generate it.")
        sys.exit(1)

    # Load JSON-LD
    jsonld, jsonld_text = load_jsonld()

    # Load validation results
    validation_lookup = load_validation()
    if validation_lookup:
        print(f"  Loaded validation results for {len(validation_lookup)} instances")
    else:
        print("  No validation results found (run validate_schemas.py first)")

    # Build graph data
    print("Building graph data...")
    graph_data = build_graph_data(jsonld, validation_lookup)

    val_meta = graph_data["metadata"]["validation"]
    print(f"  Nodes: {graph_data['metadata']['nodeCount']}")
    print(f"  Edges: {graph_data['metadata']['edgeCount']}")
    print(
        f"  Validation: {val_meta['valid']}/{val_meta['total']} valid"
        + (f", {val_meta['invalid']} invalid" if val_meta["invalid"] else ""),
    )

    # Generate HTML
    print("Generating HTML visualization...")
    html = generate_html(graph_data, jsonld_text)

    # Ensure output directory exists
    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Visualization saved to: {OUTPUT_FILE}")
    print(f"   Open in browser: file://{OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
