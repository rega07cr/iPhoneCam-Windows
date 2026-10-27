@echo off
title iPhoneCam per Windows (debug)
cd /d %~dp0
py -m pip install -q -r requirements.txt
py -u app.py
pause
