#!/usr/bin/env python3
"""Transfer a BINGIE Lite package to an Android Kodi device for installation."""

import argparse
import hashlib
import json
import re
import shlex
import signal
import socket
import subprocess
import sys
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

ADDON_ID = 'skin.titan.bingie.lite'
ADDON_DIR = '/sdcard/Android/data/org.xbmc.kodi/files/.kodi/addons/' + ADDON_ID
LOG = '/sdcard/Android/data/org.xbmc.kodi/files/.kodi/temp/kodi.log'
DEVICES = {'googletv': ('192.168.1.114:5555', 'Google TV Streamer'), 'shield': ('192.168.1.128:5555', 'SHIELD Android TV'), 'tcl': ('192.168.1.42:5555', 'Smart TV Pro')}


class DeployError(Exception):
    pass


def adb(serial, *args, check=True):
    result = subprocess.run(['adb', '-s', serial, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        raise DeployError('ADB command failed: ' + ' '.join(args[:2]))
    return result


def shell(serial, command, check=True):
    return adb(serial, 'shell', command, check=check)


def rpc(host, method, params):
    try:
        with socket.create_connection((host, 9090), timeout=2) as conn:
            conn.settimeout(2)
            conn.sendall((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}) + '\n').encode())
            data = ''
            while len(data) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk.decode('utf-8')
                try:
                    response, _ = json.JSONDecoder().raw_decode(data.lstrip())
                    return response.get('result')
                except json.JSONDecodeError:
                    pass
    except (OSError, UnicodeError):
        pass
    return None


def addon_details(serial):
    result = rpc(serial.split(':')[0], 'Addons.GetAddonDetails', {'addonid': ADDON_ID, 'properties': ['version', 'enabled']})
    if result and 'addon' in result:
        addon = result['addon']
        return addon.get('version'), addon.get('enabled')
    result = adb(serial, 'exec-out', 'cat', ADDON_DIR + '/addon.xml', check=False)
    if result.returncode:
        addons_dir = ADDON_DIR.rsplit('/', 1)[0]
        probe = shell(serial, 'if [ ! -r ' + shlex.quote(addons_dir) + ' ] || [ ! -x ' + shlex.quote(addons_dir) + ' ]; then exit 2; elif [ -e ' + shlex.quote(ADDON_DIR) + ' ] || [ -L ' + shlex.quote(ADDON_DIR) + ' ]; then exit 3; else exit 0; fi', check=False)
        if probe.returncode != 0:
            raise DeployError('Installed add-on is present or its directory is inaccessible; cannot check its manifest.')
        return None, None
    try:
        root = ET.fromstring(result.stdout)
        if root.attrib.get('id') != ADDON_ID:
            raise DeployError('Installed manifest has an unexpected add-on ID.')
        return root.attrib['version'], None
    except (ET.ParseError, KeyError) as exc:
        raise DeployError('Cannot read the installed add-on version.') from exc


