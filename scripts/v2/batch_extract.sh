#!/usr/bin/env bash
# Submit /v2/extract jobs in parallel via POST, poll each job to completion,
# and store the full V2ExtractJob record under a timestamped output folder.
#
# Resumable: a repo whose <slug>.json already exists with status=completed or
# status=failed is skipped. Orphaned pending/running records (e.g. from a
# crashed previous run) are re-submitted.
#
# Usage:
#   scripts/v2/batch_extract.sh                          # use defaults
#   V2_BASE_URL=http://localhost:8000 scripts/v2/batch_extract.sh
#   OUTPUT_DIR=./my-results scripts/v2/batch_extract.sh
#   PARALLELISM=2 scripts/v2/batch_extract.sh            # cap concurrency
#   POLL_INTERVAL_SECONDS=15 scripts/v2/batch_extract.sh
#   JOB_TIMEOUT_SECONDS=1800 scripts/v2/batch_extract.sh
#   AGENT_RUNTIME=rule_based scripts/v2/batch_extract.sh
#   OUTPUT_FORMAT=jsonld scripts/v2/batch_extract.sh
#   REPO_LIST=path/to/repos.txt scripts/v2/batch_extract.sh
#
# REPO_LIST format: one URL per line; blank lines and lines starting with `#`
# are ignored. If REPO_LIST is unset, the inline default list below is used.
#
# Files written under $OUTPUT_DIR/$RUN_ID/:
#   <owner>_<repo>.json           full V2ExtractJob record (success or failure)
#   _logs/<owner>_<repo>.log      stderr from curl + per-poll status lines
#   _summary.txt                  one-line status per repo at the end

set -u

V2_BASE_URL=${V2_BASE_URL:-http://localhost:1234}
OUTPUT_DIR=${OUTPUT_DIR:-extraction_results}
RUN_ID=${RUN_ID:-$(date +%Y%m%d-%H%M%S)}
PARALLELISM=${PARALLELISM:-5}
POLL_INTERVAL_SECONDS=${POLL_INTERVAL_SECONDS:-10}
JOB_TIMEOUT_SECONDS=${JOB_TIMEOUT_SECONDS:-1800}
HTTP_TIMEOUT_SECONDS=${HTTP_TIMEOUT_SECONDS:-60}
AGENT_RUNTIME=${AGENT_RUNTIME:-rule_based}
OUTPUT_FORMAT=${OUTPUT_FORMAT:-jsonld}
INCLUDE_CONTEXT_SUMMARY=${INCLUDE_CONTEXT_SUMMARY:-false}
REPO_LIST=${REPO_LIST:-}

DEFAULT_REPOSITORIES=(
  "https://github.com/sdsc-ordes/repository-template"
  "https://github.com/sdsc-ordes/gimie"
  "https://github.com/EPFL-Open-Science/EPFL_OS_Analysis"
  "https://github.com/DeepLabCut/DeepLabCut"
  "https://github.com/EPFL-ENAC/co2-calculator"
)

if [[ -n "$REPO_LIST" ]]; then
  if [[ ! -f "$REPO_LIST" ]]; then
    echo "REPO_LIST file not found: $REPO_LIST" >&2
    exit 2
  fi
  REPOSITORIES=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] && continue
    REPOSITORIES+=("$line")
  done <"$REPO_LIST"
else
  REPOSITORIES=("${DEFAULT_REPOSITORIES[@]}")
fi

run_dir="$OUTPUT_DIR/$RUN_ID"
log_dir="$run_dir/_logs"
mkdir -p "$log_dir"

summary_file="$run_dir/_summary.txt"
: >>"$summary_file"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required (used to parse job responses). Install jq and re-run." >&2
  exit 2
fi

