#!/bin/bash
# Cron runs without HOME set on macOS, which breaks the `sf` CLI wrapper
# (it does `cd "$HOME"` internally and `sf data query` fails with
# NamedOrgNotFoundError because it can't read ~/.sf auth). Force it.
export HOME="${HOME:-/Users/bryce}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

set -a
source ~/.zshrc 2>/dev/null || source ~/.bashrc 2>/dev/null || true
set +a
cd /Users/bryce/Inside-Sales-Claude
/usr/bin/python3 extract_salesforce.py
/usr/bin/python3 extract_recycled.py
/usr/bin/python3 scripts/extract_team_metrics.py
/usr/bin/python3 scripts/extract_commissions.py
/usr/bin/python3 scripts/morning_briefing.py
