@echo off
title Build iPhoneCam EXE
cd /d %~dp0
py -m pip install -r requirements.txt
py -m PyInstaller --noconfirm --clean --onefile --name iPhoneCam ^
  --add-data "dashboard.html;." --add-data "phone.html;." ^
  --collect-submodules cryptography ^
  app.py
echo.
echo Fatto! Trovi iPhoneCam.exe in dist\
pause
