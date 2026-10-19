@echo off
rem Build the console helper (dist\daykeeper-cli\: the Claude Code hook and the journal
rem commands; a folder, since it starts three times faster than one file), then the app
rem that carries it as a folder (dist\Daykeeper\, zipped to dist\Daykeeper-win64.zip), and
rem print the zip's SHA256. Fails when the app folder grows past 95 MB (about 71 for the app, 19 for the helper).
cd /d "%~dp0"
rem One Python for the build, the tests and CI: 3.13 (pyproject.toml). The launcher picks it
rem when it is installed; otherwise whatever `python` is, and the version is printed.
if not exist .venv\Scripts\python.exe (
    py -3.13 -m venv .venv 2>nul || python -m venv .venv
    .venv\Scripts\python -m pip install -q --require-hashes -r requirements-build.txt
)
.venv\Scripts\python --version
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onedir --console --noupx --name daykeeper-cli --icon assets\icon.ico --exclude-module tkinter --exclude-module cryptography --exclude-module argon2 --exclude-module PySide6 --exclude-module shiboken6 cli_main.py || exit /b 1
rem The app replaces an installed helper when this stamp differs.
.venv\Scripts\python -c "import time, uuid; open(r'dist\daykeeper-cli\build-id.txt', 'w').write(time.strftime('%%Y%%m%%d-%%H%%M%%S-') + uuid.uuid4().hex[:8])" || exit /b 1
.venv\Scripts\python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\Daykeeper packaging\daykeeper.spec || exit /b 1
.venv\Scripts\python packaging\check_size.py dist\Daykeeper 95 || exit /b 1
rem the zip holds the Daykeeper folder itself, as the 1.x folder build did
.venv\Scripts\python -c "import shutil; shutil.make_archive(r'dist\Daykeeper-win64', 'zip', 'dist', 'Daykeeper')" || exit /b 1
rem The installer, when Inno Setup's compiler is on PATH (iscc; CI installs it): dist\Daykeeper-<version>-setup.exe
where iscc >nul 2>nul && (
    for /f %%v in ('.venv\Scripts\python -c "import daykeeper; print(daykeeper.__version__)"') do (
        for /f "delims=-" %%n in ("%%v") do iscc /Q "/DAppVersion=%%v" "/DAppNumericVersion=%%n" "/DSourceDir=%~dp0dist\Daykeeper" "/O%~dp0dist" packaging\daykeeper.iss || exit /b 1
    )
) || echo No iscc on PATH: the installer is not built.
echo.
echo Done: %~dp0dist\Daykeeper\Daykeeper.exe and %~dp0dist\Daykeeper-win64.zip
certutil -hashfile dist\Daykeeper-win64.zip SHA256 | findstr /v "hashfile"
