#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../frontend"
python3 -m http.server 5178
