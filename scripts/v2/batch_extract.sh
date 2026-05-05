#!/usr/bin/env bash
# Submit /v2/extract jobs in parallel via POST, poll each job to completion,
# and store the full V2ExtractJob record under an output folder.
#
# Resumable: a repo whose <slug>.json already exists in the output folder
# is skipped (regardless of its inner status). Delete the file to force a
# re-run for that repo.
#
# Usage:
#   scripts/v2/batch_extract.sh <agent_runtime> <repo_list> <output_folder>
#
#   <agent_runtime>   one of: rule_based | llm | hybrid
#   <repo_list>       path to a file with one repo URL per line
#                     (blank lines and lines starting with `#` are skipped)
#   <output_folder>   directory where <slug>.json results, _logs/, _summary.txt
#                     are written. Created if missing.
#
# Examples:
#   scripts/v2/batch_extract.sh hybrid data/sdsc-seeds.txt data/run/2026-05-04-sdsc-hybrid
#   scripts/v2/batch_extract.sh rule_based data/infoscience-github-seeds.txt data/run/quick
#
# Tunables (env vars, all optional):
#   V2_BASE_URL                http://localhost:1234
#   PARALLELISM                5
#   POLL_INTERVAL_SECONDS      10
#   JOB_TIMEOUT_SECONDS        1800
#   HTTP_TIMEOUT_SECONDS       60
#   OUTPUT_FORMAT              jsonld
#   INCLUDE_CONTEXT_SUMMARY    false
#   API_TOKEN                  bearer for /v2/extract and /v2/jobs/* routes
#
# Files written into <output_folder>:
#   <owner>_<repo>.json           full V2ExtractJob record (success or failure)
#   _logs/<owner>_<repo>.log      stderr from curl + per-poll status lines
#   _summary.txt                  one-line status per repo at the end

set -u

usage() {
  sed -n '2,29p' "$0"
  exit 2
}

if [[ $# -lt 3 ]]; then
  echo "error: expected 3 positional arguments, got $#" >&2
  echo >&2
  usage
fi

AGENT_RUNTIME=$1
REPO_LIST=$2
OUTPUT_FOLDER=$3
shift 3

case "$AGENT_RUNTIME" in
  rule_based|llm|hybrid) ;;
  *)
    echo "error: <agent_runtime> must be one of rule_based|llm|hybrid (got: $AGENT_RUNTIME)" >&2
    exit 2
    ;;
esac

if [[ ! -f "$REPO_LIST" ]]; then
  echo "error: <repo_list> file not found: $REPO_LIST" >&2
  exit 2
fi

V2_BASE_URL=${V2_BASE_URL:-http://localhost:1234}
PARALLELISM=${PARALLELISM:-5}
POLL_INTERVAL_SECONDS=${POLL_INTERVAL_SECONDS:-10}
JOB_TIMEOUT_SECONDS=${JOB_TIMEOUT_SECONDS:-1800}
HTTP_TIMEOUT_SECONDS=${HTTP_TIMEOUT_SECONDS:-60}
OUTPUT_FORMAT=${OUTPUT_FORMAT:-jsonld}
INCLUDE_CONTEXT_SUMMARY=${INCLUDE_CONTEXT_SUMMARY:-false}
# Optional bearer for the auth-protected /v2/extract and /v2/jobs/* routes.
# When unset, requests go without an Authorization header (works only if
# the server has API_TOKEN unset and V2_USE_MOCK_PROVIDERS=true).
API_TOKEN=${API_TOKEN:-}

REPOSITORIES=()
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  REPOSITORIES+=("$line")
done <"$REPO_LIST"

run_dir="$OUTPUT_FOLDER"
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

# True (return 0) if the output JSON already exists for this slug, so the repo
# can be skipped on resume. Skips regardless of the inner status — delete the
# file to force a re-run.
already_done() {
  local out_file=$1
  [[ -s "$out_file" ]]
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
  local -a auth_args=()
  if [[ -n "$API_TOKEN" ]]; then
    auth_args=(-H "Authorization: Bearer $API_TOKEN")
  fi
  http_code=$(curl -sS --max-time "$HTTP_TIMEOUT_SECONDS" \
    -w '%{http_code}' \
    -X POST \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json' \
    "${auth_args[@]}" \
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
  local -a poll_auth_args=()
  if [[ -n "$API_TOKEN" ]]; then
    poll_auth_args=(-H "Authorization: Bearer $API_TOKEN")
  fi
  while :; do
    local http_code
    http_code=$(curl -sS --max-time "$HTTP_TIMEOUT_SECONDS" \
      -w '%{http_code}' \
      -H 'Accept: application/json' \
      "${poll_auth_args[@]}" \
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
    prior=$(jq -r '.status // "exists"' "$out_file" 2>/dev/null || echo "exists")
    printf '[%s] skip %s (prior status=%s)\n' "$(date +%T)" "$slug" "$prior" >>"$log_file"
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
  printf 'REPO_LIST             : %s\n' "$REPO_LIST"
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
