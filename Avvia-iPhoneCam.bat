@echo off
title iPhoneCam per Windows
cd /d %~dp0
:: Avvio senza finestra nera: tutto gira nella finestra iPhoneCam.
:: Log in iPhoneCam.log · problemi? usa Avvia-iPhoneCam-debug.bat
start "" /min pythonw -m pip install -q -r requirements.txt
timeout /t 8 /nobreak >nul
start "" pythonw -u app.py
