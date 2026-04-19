#!/usr/bin/env bash
# Convenience wrapper — the actual macOS build pipeline lives in
# installer/macos/build_pkg.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
exec bash installer/macos/build_pkg.sh "$@"
