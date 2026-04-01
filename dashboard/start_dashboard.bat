@echo off
title SkyTab Inside Sales Dashboard
echo Starting SkyTab Inside Sales Dashboard...
echo.
echo Open your browser to: http://localhost:5000
echo Press Ctrl+C to stop the server.
echo.
cd /d "%~dp0"
py -m flask --app app.py run
pause
