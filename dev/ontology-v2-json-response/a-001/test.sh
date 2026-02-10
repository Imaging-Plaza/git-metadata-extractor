#!/bin/bash
# Run all validation and test scripts for ontology-v2-json-response/a-001
# This script runs all validation steps and produces test outputs

set -e  # Exit on first error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
SCRIPTS_DIR="$BASE_DIR/scripts"
TEST_OUTPUT_DIR="$SCRIPT_DIR/test"
SUMMARY_FILE="$TEST_OUTPUT_DIR/validation_summary.txt"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print to both console and summary file
log() {
    echo -e "$1"
    # Strip color codes for the file
    echo -e "$1" | sed 's/\x1b\[[0-9;]*m//g' >> "$SUMMARY_FILE"
}

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Open Pulse Ontology v2 - Test Suite  ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "Base directory: $BASE_DIR"
echo "Test output: $TEST_OUTPUT_DIR"
echo ""

# Create test output directory
mkdir -p "$TEST_OUTPUT_DIR"

# Initialize summary file
cat > "$SUMMARY_FILE" << EOF
# Open Pulse Ontology v2 - Validation Summary
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")

EOF

# Track overall status
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

run_test() {
    local test_name="$1"
    local test_command="$2"

    TOTAL_TESTS=$((TOTAL_TESTS + 1))
    echo -e "\n${YELLOW}[$TOTAL_TESTS] Running: $test_name${NC}"
    echo "Command: $test_command"
    echo "---"

    # Capture output for summary file
    local output
    local exit_code
    output=$(eval "$test_command" 2>&1) || exit_code=$?
    exit_code=${exit_code:-0}

    # Print to console
    echo "$output"

    # Write to summary file
    echo "" >> "$SUMMARY_FILE"
    echo "## $test_name" >> "$SUMMARY_FILE"
    echo "" >> "$SUMMARY_FILE"
    echo '```' >> "$SUMMARY_FILE"
    echo "$output" >> "$SUMMARY_FILE"
    echo '```' >> "$SUMMARY_FILE"

    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}✓ PASSED: $test_name${NC}"
        echo "" >> "$SUMMARY_FILE"
        echo "**Result: ✓ PASSED**" >> "$SUMMARY_FILE"
        PASSED_TESTS=$((PASSED_TESTS + 1))
        return 0
    else
        echo -e "${RED}✗ FAILED: $test_name${NC}"
        echo "" >> "$SUMMARY_FILE"
        echo "**Result: ✗ FAILED**" >> "$SUMMARY_FILE"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        return 1
    fi
}

# Test 1: JSON Schema Validation (agent + strict)
run_test "JSON Schema Validation" \
    "python $SCRIPTS_DIR/validate_schemas.py" || true

# Test 2: Build JSON-LD and Check Cross-References
run_test "JSON-LD Build & Cross-Reference Validation" \
    "python $SCRIPTS_DIR/build_jsonld.py" || true

# Test 3: SHACL Validation (if pyshacl is available)
echo ""
echo -e "${YELLOW}Checking for pyshacl...${NC}"
if python -c "import pyshacl" 2>/dev/null; then
    run_test "SHACL Validation against TTL Ontology" \
        "python $SCRIPTS_DIR/validate_shacl.py" || true
else
    echo -e "${YELLOW}⚠ Skipping SHACL validation - pyshacl not installed${NC}"
    echo "  Install with: pip install pyshacl rdflib"
fi

# Test 4: TTL-Schema Semantic Alignment (if rdflib is available)
echo ""
echo -e "${YELLOW}Checking for rdflib...${NC}"
if python -c "import rdflib" 2>/dev/null; then
    run_test "TTL-Schema Semantic Alignment" \
        "python $SCRIPTS_DIR/test_ttl_alignment.py" || true
else
    echo -e "${YELLOW}⚠ Skipping alignment test - rdflib not installed${NC}"
    echo "  Install with: pip install rdflib"
fi

# Test 5: Round-Trip Test (if rdflib is available)
echo ""
echo -e "${YELLOW}Checking for rdflib (round-trip)...${NC}"
if python -c "import rdflib" 2>/dev/null; then
    run_test "JSON-LD Round-Trip Test" \
        "python $SCRIPTS_DIR/test_roundtrip.py" || true
else
    echo -e "${YELLOW}⚠ Skipping round-trip test - rdflib not installed${NC}"
    echo "  Install with: pip install rdflib"
fi

# Test 6: Generate visualization HTML
echo ""
echo -e "${YELLOW}Generating visualization...${NC}"
run_test "Generate Visualization HTML" \
    "python $SCRIPTS_DIR/visualize_jsonld.py" || true

# Print summary
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}              TEST SUMMARY              ${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "Total Tests: $TOTAL_TESTS"
echo -e "${GREEN}Passed: $PASSED_TESTS${NC}"
echo -e "${RED}Failed: $FAILED_TESTS${NC}"
echo ""

# Write summary to file
echo "" >> "$SUMMARY_FILE"
echo "---" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
echo "## Summary" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
echo "| Metric | Count |" >> "$SUMMARY_FILE"
echo "|--------|-------|" >> "$SUMMARY_FILE"
echo "| Total Tests | $TOTAL_TESTS |" >> "$SUMMARY_FILE"
echo "| Passed | $PASSED_TESTS |" >> "$SUMMARY_FILE"
echo "| Failed | $FAILED_TESTS |" >> "$SUMMARY_FILE"

# List generated test outputs
echo -e "${BLUE}Generated Test Outputs:${NC}"
if [ -d "$TEST_OUTPUT_DIR" ]; then
    ls -la "$TEST_OUTPUT_DIR"/*.json 2>/dev/null || echo "  No JSON outputs found"
    ls -la "$TEST_OUTPUT_DIR"/*.txt 2>/dev/null || echo "  No TXT outputs found"
    ls -la "$TEST_OUTPUT_DIR"/*.html 2>/dev/null || echo "  No HTML outputs found"
fi

# Exit with appropriate code
if [ $FAILED_TESTS -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ All tests passed!${NC}"
    echo "" >> "$SUMMARY_FILE"
    echo "**✅ All tests passed!**" >> "$SUMMARY_FILE"
    echo ""
    echo -e "${BLUE}Validation summary saved to: $SUMMARY_FILE${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}❌ Some tests failed. Check output above for details.${NC}"
    echo "" >> "$SUMMARY_FILE"
    echo "**❌ Some tests failed.**" >> "$SUMMARY_FILE"
    echo ""
    echo -e "${BLUE}Validation summary saved to: $SUMMARY_FILE${NC}"
    exit 1
fi
