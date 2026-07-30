#!/usr/bin/env bash
set -euo pipefail

token_file="${GDW_API_TOKEN_SOURCE_FILE:?set GDW_API_TOKEN_SOURCE_FILE to the bearer-token file}"
if [[ ! -r "$token_file" ]]; then
  echo "GDW API token file is not readable" >&2
  exit 1
fi
api_token="$(tr -d '\r\n' < "$token_file")"
if (( ${#api_token} < 32 )); then
  echo "GDW API token must contain at least 32 characters" >&2
  exit 1
fi

mkdir -p output/newman
raw_report="$(mktemp)"
trap 'rm -f "$raw_report"' EXIT
newman run postman/GDW.postman_collection.json \
  --environment postman/GDW.local.postman_environment.json \
  --env-var "api_token=$api_token" \
  --reporters cli,json \
  --reporter-json-export "$raw_report"

python ci/redact_newman_report.py \
  --input "$raw_report" \
  --output output/newman/report.json \
  --secret-file "$token_file"
unset api_token
