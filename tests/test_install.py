import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from ptz_controller.engine import Engine, defaults

ROOT=Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.tools=self.root/'tools';self.tools.mkdir()
        self.home=self.root/'user home';self.home.mkdir()
        self.fixture=self.root/'fixture.AppImage';self.fixture.write_bytes(b'\x7fELFtest-appimage-fixture')
        self.log=self.root/'downloads.log'
        for name in ('bash','uname','mktemp','rm','od','tr','grep','sed','mkdir','install','mv','cp'):
            path=shutil.which(name)
            if path:(self.tools/name).symlink_to(path)
        validator=shutil.which('desktop-file-validate')
        if validator:(self.tools/'desktop-file-validate').symlink_to(validator)
        self.env={**os.environ,'HOME':str(self.home),'PATH':str(self.tools),
                  'GITHUB_REPO':'https://github.com/example/PTZ_Controller',
                  'FIXTURE_APP':str(self.fixture),'FIXTURE_ROOT':str(ROOT),'DOWNLOAD_LOG':str(self.log),
                  'XDG_CONFIG_HOME':str(self.home/'.config'),'XDG_DATA_HOME':str(self.home/'.local/share')}
        self.env.pop('APPIMAGE_ASSET',None);self.env.pop('RELEASE_TAG',None);self.env.pop('GITHUB_REF',None)

    def tearDown(self):self.tmp.cleanup()

    def downloader(self, name):
        path=self.tools/name
        path.write_text('''#!/bin/bash
set -eu
output='';url=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output|-O) output=$2;shift 2 ;;
        https://*) url=$1;shift ;;
        *) shift ;;
    esac
done
printf '%s\\n' "$url" >> "$DOWNLOAD_LOG"
if [[ -n ${FAIL_SUFFIX:-} && $url == *"$FAIL_SUFFIX" ]]; then exit 22; fi
case "$url" in
    *.AppImage) cp "$FIXTURE_APP" "$output" ;;
    */icon.svg) cp "$FIXTURE_ROOT/install/icon.svg" "$output" ;;
    */ptz-controller.desktop) cp "$FIXTURE_ROOT/install/ptz-controller.desktop" "$output" ;;
    *) exit 22 ;;
esac
''')
        path.chmod(0o755)

    def install(self):
        return subprocess.run(['/bin/bash',str(ROOT/'install/install.sh')],env=self.env,
                              text=True,capture_output=True,timeout=10)

    def test_curl_installs_app_icon_launcher_and_preserves_settings(self):
        self.downloader('curl')
        config=self.home/'.config/ptz-controller/config.json';config.parent.mkdir(parents=True)
        config.write_text('existing settings')
        result=self.install();self.assertEqual(result.returncode,0,result.stderr)
        app=self.home/'.local/bin/ptz-controller.AppImage'
        self.assertEqual(app.read_bytes(),self.fixture.read_bytes());self.assertTrue(os.access(app,os.X_OK))
        icon=self.home/'.local/share/icons/hicolor/scalable/apps/ptz-controller.svg'
        self.assertEqual(ET.parse(icon).getroot().tag,'{http://www.w3.org/2000/svg}svg')
        desktop=self.home/'.local/share/applications/ptz-controller.desktop'
        line=next(line[5:] for line in desktop.read_text().splitlines() if line.startswith('Exec='))
        self.assertEqual(shlex.split(line),[str(app),'--config',str(config)])
        self.assertEqual(config.read_text(),'existing settings')
        self.assertIn('/releases/latest/download/PTZ_Controller-',self.log.read_text())
        self.assertIn('raw.githubusercontent.com/example/PTZ_Controller/main/install/icon.svg',self.log.read_text())
        self.assertNotIn('@APPIMAGE@',desktop.read_text())

    def test_wget_fallback_and_xdg_paths(self):
        self.downloader('wget')
        self.env['XDG_CONFIG_HOME']=str(self.home/'settings')
        self.env['XDG_DATA_HOME']=str(self.home/'desktop data')
        config_dir=Path(self.env['XDG_CONFIG_HOME'])/'ptz-controller'
        config_dir.mkdir(parents=True)
        preset=defaults()
        preset['axes'][0]['deadzone']=.37
        preset_path=config_dir/'Studio.json'
        preset_path.write_text(json.dumps(preset))
        original=preset_path.read_bytes()
        result=self.install();self.assertEqual(result.returncode,0,result.stderr)
        desktop=Path(self.env['XDG_DATA_HOME'])/'applications/ptz-controller.desktop'
        line=next(line[5:] for line in desktop.read_text().splitlines() if line.startswith('Exec='))
        launcher_path=Path(shlex.split(line)[2])
        self.assertEqual(launcher_path,config_dir/'config.json')
        self.assertEqual(preset_path.read_bytes(),original)
        engine=Engine(launcher_path)
        self.assertEqual(engine.active_preset,'Studio')
        self.assertEqual(engine.config,preset)
        self.assertFalse(launcher_path.exists())

    def test_failed_download_does_not_replace_existing_installation(self):
        self.downloader('curl');self.env['FAIL_SUFFIX']='icon.svg'
        app=self.home/'.local/bin/ptz-controller.AppImage';app.parent.mkdir(parents=True);app.write_text('old executable')
        result=self.install();self.assertNotEqual(result.returncode,0)
        self.assertEqual(app.read_text(),'old executable')
        self.assertFalse((self.home/'.local/share/applications/ptz-controller.desktop').exists())

    def test_desktop_paths_are_escaped_without_shell_expansion(self):
        self.downloader('curl')
        special=self.root/'home $cash `ticks` %percent \\back "quote';special.mkdir()
        self.env.update(HOME=str(special),XDG_CONFIG_HOME=str(special/'.config'),XDG_DATA_HOME=str(special/'.local/share'))
        result=self.install();self.assertEqual(result.returncode,0,result.stderr)
        desktop=special/'.local/share/applications/ptz-controller.desktop'
        line=next(line[5:] for line in desktop.read_text().splitlines() if line.startswith('Exec='))
        decoded=line.replace('\\\\','\\')  # Desktop Entry string escaping precedes Exec tokenization.
        # Exec quoted arguments also unescape dollar/backtick, unlike Python shlex.
        args=[];token='';quoted=False;escaped=False
        for char in decoded:
            if escaped:token+=char;escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=not quoted
            elif char==' ' and not quoted:
                if token:args.append(token.replace('%%','%'));token=''
            else:token+=char
        if token:args.append(token.replace('%%','%'))
        self.assertFalse(quoted);self.assertFalse(escaped)
        self.assertEqual(args,[str(special/'.local/bin/ptz-controller.AppImage'),'--config',str(special/'.config/ptz-controller/config.json')])
