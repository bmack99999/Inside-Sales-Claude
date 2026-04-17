#!/bin/bash
cd /Users/bryce/Inside-Sales-Claude
/usr/bin/python3 extract_salesforce.py
/usr/bin/python3 extract_recycled.py
/usr/bin/python3 scripts/extract_team_metrics.py
