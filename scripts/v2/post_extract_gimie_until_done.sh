#!/usr/bin/env bash

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:1234}"
SOURCE_URL="${SOURCE_URL:-github.com/sdsc-ordes/gimie}"
OUTPUT_FORMAT="${OUTPUT_FORMAT:-json}"
AGENT_RUNTIME="${AGENT_RUNTIME:-llm}"
INCLUDE_CONTEXT_SUMMARY="${INCLUDE_CONTEXT_SUMMARY:-true}"
INCLUDE_INTERMEDIATES="${INCLUDE_INTERMEDIATES:-false}"
REQUEST_TIMEOUT_SECONDS="${REQUEST_TIMEOUT_SECONDS:-120}"
RETRY_SLEEP_SECONDS="${RETRY_SLEEP_SECONDS:-10}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-0}" # 0 means retry forever.
OUTPUT_FILE="${OUTPUT_FILE:-/tmp/v2_extract_gimie_response.json}"

attempt=0

echo "POST polling started"
echo "  endpoint: ${API_BASE_URL}/v2/extract"
echo "  source_url: ${SOURCE_URL}"
echo "  runtime: ${AGENT_RUNTIME}"
echo "  output_file: ${OUTPUT_FILE}"

while true; do
  attempt=$((attempt + 1))
  temp_response_file="$(mktemp)"

  payload="$(cat <<JSON
{
  "source_url": "${SOURCE_URL}",
  "output_format": "${OUTPUT_FORMAT}",
  "agent_runtime": "${AGENT_RUNTIME}",
  "include_context_summary": ${INCLUDE_CONTEXT_SUMMARY},
  "include_intermediates": ${INCLUDE_INTERMEDIATES}
}
JSON
)"

  echo "[attempt ${attempt}] sending POST request..."

  set +e
  http_code="$(
    curl -sS \
      --max-time "${REQUEST_TIMEOUT_SECONDS}" \
      -o "${temp_response_file}" \
      -w "%{http_code}" \
      -X POST "${API_BASE_URL}/v2/extract" \
      -H "Content-Type: application/json" \
      -d "${payload}"
  )"
  curl_exit=$?
  set -e

  if [[ ${curl_exit} -ne 0 ]]; then
    echo "[attempt ${attempt}] request failed/timed out (curl exit=${curl_exit}). Retrying in ${RETRY_SLEEP_SECONDS}s..."
    rm -f "${temp_response_file}"
    if [[ "${MAX_ATTEMPTS}" -gt 0 && "${attempt}" -ge "${MAX_ATTEMPTS}" ]]; then
      echo "Reached MAX_ATTEMPTS=${MAX_ATTEMPTS} without success."
      exit 1
    fi
    sleep "${RETRY_SLEEP_SECONDS}"
    continue
  fi

  if [[ "${http_code}" == "200" ]]; then
    mv "${temp_response_file}" "${OUTPUT_FILE}"
    echo "[attempt ${attempt}] done (HTTP 200)."
    if command -v jq >/dev/null 2>&1; then
      jq -r '"summary: detected=\(.detected_type) entities=\(.stats.entities_count) duration_ms=\(.stats.duration_ms) warnings=\((.warnings|length))"' "${OUTPUT_FILE}" || true
    fi
    echo "response saved to ${OUTPUT_FILE}"
    break
  fi

  echo "[attempt ${attempt}] HTTP ${http_code} (not done yet)."
  if command -v jq >/dev/null 2>&1; then
    jq -r '.detail // .error_type // "no detail"' "${temp_response_file}" || true
  fi
  rm -f "${temp_response_file}"

  if [[ "${MAX_ATTEMPTS}" -gt 0 && "${attempt}" -ge "${MAX_ATTEMPTS}" ]]; then
    echo "Reached MAX_ATTEMPTS=${MAX_ATTEMPTS} without success."
    exit 1
  fi

  echo "retrying in ${RETRY_SLEEP_SECONDS}s..."
  sleep "${RETRY_SLEEP_SECONDS}"
done
