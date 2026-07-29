#!/usr/bin/env bash
set -euo pipefail

mkdir -p output/newman
newman run postman/GDW.postman_collection.json \
  --environment postman/GDW.local.postman_environment.json \
  --reporters cli,json \
  --reporter-json-export output/newman/report.json