def package_contents(path):
    if not path.is_file():
        raise DeployError('Package does not exist: ' + str(path))
    result = subprocess.run(['unzip', '-t', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise DeployError('Package failed unzip -t.')
    try:
        with zipfile.ZipFile(path) as package:
            members = package.infolist()
            paths = [item.filename for item in members]
            if len(paths) != len(set(paths)):
                raise DeployError('Package contains duplicate paths.')
            expected = {}
            for item in members:
                parts = PurePosixPath(item.filename).parts
                if not parts or parts[0] != ADDON_ID or '..' in parts or item.filename.startswith('/'):
                    raise DeployError('Package must contain only the add-on directory and safe paths.')
                if not item.is_dir():
                    relative = '/'.join(parts[1:])
                    if not relative:
                        raise DeployError('Package contains an invalid file path.')
                    expected[relative] = hashlib.sha256(package.read(item)).digest()
            root = ET.fromstring(package.read(ADDON_ID + '/addon.xml'))
            if root.attrib.get('id') != ADDON_ID or not root.attrib.get('version'):
                raise DeployError('Package manifest has an unexpected add-on ID or no version.')
            return root.attrib['version'], expected
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise DeployError('Cannot read package or its manifest.') from exc


def compare_installed(serial, expected):
    process = subprocess.Popen(['adb', '-s', serial, 'exec-out', 'tar', '-c', '-C', ADDON_DIR, '.'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    found = {}
    existing_private = set()
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for item in archive:
                name = item.name.removeprefix('./')
                if name.startswith('resources/private/') and not item.isdir():
                    existing_private.add(name)
                if name in expected and item.isfile():
                    source = archive.extractfile(item)
                    if source:
                        digest = hashlib.sha256()
                        for chunk in iter(lambda: source.read(1024 * 1024), b''):
                            digest.update(chunk)
                        found[name] = digest.digest()
    except (tarfile.TarError, OSError) as exc:
        process.kill()
        process.wait()
        raise DeployError('Cannot read the installed add-on files over ADB.') from exc
    finally:
        process.stdout.close()
    if process.wait() != 0:
        raise DeployError('Cannot read the installed add-on files over ADB.')
    missing = set(expected) - set(found)
    different = {name for name, digest in found.items() if digest != expected[name]}
    different |= missing & existing_private
    extra_private = existing_private - set(expected)
    return missing, different, extra_private


def log_bytes(serial):
    result = adb(serial, 'exec-out', 'cat', LOG, check=False)
    if result.returncode:
        raise DeployError('Cannot read the Kodi log on this device.')
    return result.stdout


def deployment_log_ok(before, after, version):
    if not after.startswith(before):
        return False
    new = after[len(before):].decode('utf-8', errors='replace')
    markers = ['CAddonMgr::FindAddon: ' + ADDON_ID + ' v' + version + ' installed', 'version: ' + version + ')', 'Main Monitor Service Starting (' + ADDON_ID + '-' + version + ')']
    position = 0
    for marker in markers:
        position = new.find(marker, position)
        if position < 0:
            return False
        position += len(marker)
    return True


def rpc_required(serial, method, params):
    result = rpc(serial.split(':')[0], method, params)
    if result is None:
        raise DeployError('Kodi did not respond to ' + method + '.')
    return result


def wait_for_kodi(serial, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        props = rpc(serial.split(':')[0], 'GUI.GetProperties', {'properties': ['currentwindow', 'currentcontrol']})
        players = rpc(serial.split(':')[0], 'Player.GetActivePlayers', {})
        if isinstance(props, dict) and isinstance(players, list):
            stop_playback(serial, players)
            return
        time.sleep(1)
    raise DeployError('Kodi JSON-RPC did not become ready.')


def ensure_kodi(serial):
    check_playback_before_permission_change(serial)
    shell(serial, 'am start -n org.xbmc.kodi/.Splash')
    wait_for_kodi(serial)


def ui_properties(serial):
    props = rpc_required(serial, 'GUI.GetProperties', {'properties': ['currentwindow', 'currentcontrol']})
    players = rpc_required(serial, 'Player.GetActivePlayers', {})
    if not isinstance(props, dict) or not isinstance(players, list) or players:
        raise DeployError('Kodi UI or playback state cannot be safely checked.')
    window = props.get('currentwindow', {})
    control = props.get('currentcontrol', {})
    if not isinstance(window, dict) or not isinstance(control, dict) or not isinstance(window.get('id'), int):
        raise DeployError('Kodi did not report a usable window or control.')
    return window['id'], control.get('label', '')


def ui_action(serial, method):
    result = rpc_required(serial, method, {})
    if result != 'OK':
        raise DeployError('Kodi did not accept ' + method + '.')


def wait_ui(serial, predicate, description, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.3)
    raise DeployError('Timed out waiting for ' + description + '.')


def picker_state(serial):
    window, _ = ui_properties(serial)
    if window != 10126:
        raise DeployError('Kodi left the ZIP file picker unexpectedly.')
    booleans = rpc_required(serial, 'XBMC.GetInfoBooleans', {'booleans': ['Control.HasFocus(450)', 'Window.IsVisible(filebrowser)']})
    if not isinstance(booleans, dict) or not all(booleans.get(key) is True for key in ('Control.HasFocus(450)', 'Window.IsVisible(filebrowser)')):
        raise DeployError('Kodi file picker is not visible or its list is not focused.')
    labels = ['Container(450).NumItems', 'Container(450).CurrentItem', 'Container(450).ListItem.Label', 'Container(450).ListItem.FileNameAndPath']
    values = rpc_required(serial, 'XBMC.GetInfoLabels', {'labels': labels})
    try:
        count = int(values[labels[0]])
        current = int(values[labels[1]])
        label = values[labels[2]]
        path = values[labels[3]]
    except (KeyError, ValueError, TypeError) as exc:
        raise DeployError('Kodi did not report its file picker contents.') from exc
    if count < 0 or count > 10000 or current < 0 or current > count:
        raise DeployError('Kodi reported an unexpected file picker position.')
    return count, current, label, path


def picker_find(serial, target):
    count, current, _, _ = picker_state(serial)
    labels = ['Container(450).ListItemAbsolute(%d).FileNameAndPath' % i for i in range(count + 1)]
    values = rpc_required(serial, 'XBMC.GetInfoLabels', {'labels': labels})
    if not isinstance(values, dict) or any(name not in values for name in labels):
        raise DeployError('Kodi did not report all ZIP file picker paths.')
    matches = [i for i, name in enumerate(labels) if values[name] == target]
    if len(matches) > 1:
        raise DeployError('Kodi returned duplicate matching ZIP picker paths.')
    return (count, current, matches[0]) if matches else None


def picker_target(serial, target, before_select=None):
    found = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        found = picker_find(serial, target)
        if found is not None:
            break
        time.sleep(0.3)
    if found is None:
        raise DeployError('Expected path was not found in the Kodi file picker: ' + target)
    count, current, target_index = found
    while current != target_index:
        previous = current
        assert_ui_safe(serial, 10126)
        ui_action(serial, 'Input.Down' if current < target_index else 'Input.Up')
        wait_ui(serial, lambda: picker_state(serial)[1] != previous, 'the file picker selection to move')
        count2, current, _, _ = picker_state(serial)
        if count2 != count or abs(current - previous) != 1:
            raise DeployError('Kodi file picker changed unexpectedly while moving.')
    _, _, _, selected = picker_state(serial)
    if selected != target:
        raise DeployError('Kodi selected an unexpected path; installation stopped.')
    assert_ui_safe(serial, 10126)
    if before_select is not None:
        before_select()
    ui_action(serial, 'Input.Select')


def dialog_is_clear(serial):
    ui_properties(serial)
    keys = ['Window.IsVisible(busydialog)', 'Window.IsVisible(busydialognocancel)', 'System.HasModalDialog']
    values = rpc_required(serial, 'XBMC.GetInfoBooleans', {'booleans': keys})
    if not isinstance(values, dict) or any(key not in values or not isinstance(values[key], bool) for key in keys):
        raise DeployError('Kodi did not report whether a dialog is active.')
    return not any(values[key] for key in keys)


def assert_ui_safe(serial, expected_window):
    window, _ = ui_properties(serial)
    if window != expected_window:
        raise DeployError('Kodi is in an unexpected window; installation stopped.')
    if not dialog_is_clear(serial):
        raise DeployError('Kodi has a busy or modal dialog; installation stopped.')


def menu_path(serial):
    assert_ui_safe(serial, 10040)
    keys = ['Control.HasFocus(50)']
    values = rpc_required(serial, 'XBMC.GetInfoBooleans', {'booleans': keys})
    if not isinstance(values, dict) or values.get(keys[0]) is not True:
        raise DeployError('Kodi add-on browser list is not focused.')
    key = 'Container(50).ListItem.FileNameAndPath'
    values = rpc_required(serial, 'XBMC.GetInfoLabels', {'labels': [key]})
    if not isinstance(values, dict) or not isinstance(values.get(key), str) or not values[key]:
        raise DeployError('Kodi did not report the selected add-on browser item.')
    return values[key]


def open_zip_picker(serial):
    wait_ui(serial, lambda: dialog_is_clear(serial), 'Kodi to finish loading', seconds=90)
    result = rpc_required(serial, 'GUI.ActivateWindow', {'window': 'addonbrowser'})
    if result != 'OK':
        raise DeployError('Kodi did not open the add-on browser.')
    wait_ui(serial, lambda: ui_properties(serial)[0] == 10040, 'the add-on browser')
    target = 'addons://install/'
    for direction in ('Input.Down', 'Input.Up'):
        for _ in range(6):
            current = menu_path(serial)
            if current == target:
                break
            ui_action(serial, direction)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and menu_path(serial) == current:
                time.sleep(0.1)
        if menu_path(serial) == target:
            break
    if menu_path(serial) != target:
        raise DeployError('Install from zip file was not found in the add-on browser.')
    ui_action(serial, 'Input.Select')
    wait_ui(serial, lambda: ui_properties(serial)[0] == 10126, 'the ZIP file picker')


def install_zip(serial, remote_name, before_select):
    open_zip_picker(serial)
    picker_target(serial, '/storage/emulated/0/')
    picker_target(serial, '/storage/emulated/0/Download/')
    picker_target(serial, '/storage/emulated/0/Download/' + remote_name, before_select=before_select)


def addon_browser_path(serial):
    window, _ = ui_properties(serial)
    if window != 10040:
        return None
    key = 'Container.FolderPath'
    values = rpc_required(serial, 'XBMC.GetInfoLabels', {'labels': [key]})
    if not isinstance(values, dict) or not isinstance(values.get(key), str):
        raise DeployError('Kodi did not report its add-on browser path.')
    return values[key]


def addon_jobs_complete(serial):
    # A real directory transition forces Kodi to rebuild the browser root.
    if not dialog_is_clear(serial):
        return False
    repos = {'window': 'addonbrowser', 'parameters': ['addons://repos/']}
    if rpc_required(serial, 'GUI.ActivateWindow', repos) != 'OK':
        raise DeployError('Kodi did not open add-on repositories while checking installation jobs.')
    wait_ui(serial, lambda: addon_browser_path(serial) == 'addons://repos/', 'the add-on repository list')
    if not dialog_is_clear(serial):
        return False
    root = {'window': 'addonbrowser', 'parameters': ['addons://']}
    if rpc_required(serial, 'GUI.ActivateWindow', root) != 'OK':
        raise DeployError('Kodi did not open the add-on browser root while checking installation jobs.')
    wait_ui(serial, lambda: addon_browser_path(serial) == 'addons://', 'the add-on browser root')
    assert_ui_safe(serial, 10040)
    count_key = 'Container(50).NumItems'
    values = rpc_required(serial, 'XBMC.GetInfoLabels', {'labels': [count_key]})
    try:
        count = int(values[count_key])
    except (KeyError, TypeError, ValueError) as exc:
        raise DeployError('Kodi did not report its add-on browser root items.') from exc
    if not 1 <= count <= 1000:
        raise DeployError('Kodi reported an unexpected add-on browser root size.')
    keys = ['Container(50).ListItemAbsolute(%d).FileNameAndPath' % i for i in range(count)]
    values = rpc_required(serial, 'XBMC.GetInfoLabels', {'labels': keys})
    if not isinstance(values, dict) or any(not isinstance(values.get(key), str) or not values[key] for key in keys):
        raise DeployError('Kodi did not report the complete add-on browser root.')
    paths = [values[key] for key in keys]
    if paths.count('addons://install/') != 1:
        raise DeployError('Kodi did not show the expected add-on browser root.')
    return 'addons://downloading/' not in paths


def storage_permission(serial):
    result = shell(serial, 'appops get org.xbmc.kodi MANAGE_EXTERNAL_STORAGE')
    output = result.stdout.decode('utf-8', errors='replace').strip()
    match = re.search(r'(?m)^MANAGE_EXTERNAL_STORAGE:\s*(allow|default|deny|ignore)(?:[;\s]|$)', output)
    if match:
        return match.group(1)
    if output == 'No operations.':
        return 'default'
    raise DeployError('Cannot determine Kodi external-storage permission.')


def set_storage_permission(serial, permission):
    shell(serial, 'appops set org.xbmc.kodi MANAGE_EXTERNAL_STORAGE ' + permission)
    if storage_permission(serial) != permission:
        raise DeployError('Could not set Kodi external-storage permission to ' + permission + '.')


def stop_playback(serial, players):
    if not isinstance(players, list):
        raise DeployError('Kodi playback state cannot be checked.')
    for player in players:
        if not isinstance(player, dict) or not isinstance(player.get('playerid'), int):
            raise DeployError('Kodi returned an invalid active player.')
        result = rpc_required(serial, 'Player.Stop', {'playerid': player['playerid']})
        if result != 'OK':
            raise DeployError('Kodi did not stop playback.')
    if players:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            remaining = rpc_required(serial, 'Player.GetActivePlayers', {})
            if not isinstance(remaining, list):
                raise DeployError('Kodi playback state cannot be checked.')
            if not remaining:
                return
            time.sleep(0.5)
        raise DeployError('Kodi playback did not stop.')


def check_playback_before_permission_change(serial):
    players = rpc(serial.split(':')[0], 'Player.GetActivePlayers', {})
    if players is None:
        process = shell(serial, 'pidof org.xbmc.kodi', check=False)
        if process.returncode not in (0, 1) or (process.returncode == 0 and process.stdout.strip()):
            raise DeployError('Kodi is running or its process state cannot be checked; playback cannot be verified.')
    else:
        stop_playback(serial, players)


def deploy(args):
    serial, model = DEVICES[args.target]
    version, expected = package_contents(args.archive)
    state = adb(serial, 'get-state', check=False)
    if state.returncode or state.stdout.decode().strip() != 'device':
        adb(serial, 'connect', serial, check=False)
        state = adb(serial, 'get-state', check=False)
        if state.returncode or state.stdout.decode().strip() != 'device':
            raise DeployError('Cannot connect to target over ADB (device may be offline or unauthorized): ' + serial)
    actual_model = shell(serial, 'getprop ro.product.model').stdout.decode().strip()
    if actual_model != model:
        raise DeployError('Unexpected device model for ' + args.target + ': ' + actual_model)
    if args.target == 'tcl':
        sdk = shell(serial, 'getprop ro.build.version.sdk').stdout.decode().strip()
        brand = shell(serial, 'getprop ro.product.brand').stdout.decode().strip()
        manufacturer = shell(serial, 'getprop ro.product.manufacturer').stdout.decode().strip()
        kodi = shell(serial, 'pm path org.xbmc.kodi', check=False)
        kodi_data = shell(serial, 'test -d /sdcard/Android/data/org.xbmc.kodi/files/.kodi', check=False)
        if not actual_model or not sdk.isdigit() or not any('tcl' in value.casefold() for value in (actual_model, brand, manufacturer)) or kodi.returncode or not kodi.stdout.startswith(b'package:') or kodi_data.returncode:
            raise DeployError('Cannot verify the TCL device, Android, Kodi and its data directory on ' + serial + '; no files were transferred.')
    print('Target:', args.target, '(' + serial + ', ' + actual_model + ')')
    print('Package:', args.archive, 'version:', version)
    installed_version, enabled = addon_details(serial)
    print('Installed version:', installed_version or 'not found', '| enabled:', enabled if enabled is not None else 'unknown')
    if installed_version:
        missing, different, extra_private = compare_installed(serial, expected)
        if installed_version == version and not missing and not different:
            if enabled is False:
                raise DeployError('The matching add-on is disabled in Kodi; enable it before use.')
            print('All packaged files already match the installed version; no installation needed.')
            return
        private_changes = sorted({path for path in different if path.startswith('resources/private/')} | extra_private)
        if private_changes:
            raise DeployError('Installed private files differ from the package and would be overwritten: ' + ', '.join(private_changes) + '. Reconcile them before deploying.')
    check_playback_before_permission_change(serial)
    permission = storage_permission(serial)
    if permission not in ('allow', 'default'):
        raise DeployError('Kodi external-storage permission is explicitly denied; enable it before deploying.')
    permission_changed = permission == 'default'
    installed = False
    install_state = {'selected': False}
    try:
        if permission_changed:
            set_storage_permission(serial, 'allow')
        power = shell(serial, 'dumpsys power').stdout.decode('utf-8', errors='replace')
        wake = re.search(r'\bmWakefulness=(Awake|Asleep|Dreaming|Dozing)\b', power)
        if not wake:
            raise DeployError('Cannot determine whether the device is awake.')
        if wake.group(1) != 'Awake':
            shell(serial, 'input keyevent KEYCODE_WAKEUP')
        ensure_kodi(serial)
        before_log = log_bytes(serial)
        remote_name = 'bingie-deploy-' + uuid.uuid4().hex + '-' + args.archive.name
        remote = '/sdcard/Download/' + remote_name
        reservation = shell(serial, 'set -C; : > ' + shlex.quote(remote), check=False)
        if reservation.returncode:
            raise DeployError('Could not reserve an unused temporary ZIP path on the device.')
        try:
            print('Transferring the package to the device...')
            adb(serial, 'push', str(args.archive), remote)
            print('Installing the package through Kodi...')
            def mark_select():
                install_state['selected'] = True
            try:
                install_zip(serial, remote_name, mark_select)
            except DeployError:
                if not install_state['selected']:
                    raise
                print('Kodi did not acknowledge ZIP selection; checking whether installation started.')
            deadline = time.monotonic() + 300
            last = 'Kodi has not finished installing the package.'
            while time.monotonic() < deadline:
                try:
                    current, current_enabled = addon_details(serial)
                    if current == version and current_enabled is not False:
                        missing, different, _ = compare_installed(serial, expected)
                        if not missing and not different:
                            if deployment_log_ok(before_log, log_bytes(serial), version) and addon_jobs_complete(serial):
                                print('Installed version', version, 'and all', len(expected), 'package files match.')
                                print('Kodi log confirms the skin reloaded and its service started.')
                                if current_enabled is None:
                                    print('Kodi JSON-RPC was unavailable; enabled state could not be verified.')
                                else:
                                    print('Kodi reports the add-on enabled.')
                                installed = True
                                break
                            last = 'Kodi has not confirmed both the skin reload and completion of its installation jobs.'
                        else:
                            last = 'Some installed files do not yet match the package.'
                    elif current_enabled is False:
                        last = 'Kodi reports the add-on is disabled.'
                except (DeployError, OSError):
                    last = 'Kodi or the installed files could not be read while verifying; retrying.'
                time.sleep(2)
            if not installed:
                raise DeployError(last + ' Installation outcome is unknown.')
        finally:
            if install_state['selected'] and not installed:
                print('Installation outcome is unknown; retained ZIP:', remote, file=sys.stderr)
                print('Wait until Kodi has finished installing, then remove it with:', file=sys.stderr)
                print('  adb -s ' + shlex.quote(serial) + ' shell rm -f -- ' + shlex.quote(remote), file=sys.stderr)
                if permission_changed:
                    print('Kodi external-storage permission remains allow (originally default).', file=sys.stderr)
                    print('After Kodi finishes, restore it with:', file=sys.stderr)
                    print('  adb -s ' + shlex.quote(serial) + ' shell appops set org.xbmc.kodi MANAGE_EXTERNAL_STORAGE default', file=sys.stderr)
                    print('Restoring permission may stop Kodi; restart it if needed.', file=sys.stderr)
                else:
                    print('Kodi external-storage permission remains allow (unchanged).', file=sys.stderr)
            else:
                result = shell(serial, 'rm -f -- ' + shlex.quote(remote), check=False)
                if result.returncode:
                    raise DeployError('Could not remove the transferred ZIP: ' + remote)
                print('Removed the temporary ZIP from the device.')
    finally:
        if permission_changed and not (install_state['selected'] and not installed):
            set_storage_permission(serial, 'default')
            print('Restored Kodi external-storage permission.')
            process = shell(serial, 'pidof org.xbmc.kodi', check=False)
            if process.returncode == 1 and not process.stdout.strip():
                ensure_kodi(serial)
            elif process.returncode != 0:
                raise DeployError('Cannot check whether Kodi needs restarting after restoring permission.')
            if installed:
                current, enabled = addon_details(serial)
                if current != version or enabled is False:
                    raise DeployError('Cannot confirm the installed version after restoring permission.')


def handle_termination(signum, frame):
    raise KeyboardInterrupt


def main():
    signal.signal(signal.SIGTERM, handle_termination)
    parser = argparse.ArgumentParser(description='Transfer and verify a BINGIE Lite ZIP on a known Android Kodi device.')
    parser.add_argument('target', choices=DEVICES, help='target device: googletv, shield or tcl')
    parser.add_argument('archive', type=Path, help='path to the already-built add-on ZIP')
    args = parser.parse_args()
    try:
        deploy(args)
    except (DeployError, FileNotFoundError, EOFError, KeyboardInterrupt) as exc:
        print('\nDeployment stopped:', exc or 'cancelled', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
