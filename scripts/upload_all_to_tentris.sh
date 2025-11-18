#!/bin/bash

# --- CONFIGURATION ---
TENTRIS_HOST="http://128.178.219.51:7502"
USERNAME="admin"
PASSWORD="shrekislife"
COOKIE_FILE="/tmp/tentris-cookie"
DATA_DIR="/home/rmfranken/git-metadata-extractor/data/1_batch_11122025/1_batch/converted"
PYTHON_CMD="/home/rmfranken/git-metadata-extractor/.venv/bin/python"
TEMP_DIR="/tmp/tentris_batch"

# --- END CONFIGURATION ---

echo "=== Tentris Batch Upload ==="
echo "Data directory: $DATA_DIR"
echo ""

# Create temp directory
mkdir -p "$TEMP_DIR"

# Step 1: Login
echo "Step 1: Logging in to Tentris..."
login_response=$(curl -s -w "\n%{http_code}" -c "$COOKIE_FILE" \
    --data "username=$USERNAME&password=$PASSWORD" \
    "$TENTRIS_HOST/login")

login_code=$(echo "$login_response" | tail -n1)

if [[ "$login_code" =~ ^(2[0-9][0-9]|303)$ ]]; then
    echo "✅ Login successful"
else
    echo "❌ Login failed (HTTP $login_code)"
    exit 1
fi

echo ""

# Step 2: Count files
cd "$DATA_DIR" || { echo "❌ Directory not found: $DATA_DIR"; exit 1; }
total_files=$(ls -1 *.jsonld 2>/dev/null | wc -l)

if [ "$total_files" -eq 0 ]; then
    echo "❌ No JSON-LD files found in $DATA_DIR"
    exit 1
fi

echo "Found $total_files JSON-LD files to upload"
echo ""

# Step 3: Process each file
count=0
success=0
failed=0
failed_files=()

for jsonld_file in *.jsonld; do
    if [ -f "$jsonld_file" ]; then
        count=$((count + 1))
        echo "[$count/$total_files] Processing: $jsonld_file"

        # Convert to Turtle
        turtle_file="$TEMP_DIR/$(basename "$jsonld_file" .jsonld).ttl"

        $PYTHON_CMD << PYEOF
import sys
from rdflib import Graph

try:
    g = Graph()
    g.parse("$DATA_DIR/$jsonld_file", format="json-ld")

    with open("$turtle_file", "w", encoding="utf-8") as f:
        f.write(g.serialize(format="turtle"))

    print(f"  ✅ Converted to Turtle ({len(g)} triples)")
    sys.exit(0)
except Exception as e:
    print(f"  ❌ Conversion failed: {e}")
    sys.exit(1)
PYEOF

        if [ $? -ne 0 ]; then
            echo "  ❌ Skipping due to conversion error"
            failed=$((failed + 1))
            failed_files+=("$jsonld_file (conversion failed)")
            continue
        fi

        # Upload to Tentris
        upload_response=$(curl -s -w "\n%{http_code}" -b "$COOKIE_FILE" \
            -X POST \
            -H "Content-Type: text/turtle" \
            --data-binary "@$turtle_file" \
            "$TENTRIS_HOST/graph-store?default")

        upload_code=$(echo "$upload_response" | tail -n1)

        if [[ "$upload_code" =~ ^2[0-9][0-9]$ ]]; then
            echo "  ✅ Uploaded successfully (HTTP $upload_code)"
            success=$((success + 1))
            # Clean up temp file
            rm -f "$turtle_file"
        else
            echo "  ❌ Upload failed (HTTP $upload_code)"
            echo "     Response: $(echo "$upload_response" | head -n -1 | head -c 100)"
            failed=$((failed + 1))
            failed_files+=("$jsonld_file (HTTP $upload_code)")
        fi

        # Small delay to avoid overwhelming the server
        sleep 0.1
        echo ""
    fi
done

# Cleanup
rm -f "$COOKIE_FILE"
rmdir "$TEMP_DIR" 2>/dev/null

# Summary
echo "=== Upload Complete ==="
echo "Total files:  $total_files"
echo "Successful:   $success"
echo "Failed:       $failed"

if [ $failed -gt 0 ]; then
    echo ""
    echo "Failed files:"
    for file in "${failed_files[@]}"; do
        echo "  - $file"
    done
    exit 1
else
    echo ""
    echo "✅ All files uploaded successfully!"
    exit 0
fi
