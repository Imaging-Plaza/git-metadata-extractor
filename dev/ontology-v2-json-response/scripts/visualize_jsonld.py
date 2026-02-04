#!/usr/bin/env python3
"""
Generate an interactive HTML visualization of JSON-LD graph data using Sigma.js.

This script reads the JSON-LD output from build_jsonld.py and creates a standalone
HTML file with:
- Hierarchical radial layout (organized by entity type)
- Curved edges for better visual distinction
- Interactive features: search, filter, click-to-highlight, tooltips

Usage:
    python scripts/visualize_jsonld.py

Output:
    - a-001/test/visualization.html - Interactive graph visualization
"""

import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
TEST_OUTPUT_DIR = BASE_DIR / "a-001" / "test"
JSONLD_FILE = TEST_OUTPUT_DIR / "jsonld_output.json"
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


def load_jsonld() -> dict:
    """Load JSON-LD file."""
    with open(JSONLD_FILE, encoding="utf-8") as f:
        return json.load(f)


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

                # Check for bidirectional edge (for curved rendering)
                reverse_key = (target_id, source_id)
                has_reverse = any(
                    (reverse_key[0], reverse_key[1], f) in edge_set
                    for f in CROSS_REF_FIELDS
                )

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
                        "curvature": 0.3 if has_reverse else 0,
                    },
                )

    return edges


def build_graph_data(jsonld: dict) -> dict:
    """Build the graph data structure for Sigma.js."""
    graph = jsonld.get("@graph", [])
    id_index = build_id_index(graph)

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

        nodes.append(
            {
                "id": node_id,
                "label": extract_label(node),
                "x": pos[0],
                "y": pos[1],
                "size": size,
                "color": config["color"],
                "nodeType": config[
                    "label"
                ],  # Use nodeType instead of type (Sigma reserves 'type' for renderers)
                "entityType": node_type,
                "properties": properties,
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
        },
    }


def generate_html(graph_data: dict) -> str:
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

        /* Layout selector */
        #layout-selector {{
            position: absolute;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            gap: 4px;
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 4px;
            z-index: 100;
        }}

        .layout-btn {{
            padding: 8px 14px;
            font-size: 12px;
            font-weight: 500;
            background: transparent;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            color: #64748b;
            transition: all 0.2s;
        }}

        .layout-btn:hover {{
            background: #f1f5f9;
            color: #1e293b;
        }}

        .layout-btn.active {{
            background: #3b82f6;
            color: white;
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
            background: white;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 12px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            pointer-events: none;
            display: none;
            max-width: 350px;
            z-index: 1000;
        }}

        #tooltip.visible {{
            display: block;
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
    </style>
