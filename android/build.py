"""Build daevis.apk with the Android SDK command-line tools only (no Gradle).

  python android/build.py [--out DIR] [--version-code N] [--version-name X]
  python android/build.py --test        # plain-JDK logic tests only

Needs ANDROID_HOME (platforms;android-35 and build-tools) and a JDK 17+. The page is the
desktop app's desktop/ui/index.html, copied into assets so both apps show the same screen.
Signing key: DAEVIS_KEYSTORE / DAEVIS_KEY_PASS if set (CI reads them from the repository secrets
DAEVIS_ANDROID_KEYSTORE_B64 / DAEVIS_ANDROID_KEY_PASS), otherwise a local key made once in
~/.daevis-android/release.jks. Android only installs an update signed with the same key.
"""
import argparse
import glob
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXE = '.exe' if os.name == 'nt' else ''
BAT = '.bat' if os.name == 'nt' else ''


def run(*args):
    print('+', ' '.join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True)


def sdk():
    home = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or '')
    platforms = sorted(home.glob('platforms/android-3[4-9]'), key=lambda p: int(p.name.split('-')[1]))
    tools = sorted(home.glob('build-tools/3[4-9].*'), key=lambda p: [int(x) for x in p.name.split('-')[0].split('.')])
    if not platforms or not tools:
        raise SystemExit('Android SDK with platforms;android-34+ and build-tools 34+ is required (set ANDROID_HOME).')
    return platforms[-1] / 'android.jar', tools[-1]


def test(out):
    classes = out / 'test-classes'
    classes.mkdir(parents=True, exist_ok=True)
    run('javac', '-encoding', 'UTF-8', '-d', classes, HERE / 'src/io/daevis/app/Logic.java', HERE / 'test/LogicTest.java')
    run('java', '-Dstdout.encoding=UTF-8', '-cp', classes, 'LogicTest')


def keystore(out):
    path = os.environ.get('DAEVIS_KEYSTORE')
    password = os.environ.get('DAEVIS_KEY_PASS', 'daevis-local')
    if path:
        return Path(path), password
    path = Path.home() / '.daevis-android' / 'release.jks'
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        run('keytool', '-genkeypair', '-keystore', path, '-storepass', password, '-keypass', password, '-alias', 'daevis',
            '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000', '-dname', 'CN=Daevis')
    return path, password


def build(out, version_code, version_name):
    android_jar, tools = sdk()
    if out.exists():
        shutil.rmtree(out)
    (out / 'assets').mkdir(parents=True)
    shutil.copy(ROOT / 'desktop/ui/index.html', out / 'assets/index.html')
    run(tools / ('aapt2' + EXE), 'compile', '--dir', HERE / 'res', '-o', out / 'res.zip')
    run(tools / ('aapt2' + EXE), 'link', '-o', out / 'base.apk', '-I', android_jar, '--manifest', HERE / 'AndroidManifest.xml',
        '-A', out / 'assets', '--java', out / 'gen', '--min-sdk-version', '26', '--target-sdk-version', '35',
        '--version-code', version_code, '--version-name', version_name, out / 'res.zip')
    sources = [str(p) for p in (HERE / 'src').rglob('*.java')] + glob.glob(str(out / 'gen/**/*.java'), recursive=True)
    run('javac', '-encoding', 'UTF-8', '-source', '11', '-target', '11', '-Xlint:-options', '-classpath', android_jar, '-d', out / 'classes', *sources)
    (out / 'dex').mkdir()
    run(tools / ('d8' + BAT), '--release', '--min-api', '26', '--lib', android_jar, '--output', out / 'dex',
        *[str(p) for p in (out / 'classes').rglob('*.class')])
    with zipfile.ZipFile(out / 'base.apk', 'a') as apk:
        apk.write(out / 'dex/classes.dex', 'classes.dex')
    run(tools / ('zipalign' + EXE), '-p', '-f', '4', out / 'base.apk', out / 'aligned.apk')
    store, password = keystore(out)
    run(tools / ('apksigner' + BAT), 'sign', '--ks', store, '--ks-pass', 'pass:' + password, '--key-pass', 'pass:' + password,
        '--out', out / 'daevis.apk', out / 'aligned.apk')
    run(tools / ('apksigner' + BAT), 'verify', '--print-certs', out / 'daevis.apk')
    print('built', out / 'daevis.apk', (out / 'daevis.apk').stat().st_size, 'bytes')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(ROOT / 'build' / 'android'))
    parser.add_argument('--version-code', default='1')
    parser.add_argument('--version-name', default='0.1.0')
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    out = Path(args.out)
    test(out.parent / 'android-test')
    if not args.test:
        build(out, args.version_code, args.version_name)


if __name__ == '__main__':
    sys.exit(main())
