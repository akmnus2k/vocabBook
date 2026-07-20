@echo off
rem Open PT vocab book: start the server if it is not running, then open the browser
netstat -ano | findstr ":8511" | findstr "LISTENING" >nul
if not errorlevel 1 goto open

wscript "D:\JProjects\vocabBook\start_server.vbs"
set /a tries=0
:wait
timeout /t 1 /nobreak >nul
set /a tries+=1
netstat -ano | findstr ":8511" | findstr "LISTENING" >nul
if not errorlevel 1 goto open
if %tries% lss 20 goto wait

:open
start "" http://localhost:8511
