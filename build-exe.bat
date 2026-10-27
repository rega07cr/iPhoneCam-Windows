@echo off
title Build iPhoneCam EXE
cd /d %~dp0
py -m pip install -r requirements.txt
py -m PyInstaller --noconfirm --clean --onefile --windowed --name iPhoneCam ^
  --icon assets\icon.ico --version-file version-info.txt ^
  --add-data "dashboard.html;." --add-data "phone.html;." ^
  --add-data "driver\UnityCapture;driver\UnityCapture" ^
  --collect-all pyvirtualcam ^
  app.py
echo.
echo Fatto! Trovi iPhoneCam.exe in dist\
pause
