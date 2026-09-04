@echo off
REM Build a standalone Undercut.exe (onedir - starts faster than onefile).
pyinstaller --noconfirm --windowed --name "Undercut" ^
  --hidden-import PySide6.QtMultimedia ^
  --hidden-import PySide6.QtMultimediaWidgets ^
  main.py
echo.
echo Build finished. Run dist\Undercut\Undercut.exe
echo NOTE: ffmpeg.exe and ffprobe.exe must be on PATH (or in C:\ffmpeg\bin).
pause
