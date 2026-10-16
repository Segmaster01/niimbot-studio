# PyInstaller spec: builds dist\NiimbotStudio\NiimbotStudio.exe (one-folder, windowed).
# Usage: pyinstaller --noconfirm NiimbotStudio.spec
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("bleak.backends.winrt")
    + collect_submodules("winrt")
    + ["segno", "barcode", "barcode.codex"]
)

a = Analysis(
    ["Niimbot Studio.pyw"],
    pathex=[],
    binaries=[],
    datas=[("niimbot_studio/resources", "niimbot_studio/resources")],
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter", "unittest", "pydoc", "numpy",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
        "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtNetwork",
    ],
    noarchive=False,
)
# Qt/Pillow pieces pulled in by plugins that a widgets-only app never loads.
DROP = ("opengl32sw.dll", "qt6quick", "qt6qml", "qt6pdf", "qpdf.dll", "qt6virtualkeyboard",
        "qtvirtualkeyboardplugin", "_avif.")
a.binaries = [b for b in a.binaries if not any(d in b[0].lower().replace("\\", "/").split("/")[-1] for d in DROP)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NiimbotStudio",
    icon="niimbot_studio/resources/icon-blue.ico",
    console=False,
    upx=False,
    version="version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="NiimbotStudio")
