@echo off
cd /d "%~dp0"
echo ================================================
echo   SkyTab Inside Sales - SF Extraction
echo   %date% %time%
echo ================================================
echo.

echo [1/4] Extracting leads and opportunities...
python extract_salesforce.py
echo.

echo [2/4] Scanning recycled leads...
python extract_recycled.py
echo.

echo [3/4] Updating team metrics...
python scripts\extract_team_metrics.py
echo.

echo [4/4] Sending briefing...
python scripts\morning_briefing.py
echo.

echo Done!