# Sanitize "https://github.com/<owner>/<repo>" → "<owner>_<repo>"
slug_for() {
  local url=$1
  url=${url%/}
  url=${url#https://github.com/}
  url=${url#http://github.com/}
  url=${url#github.com/}
  printf '%s' "${url//\//_}"
}

# True (return 0) if a previous run already produced a terminal record for this
# slug, so the repo can be skipped on resume.
already_done() {
  local out_file=$1
  [[ -s "$out_file" ]] || return 1
  local status
  status=$(jq -r '.status // empty' "$out_file" 2>/dev/null || true)
  case "$status" in
    completed|failed) return 0 ;;
    *) return 1 ;;
  esac
}

submit_job() {
  local repo_url=$1
  local log_file=$2
  local payload
  payload=$(jq -nc \
    --arg source_url "$repo_url" \
    --arg output_format "$OUTPUT_FORMAT" \
    --arg agent_runtime "$AGENT_RUNTIME" \
    --argjson include_context_summary "$INCLUDE_CONTEXT_SUMMARY" \
    '{source_url:$source_url, output_format:$output_format, agent_runtime:$agent_runtime, include_context_summary:$include_context_summary}')

  local body_file
  body_file=$(mktemp)
  local http_code
  http_code=$(curl -sS --max-time "$HTTP_TIMEOUT_SECONDS" \
    -w '%{http_code}' \
    -X POST \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json' \
    -o "$body_file" \
    --data "$payload" \
    "$V2_BASE_URL/v2/extract" 2>>"$log_file") || http_code="000"

  if [[ "$http_code" != "202" ]]; then
    {
      printf '[%s] submit failed http=%s\n' "$(date +%T)" "$http_code"
      cat "$body_file"
      printf '\n'
    } >>"$log_file"
    rm -f "$body_file"
    return 1
  fi

  local job_id
  job_id=$(jq -r '.job_id // empty' "$body_file")
  rm -f "$body_file"
  if [[ -z "$job_id" ]]; then
    printf '[%s] submit returned no job_id\n' "$(date +%T)" >>"$log_file"
    return 1
  fi
  printf '%s' "$job_id"
}

# Poll /v2/jobs/{id} until status is terminal or JOB_TIMEOUT_SECONDS elapses.
# On terminal status, writes the full job record to $out_file and prints the
# terminal status to stdout. Returns non-zero on poll failure / timeout.
poll_job() {
  local job_id=$1
  local out_file=$2
  local log_file=$3
  local started=$SECONDS
  local body_file
  body_file=$(mktemp)
  while :; do
    local http_code
    http_code=$(curl -sS --max-time "$HTTP_TIMEOUT_SECONDS" \
      -w '%{http_code}' \
      -H 'Accept: application/json' \
      -o "$body_file" \
      "$V2_BASE_URL/v2/jobs/$job_id" 2>>"$log_file") || http_code="000"

    if [[ "$http_code" != "200" ]]; then
      {
        printf '[%s] poll http=%s job=%s\n' "$(date +%T)" "$http_code" "$job_id"
        cat "$body_file"
        printf '\n'
      } >>"$log_file"
      rm -f "$body_file"
      return 1
    fi

    local status
    status=$(jq -r '.status // empty' "$body_file")
    case "$status" in
      completed|failed)
        jq . "$body_file" >"$out_file" 2>>"$log_file" || cp "$body_file" "$out_file"
        rm -f "$body_file"
        printf '%s' "$status"
        return 0
        ;;
      pending|running)
        printf '[%s] %-8s job=%s\n' "$(date +%T)" "$status" "$job_id" >>"$log_file"
        ;;
      *)
        printf '[%s] unexpected status=%s job=%s\n' "$(date +%T)" "$status" "$job_id" >>"$log_file"
        ;;
    esac

    local elapsed=$((SECONDS - started))
    if (( elapsed >= JOB_TIMEOUT_SECONDS )); then
      printf '[%s] timeout after %ds job=%s\n' "$(date +%T)" "$elapsed" "$job_id" >>"$log_file"
      rm -f "$body_file"
      return 1
    fi
    sleep "$POLL_INTERVAL_SECONDS"
  done
}

extract_one() {
  local repo_url=$1
  local slug
  slug=$(slug_for "$repo_url")
  local out_file="$run_dir/$slug.json"
  local log_file="$log_dir/$slug.log"

  if already_done "$out_file"; then
    local prior
    prior=$(jq -r '.status' "$out_file")
    printf '[%s] skip %s (prior status=%s)\n' "$(date +%T)" "$slug" "$prior" | tee -a "$log_file"
    printf 'SKIP %-9s %s\n' "$prior" "$slug" >>"$summary_file"
    return 0
  fi

  printf '[%s] submit %s\n' "$(date +%T)" "$slug" | tee -a "$log_file"
  local start_ts=$SECONDS
  local job_id
  job_id=$(submit_job "$repo_url" "$log_file") || job_id=""
  if [[ -z "$job_id" ]]; then
    local elapsed=$((SECONDS - start_ts))
    printf '[%s] FAIL submit %s (%ds)\n' "$(date +%T)" "$slug" "$elapsed" | tee -a "$log_file"
    printf 'FAIL submit  %3ds  %s\n' "$elapsed" "$slug" >>"$summary_file"
    return 0
  fi

  printf '[%s] polling %s job=%s\n' "$(date +%T)" "$slug" "$job_id" >>"$log_file"
  local terminal
  terminal=$(poll_job "$job_id" "$out_file" "$log_file") || terminal=""
  local elapsed=$((SECONDS - start_ts))

  case "$terminal" in
    completed)
      printf '[%s] OK %s (%ds)\n' "$(date +%T)" "$slug" "$elapsed" | tee -a "$log_file"
      printf 'OK   completed %3ds  %s\n' "$elapsed" "$slug" >>"$summary_file"
      ;;
    failed)
      printf '[%s] FAIL %s (%ds)\n' "$(date +%T)" "$slug" "$elapsed" | tee -a "$log_file"
      printf 'FAIL failed    %3ds  %s\n' "$elapsed" "$slug" >>"$summary_file"
      ;;
    *)
      printf '[%s] FAIL poll %s (%ds)\n' "$(date +%T)" "$slug" "$elapsed" | tee -a "$log_file"
      printf 'FAIL poll      %3ds  %s\n' "$elapsed" "$slug" >>"$summary_file"
      ;;
  esac
}

dispatch() {
  printf 'V2_BASE_URL           : %s\n' "$V2_BASE_URL"
  printf 'OUTPUT_DIR            : %s\n' "$run_dir"
  printf 'PARALLELISM           : %s\n' "$PARALLELISM"
  printf 'POLL_INTERVAL_SECONDS : %s\n' "$POLL_INTERVAL_SECONDS"
  printf 'JOB_TIMEOUT_SECONDS   : %s\n' "$JOB_TIMEOUT_SECONDS"
  printf 'AGENT_RUNTIME         : %s\n' "$AGENT_RUNTIME"
  printf 'OUTPUT_FORMAT         : %s\n' "$OUTPUT_FORMAT"
  printf 'INCLUDE_CONTEXT_SUMMARY: %s\n' "$INCLUDE_CONTEXT_SUMMARY"
  printf 'REPO_LIST             : %s\n' "${REPO_LIST:-<inline default>}"
  printf 'REPOSITORIES          : %s\n' "${#REPOSITORIES[@]}"
  printf '\n'

  local in_flight=0
  for repo in "${REPOSITORIES[@]}"; do
    extract_one "$repo" &
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
echo "=== summary ($summary_file) ==="
cat "$summary_file"
