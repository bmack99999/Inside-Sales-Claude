#!/bin/bash
# Mac launcher for Inside Sales Dashboard
# Usage: double-click in Finder (after chmod +x start_dashboard.sh)
# Or run: bash start_dashboard.sh

cd "$(dirname "$0")"
python3 app.py
