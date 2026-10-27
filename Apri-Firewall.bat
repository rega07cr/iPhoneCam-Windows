@echo off
:: Consente a Python di ricevere connessioni LAN (profilo privato).
:: Eseguire come amministratore: tasto destro -> Esegui come amministratore
netsh advfirewall firewall show rule name="iPhoneCam" >nul 2>&1
if %errorlevel%==0 (
  echo Regola "iPhoneCam" gia' presente.
) else (
  netsh advfirewall firewall add rule name="iPhoneCam" dir=in action=allow program="%~dp0python.exe" enable=yes profile=private 2>nul
  for /f "tokens=*" %%p in ('where py 2^>nul') do set PYLAUNCHER=%%p
  netsh advfirewall firewall add rule name="iPhoneCam" dir=in action=allow program="C:\Windows\py.exe" enable=yes profile=private 2>nul
  echo Regola firewall aggiunta (profilo privato).
)
echo.
echo NOTA: se usi l'EXE compilato, aggiungi anche dist\iPhoneCam.exe:
echo   Impostazioni -^> Firewall -^> Consenti app -^> iPhoneCam -^> Privata
pause
