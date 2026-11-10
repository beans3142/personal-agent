"""Build dist/Daevis.exe (one file). Used by the release workflow and for local builds.

Run from the repository root: python desktop/build.py [--dist DIR]
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def arguments(dist):
    sep = ';'
    return [
        str(ROOT / 'desktop' / 'app.py'), '--noconfirm', '--onefile', '--windowed', '--name', 'Daevis',
        '--icon', str(ROOT / 'desktop' / 'daevis.ico'),
        '--paths', str(ROOT), '--distpath', str(dist), '--workpath', str(Path(dist).parent / 'build'), '--specpath', str(Path(dist).parent),
        '--add-data', f'{ROOT / "desktop" / "ui"}{sep}ui',
        # The voice client and PC power receiver are loaded from these unchanged scripts.
        '--add-data', f'{ROOT / "deploy" / "voice-pc.py"}{sep}.',
        '--add-data', f'{ROOT / "deploy" / "device-node.py"}{sep}.',
        '--collect-all', 'faster_whisper', '--collect-all', 'ctranslate2', '--collect-all', 'webview', '--collect-data', 'certifi',
        '--hidden-import', 'sounddevice', '--hidden-import', 'numpy', '--hidden-import', 'winsound', '--hidden-import', 'edge_tts',
        '--hidden-import', 'clr',
        '--collect-submodules', 'mcp.server', '--collect-submodules', 'mcp.shared', '--collect-submodules', 'mcp.client', '--hidden-import', 'desktop.mcp_server', '--hidden-import', 'desktop.cli',
    ] + [arg for name in EXCLUDE for arg in ('--exclude-module', name)]


# Large packages a developer PC may have that the app never imports.
EXCLUDE = ['torch', 'torchaudio', 'torchvision', 'tensorflow', 'transformers', 'pytest', 'matplotlib', 'pandas',
           'scipy', 'IPython', 'jupyter', 'notebook', 'PIL', 'cv2', 'sklearn', 'playwright', 'discord']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', default=str(ROOT / 'dist'))
    args = parser.parse_args()
    import PyInstaller.__main__
    PyInstaller.__main__.run(arguments(args.dist))


if __name__ == '__main__':
    main()