</head>
<body>
    <div id="app">
        <div id="main-content">
            <div id="sidebar-wrapper">
                <div id="sidebar">
                    <div id="sidebar-header">
                        <h1>Open Pulse Ontology</h1>
                        <p>JSON-LD Graph Visualization</p>
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
                            <div id="node-properties"></div>
                        </div>
                    </div>
                </div>
            </div>
            <button id="sidebar-toggle" title="Toggle Sidebar">◀</button>
            </div>

            <div id="graph-area">
                <div id="graph-container">
                    <div id="sigma-container"></div>

                    <!-- Layout selector -->
                    <div id="layout-selector">
                        <button class="layout-btn active" data-layout="force" title="Force-directed layout with animation">Force Atlas</button>
                        <button class="layout-btn" data-layout="circular" title="Arrange nodes in a circle by type">Circular</button>
                        <button class="layout-btn" data-layout="radial" title="Hierarchical rings by entity type">Radial</button>
                        <button class="layout-btn" data-layout="grid" title="Arrange nodes in a grid">Grid</button>
                    </div>

                    <div id="controls">
                        <button class="control-btn" id="zoom-in" title="Zoom In">+</button>
                        <button class="control-btn" id="zoom-out" title="Zoom Out">−</button>
                        <button class="control-btn" id="zoom-fit" title="Fit to View">⊡</button>
                        <button class="control-btn" id="reset-view" title="Reset">↺</button>
                        <button class="control-btn" id="toggle-drag" title="Toggle Drag Mode">✋</button>
                        <button class="control-btn active" id="toggle-tooltip" title="Toggle Tooltips">💬</button>
                        <button class="control-btn" id="stop-layout" title="Stop Animation" style="display:none;">⏹</button>
                    </div>

                    <div id="drag-hint">Drag mode: Click and drag nodes to reposition them</div>

                    <div id="stats">
                        <span id="node-count">0</span> nodes · <span id="edge-count">0</span> edges
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
        // Graph data injected from Python
        const GRAPH_DATA = {json.dumps(graph_data, indent=2)};

        // Entity configuration
        const ENTITY_CONFIG = {json.dumps({v["label"]: {"color": v["color"]} for k, v in ENTITY_CONFIG.items()}, indent=2)};

        // Edge configuration
        const EDGE_CONFIG = {json.dumps({v["label"]: {"color": v["color"]} for k, v in EDGE_CONFIG.items()}, indent=2)};

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

        // Add nodes
        GRAPH_DATA.nodes.forEach(node => {{
            graph.addNode(node.id, {{
                label: node.label,
                x: node.x,
                y: node.y,
                size: node.size,
                color: node.color,
                nodeType: node.nodeType,
                entityType: node.entityType,
                properties: node.properties,
                originalColor: node.color,
                originalSize: node.size,
            }});
        }});

        // Add edges
        GRAPH_DATA.edges.forEach((edge, index) => {{
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

            // Update active button
            document.querySelectorAll('.layout-btn').forEach(btn => {{
                btn.classList.toggle('active', btn.dataset.layout === layoutName);
            }});
        }}

        // Layout button handlers
        document.querySelectorAll('.layout-btn').forEach(btn => {{
            btn.addEventListener('click', () => {{
                applyLayout(btn.dataset.layout);
            }});
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

        // Update stats
        document.getElementById("node-count").textContent = graph.order;
        document.getElementById("edge-count").textContent = graph.size;

        // Build filters
        const filtersContainer = document.getElementById("filters");
        const typeCounts = {{}};
        GRAPH_DATA.nodes.forEach(node => {{
            typeCounts[node.nodeType] = (typeCounts[node.nodeType] || 0) + 1;
        }});

        Object.entries(ENTITY_CONFIG).forEach(([type, config]) => {{
            const count = typeCounts[type] || 0;
            if (count === 0) return;

            const item = document.createElement("label");
            item.className = "filter-item";
            item.innerHTML = `
                <input type="checkbox" class="filter-checkbox" data-type="${{type}}" checked>
                <span class="filter-color" style="background: ${{config.color}}"></span>
                <span class="filter-label">${{type}}</span>
                <span class="filter-count">${{count}}</span>
            `;
            filtersContainer.appendChild(item);
        }});

        // Build edge legend
        const edgeLegend = document.getElementById("edge-legend");
        Object.entries(EDGE_CONFIG).forEach(([label, config]) => {{
            const item = document.createElement("div");
            item.className = "legend-item";
            item.innerHTML = `
                <span class="legend-line" style="background: ${{config.color}}"></span>
                <span>${{label}}</span>
            `;
            edgeLegend.appendChild(item);
        }});

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

            const propsDiv = document.getElementById("node-properties");
            propsDiv.innerHTML = "";

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
        }}

        // Sigma event handlers
        sigma.on("clickNode", ({{ node }}) => {{
            selectNode(node);
            highlightTableRow(node);
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

        // Group nodes by type for tables
        const nodesByType = {{}};
        GRAPH_DATA.nodes.forEach(node => {{
            if (!nodesByType[node.nodeType]) {{
                nodesByType[node.nodeType] = [];
            }}
            nodesByType[node.nodeType].push(node);
        }});

        // Build tabs
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
            document.querySelectorAll(".data-tab").forEach(tab => {{
                tab.classList.toggle("active", tab.dataset.type === type);
            }});
            dataPanelContent.innerHTML = buildTable(type);

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

        // Initialize first tab
        if (firstTab) switchTab(firstTab);
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
        exit(1)

    # Load JSON-LD
    jsonld = load_jsonld()

    # Build graph data
    print("Building graph data...")
    graph_data = build_graph_data(jsonld)

    print(f"  Nodes: {graph_data['metadata']['nodeCount']}")
    print(f"  Edges: {graph_data['metadata']['edgeCount']}")

    # Generate HTML
    print("Generating HTML visualization...")
    html = generate_html(graph_data)

    # Ensure output directory exists
    TEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Visualization saved to: {OUTPUT_FILE}")
    print(f"   Open in browser: file://{OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
