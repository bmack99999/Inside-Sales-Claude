#!/bin/bash
cd /Users/bryce/Inside-Sales-Claude
echo "================================================"
echo "  SkyTab Inside Sales — Manual SF Refresh"
echo "  $(date)"
echo "================================================"
echo ""
/usr/bin/python3 extract_salesforce.py
echo ""
echo "================================================"
echo "  Scanning recycled leads (this takes ~2 min)..."
echo "================================================"
echo ""
/usr/bin/python3 extract_recycled.py
echo ""
echo "================================================"
echo "  Updating team leaderboard metrics..."
echo "================================================"
echo ""
/usr/bin/python3 scripts/extract_team_metrics.py
echo ""
echo "Done! You can close this window."
