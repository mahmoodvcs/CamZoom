# PyInstaller spec: builds dist\CamZoom\CamZoom.exe (one folder, no console). Run via tools\build.ps1.

a = Analysis(
    ["camzoom.py"],
    datas=[("models/face_detection_yunet_2023mar.onnx", "models")],
    hiddenimports=["pystray._win32"],
    excludes=["unittest", "pydoc", "pyvirtualcam"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CamZoom",
    icon="assets/camzoom.ico",
    console=False,
    version="assets/version.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="CamZoom")
