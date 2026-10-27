@echo off
title iPhoneCam - Installa driver webcam virtuale
cd /d %~dp0
echo ============================================================
echo  Installazione driver "Unity Video Capture" (una tantum)
echo  Servono i permessi di amministratore: accetta la richiesta.
echo ============================================================
call "driver\UnityCapture\Install.bat"
echo.
echo Se hai visto "succeeded", il driver e' pronto.
echo Ora: 1) chiudi e RIAPRI Discord/Zoom/Teams, 2) seleziona
echo "Unity Video Capture" come videocamera.
pause
