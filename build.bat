@echo off
REM Build a standalone Undercut.exe (onedir - starts faster than onefile).
pyinstaller --noconfirm --windowed --name "Undercut" ^
  --icon "app\icon.ico" ^
  --add-data "app\icon.ico;app" ^
  --add-data "app\icon.png;app" ^
  main.py
echo.
echo Build finished. Run dist\Undercut\Undercut.exe
echo NOTE: ffmpeg.exe must be on PATH (or in C:\ffmpeg\bin).
pause
