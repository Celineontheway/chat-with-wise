#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check python3
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found" >&2; exit 1
fi

# Check requests
python3 -c "import requests" 2>/dev/null || {
  echo "ERROR: requests not installed. Run: pip3 install requests" >&2; exit 1
}

# Check API key
KEY_ENV="${LLM_API_KEY_ENV:-ANTHROPIC_API_KEY}"
if [ -z "${!KEY_ENV:-}" ]; then
  echo "ERROR: $KEY_ENV not set" >&2; exit 1
fi

# Check config
CONFIG="$HOME/.openclaw/feishu-council.json"
if [ ! -f "$CONFIG" ]; then
  echo "ERROR: Config not found: $CONFIG" >&2
  echo "Run: python3 $SCRIPT_DIR/council.py setup" >&2
  exit 1
fi

exec python3 "$SCRIPT_DIR/council.py" "$@"
