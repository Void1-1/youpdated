# PyInstaller's recipe for the engine the desktop apps bundle
import os

from PyInstaller.utils.hooks import collect_submodules

analysis = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.environ["YOUPDATED_ENGINE_SOURCE"]],
    hiddenimports=[
        # build.py writes this; api.py imports it by name
        "youpdated._build",
        # rich picks its Unicode tables by name at runtime
        *collect_submodules("rich._unicode_data"),
    ],
    # never used by the engine
    excludes=["tkinter", "test", "unittest", "pydoc", "pip", "setuptools"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="youpdated-engine",
    console=True,
    upx=False,
)
COLLECT(exe, analysis.binaries, analysis.datas, name="youpdated-engine", upx=False)
