#!/usr/bin/env bash
# Run /v2/extract in parallel against a fixed set of repositories and
# store each response under a timestamped output folder for evaluation.
#
# Usage:
#   scripts/v2/batch_extract.sh                    # use defaults
#   V2_BASE_URL=http://localhost:8000 scripts/v2/batch_extract.sh
#   OUTPUT_DIR=./my-results scripts/v2/batch_extract.sh
#   PARALLELISM=2 scripts/v2/batch_extract.sh      # cap concurrency
#   TIMEOUT_SECONDS=900 scripts/v2/batch_extract.sh
#
# Files written under $OUTPUT_DIR/$RUN_ID/:
#   <owner>_<repo>.jsonld         pretty-printed response on HTTP 200
#   <owner>_<repo>.error.json     raw response on non-200
#   _logs/<owner>_<repo>.log      stderr from curl + timing
#   _summary.txt                  one-line status per repo at the end

set -u

V2_BASE_URL=${V2_BASE_URL:-http://localhost:1234}
OUTPUT_DIR=${OUTPUT_DIR:-extraction_results}
RUN_ID=${RUN_ID:-$(date +%Y%m%d-%H%M%S)}
PARALLELISM=${PARALLELISM:-5}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-1800}

REPOSITORIES=(
  "https://github.com/sdsc-ordes/repository-template"
  "https://github.com/sdsc-ordes/gimie"
  "https://github.com/EPFL-Open-Science/EPFL_OS_Analysis"
  "https://github.com/DeepLabCut/DeepLabCut"
  "https://github.com/EPFL-ENAC/co2-calculator"
)

run_dir="$OUTPUT_DIR/$RUN_ID"
log_dir="$run_dir/_logs"
mkdir -p "$log_dir"

summary_file="$run_dir/_summary.txt"
: >"$summary_file"

# Sanitize "https://github.com/<owner>/<repo>" → "<owner>_<repo>"
slug_for() {
  local url=$1
  url=${url%/}
  url=${url#https://github.com/}
  url=${url#http://github.com/}
  url=${url#github.com/}
  printf '%s' "${url//\//_}"
}

extract_one() {
  local repo_url=$1
  local slug
  slug=$(slug_for "$repo_url")
  local path=${repo_url#https://}
  path=${path#http://}
  local target_url="$V2_BASE_URL/v2/extract/$path?output_format=jsonld"
  local out_file="$run_dir/$slug.jsonld"
  local err_file="$run_dir/$slug.error.json"
  local log_file="$log_dir/$slug.log"
  local raw_file
  raw_file=$(mktemp)

  printf '[%s] starting %s\n' "$(date +%T)" "$slug" | tee -a "$log_file"
  local start_ts=$SECONDS
  local http_code
  http_code=$(curl -sS --max-time "$TIMEOUT_SECONDS" \
    -w '%{http_code}' \
    -H 'Accept: application/json' \
    -o "$raw_file" \
    "$target_url" 2>>"$log_file") || http_code="000"
  local elapsed=$((SECONDS - start_ts))

  if [[ "$http_code" == "200" ]]; then
    if command -v jq >/dev/null 2>&1; then
      jq . "$raw_file" >"$out_file" 2>>"$log_file" || cp "$raw_file" "$out_file"
    else
      cp "$raw_file" "$out_file"
    fi
    printf '[%s] OK %s (%ds)\n' "$(date +%T)" "$slug" "$elapsed" | tee -a "$log_file"
    printf 'OK  %3ds  %s\n' "$elapsed" "$slug" >>"$summary_file"
  else
    if command -v jq >/dev/null 2>&1 && jq -e . "$raw_file" >/dev/null 2>&1; then
      jq . "$raw_file" >"$err_file"
    else
      cp "$raw_file" "$err_file"
    fi
    printf '[%s] FAIL %s http=%s (%ds)\n' "$(date +%T)" "$slug" "$http_code" "$elapsed" \
      | tee -a "$log_file"
    printf 'FAIL http=%s %3ds  %s\n' "$http_code" "$elapsed" "$slug" >>"$summary_file"
  fi
  rm -f "$raw_file"
}

dispatch() {
  printf 'V2_BASE_URL    : %s\n' "$V2_BASE_URL"
  printf 'OUTPUT_DIR     : %s\n' "$run_dir"
  printf 'PARALLELISM    : %s\n' "$PARALLELISM"
  printf 'TIMEOUT_SECONDS: %s\n' "$TIMEOUT_SECONDS"
  printf 'REPOSITORIES   : %s\n' "${#REPOSITORIES[@]}"
  printf '\n'

  local pids=()
  local in_flight=0
  for repo in "${REPOSITORIES[@]}"; do
    extract_one "$repo" &
    pids+=("$!")
    in_flight=$((in_flight + 1))
    if (( in_flight >= PARALLELISM )); then
      wait -n
      in_flight=$((in_flight - 1))
    fi
  done
  wait
}

dispatch
echo
echo "=== summary ($run_dir/_summary.txt) ==="
cat "$summary_file"
