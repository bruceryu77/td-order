@echo off
cd /d "%~dp0"
echo.
echo  T^&D Admin Server
echo  - Admin:  http://127.0.0.1:8765/admin.html
echo  - Order:  http://127.0.0.1:8765/index.html
echo  - Save in Admin auto-pushes catalog to GitHub.
echo.
start "" "http://127.0.0.1:8765/admin.html"
python admin_server.py
pause
