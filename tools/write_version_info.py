"""Write version_info.txt (Windows file version resource for the .exe) from niimbot_studio.__version__."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
version = re.search(r'__version__ = "([^"]+)"', (ROOT / "niimbot_studio" / "__init__.py").read_text()).group(1)
nums = tuple(int(n) for n in re.findall(r"\d+", version)[:3]) + (0,)
nums = (nums + (0, 0, 0, 0))[:4]

(ROOT / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Niimbot Studio'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'NiimbotStudio'),
      StringStruct('OriginalFilename', 'NiimbotStudio.exe'),
      StringStruct('ProductName', 'Niimbot Studio'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
print(version)
