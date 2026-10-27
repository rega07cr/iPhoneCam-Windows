@echo off
title iPhoneCam - Rimuovi driver webcam virtuale
cd /d %~dp0
call "driver\UnityCapture\Uninstall.bat"
echo.
echo Driver rimosso.
pause
