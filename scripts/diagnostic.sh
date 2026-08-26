#!/usr/bin/env bash
# Digital Signage Client Quick Diagnostic Runner
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${SCRIPT_DIR}/../../diagnostic.sh" "$@"
