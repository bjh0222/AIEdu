@echo off
setlocal
"%~dp0..\services\api\.venv\Scripts\python.exe" -B "%~dp0local_embedding.py" %*
exit /b %errorlevel%
