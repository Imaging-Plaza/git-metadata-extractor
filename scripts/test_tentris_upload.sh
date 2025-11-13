#!/bin/bash

# Configuration
TENTRIS_HOST="http://128.178.219.51:7502"
USERNAME="admin"  # !!! CHANGE THIS !!!
PASSWORD="shrekislife"  # !!! CHANGE THIS !!!
COOKIE_FILE="/tmp/tentris-cookie"
TEST_FILE="/home/rmfranken/git-metadata-extractor/data/1_batch_11122025/1_batch/converted/0xKDI.jsonld"

echo "=== Tentris Authentication & Upload Test ==="
echo ""

# Step 1: Try multiple login methods
echo "Step 1: Logging in to Tentris..."
echo "Using username: $USERNAME"

# Try the login method from the documentation
# Just use --data (defaults to POST with proper content-type)
echo "  Trying form-based login..."
login_response=$(curl -s -w "\n%{http_code}" -c "$COOKIE_FILE" \
    --data "username=$USERNAME&password=$PASSWORD" \
    "$TENTRIS_HOST/login")

login_code=$(echo "$login_response" | tail -n1)
echo "Login response code: $login_code"

# 303 is a redirect (See Other) which usually means success for POST to login
if [[ "$login_code" =~ ^(2[0-9][0-9]|303)$ ]]; then
    echo "✅ Login successful (got redirect or 2xx)"
    echo "Cookie saved to: $COOKIE_FILE"
    
    # Show what's in the cookie file
    if [ -f "$COOKIE_FILE" ]; then
        echo "Cookie contents:"
        cat "$COOKIE_FILE"
    fi
else
    echo "❌ Login failed (HTTP $login_code)"
    echo "Response: $(echo "$login_response" | head -n -1)"
    exit 1
fi

echo ""

# Step 2: Convert JSON-LD to Turtle format
echo "Step 2: Converting JSON-LD to Turtle format..."
echo "Input file: $TEST_FILE"

TEMP_TURTLE="/tmp/tentris_upload.ttl"

# Use Python with rdflib to convert JSON-LD to Turtle
# Use the venv python if available, otherwise fall back to python3
PYTHON_CMD="/home/rmfranken/git-metadata-extractor/.venv/bin/python"
if [ ! -f "$PYTHON_CMD" ]; then
    PYTHON_CMD="python3"
fi

$PYTHON_CMD << 'PYEOF'
import sys
import json
from rdflib import Graph

try:
    # Load JSON-LD file
    g = Graph()
    g.parse("/home/rmfranken/git-metadata-extractor/data/1_batch_11122025/1_batch/converted/0xKDI.jsonld", format="json-ld")
    
    # Serialize to Turtle
    with open("/tmp/tentris_upload.ttl", "w", encoding="utf-8") as f:
        f.write(g.serialize(format="turtle"))
    
    print(f"✅ Converted to Turtle ({len(g)} triples)")
    sys.exit(0)
except Exception as e:
    print(f"❌ Conversion failed: {e}")
    sys.exit(1)
PYEOF

if [ $? -ne 0 ]; then
    echo "Failed to convert JSON-LD to Turtle"
    exit 1
fi

echo ""

# Step 3: Upload the Turtle file
echo "Step 3: Uploading Turtle file to Tentris..."
echo "File: $TEMP_TURTLE"

upload_response=$(curl -s -w "\n%{http_code}" -b "$COOKIE_FILE" \
    -X POST \
    -H "Content-Type: text/turtle" \
    --data-binary "@$TEMP_TURTLE" \
    "$TENTRIS_HOST/graph-store?default")

upload_code=$(echo "$upload_response" | tail -n1)
echo "Upload response code: $upload_code"

if [[ "$upload_code" =~ ^2[0-9][0-9]$ ]]; then
    echo "✅ Upload successful"
    echo "Response: $(echo "$upload_response" | head -n -1)"
else
    echo "❌ Upload failed (HTTP $upload_code)"
    echo "Response: $(echo "$upload_response" | head -n -1)"
    exit 1
fi

echo ""

# Step 4: Verify with a SPARQL query
echo "Step 4: Verifying upload with SPARQL query..."
query_response=$(curl -s -w "\n%{http_code}" -b "$COOKIE_FILE" \
    -H "Content-Type: application/sparql-query" \
    --data "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }" \
    "$TENTRIS_HOST/sparql")

query_code=$(echo "$query_response" | tail -n1)
echo "Query response code: $query_code"

if [[ "$query_code" =~ ^2[0-9][0-9]$ ]]; then
    echo "✅ Query successful"
    echo "Response: $(echo "$query_response" | head -n -1)"
else
    echo "❌ Query failed (HTTP $query_code)"
    echo "Response: $(echo "$query_response" | head -n -1)"
fi

echo ""
echo "=== Test Complete ==="

# Clean up cookie file
rm -f "$COOKIE_FILE"
