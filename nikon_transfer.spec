# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Nikon Transfer GUI.

Build:    pyinstaller nikon_transfer.spec --noconfirm
Output:   dist/Nikon Transfer.app
"""

a = Analysis(
    ['nikon_transfer_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim weight: GUI doesn't need any of these.
    excludes=[
        'tkinter', 'pytest', 'unittest',
        'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtTest', 'PySide6.Qt3DCore',
        'PySide6.QtCharts', 'PySide6.QtDataVisualization',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Nikon Transfer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Nikon Transfer',
)
app = BUNDLE(
    coll,
    name='Nikon Transfer.app',
    icon='assets/icon.icns',
    bundle_identifier='com.dobbelaere.nikon-transfer',
    info_plist={
        'CFBundleName': 'Nikon Transfer',
        'CFBundleDisplayName': 'Nikon Transfer D5300',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        # Let Qt follow the system appearance (dark/light).
        'NSRequiresAquaSystemAppearance': False,
        'LSMinimumSystemVersion': '11.0',
        # macOS asks the user once when the app first joins the LAN to talk to the camera.
        'NSLocalNetworkUsageDescription':
            'Connexion Wi-Fi à la caméra Nikon D5300 sur le réseau local.',
    },
)
