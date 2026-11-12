"""Prepare and inspect a private headless client experiment without claiming a server."""
from __future__ import annotations

import argparse
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time

SCHEMA = 'moon-dedicated-host-experiment/v2'
OWNERSHIP = '.moon-dedicated-host.json'
REQUIRED = ('MoonSweepers.exe', 'GameAssembly.dll', 'UnityPlayer.dll',
            'MoonSweepers_Data/il2cpp_data/Metadata/global-metadata.dat')
# These identify loader overlays or profile trees, not clean game distribution
# content. Match without case because the copied installation runs under Wine.
FORBIDDEN_ROOTS = {'version.dll', 'winhttp.dll', 'doorstop_config.ini',
                   'bepinex', 'melonloader', 'moonlab', '.wine',
                   'system.reg', 'user.reg', 'userdef.reg'}
PROFILE_DIRECTORIES = {'drive_c', 'dosdevices', 'prefix', 'profiles', 'saves'}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    path = nonsymlink(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                         getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f'Non-regular input refused: {path}')
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def nonsymlink(path: Path) -> Path:
    """Refuse symlinked parents as well as a symlink at the final component."""
    absolute = Path(os.path.abspath(path))
    for item in (absolute, *absolute.parents):
        reparse = item.exists() and bool(getattr(item.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
        if item.is_symlink() or reparse:
            raise ValueError(f'Symlink path refused: {item}')
    return absolute


def game_path(value: str | Path) -> Path:
    path = nonsymlink(Path(value))
    if not path.is_dir():
        raise ValueError('Game folder must exist')
    for relative in REQUIRED:
        target = nonsymlink(path / relative)
        if not target.is_file():
            raise ValueError(f'Current IL2CPP game file missing: {relative}')
    return path


def write_json(path: Path, data: dict) -> None:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')


def api_evidence() -> dict:
    # The build packages this derived evidence beside the module. No proprietary
    # binaries, raw IL, account identities or save contents are included.
    return json.loads(files(__package__).joinpath('api-evidence.json').read_text())


def inspect(game: Path) -> dict:
    game = game_path(game)
    evidence = api_evidence()
    identity = {name: digest(game / name) for name in REQUIRED}
    pins = evidence['game_hashes']
    if not isinstance(pins, dict) or any(
            name not in REQUIRED or not isinstance(value, str) or len(value) != 64 or
            any(c not in '0123456789abcdef' for c in value)
            for name, value in pins.items()):
        raise ValueError('Malformed bundled game identity evidence')
    missing = [name for name in REQUIRED if name not in pins]
    mismatched = [name for name, expected in pins.items() if identity[name] != expected]
    blockers = []
    if missing:
        blockers.append('Bundled evidence lacks required file pins: ' + ', '.join(missing))
    if mismatched:
        blockers.append('Source differs from bundled evidence: ' + ', '.join(mismatched))
    return {'schema': SCHEMA, 'mode': 'inspection', 'game': str(game),
            'source_sha256': identity,
            'matches_evidence_build': not blockers,
            'admission': {'identity_admitted': not blockers,
                          'clean_input_check_required': True,
                          'missing_pins': missing, 'mismatched_files': mismatched,
                          'blockers': blockers},
            'api_evidence': evidence,
            'dedicated_server': False,
            'qualification': 'Architecture pending; no clientless host bootstrap established',
            'requirements_remaining': [
                'Steam game-server or supported account-host initialization and ownership',
                'World/slot bootstrap without an active local player or main-menu interaction',
                'Host authority, remote join and disconnect survival without a local player',
                'Save, shutdown and recovery lifecycle under unattended operation',
                'Loaded-world and remote-client qualification with a fresh private profile']}


def source_files(source: Path):
    def traversal_error(error):
        raise error

    for current, dirs, files in os.walk(source, followlinks=False, onerror=traversal_error):
        directory = nonsymlink(Path(current))
        for name in sorted(dirs):
            nonsymlink(directory / name)
            if name.casefold() in PROFILE_DIRECTORIES:
                raise ValueError(f'Profile/prefix directory refused: {directory / name}')
        if directory == source:
            for name in dirs + files:
                if name.casefold() in FORBIDDEN_ROOTS:
                    raise ValueError(f'Choose a clean installed input; loader refused: {name}')
        dirs.sort()
        for name in sorted(files):
            item = nonsymlink(directory / name)
            if not stat.S_ISREG(item.lstat().st_mode):
                raise ValueError(f'Non-regular input refused: {item}')
            yield item, item.relative_to(source)


def inventory(source: Path) -> dict:
    return {relative.as_posix(): {'size': item.stat().st_size, 'sha256': digest(item)}
            for item, relative in source_files(source)}


def prepare(source: Path, destination: Path) -> dict:
    source = game_path(source)
    destination = nonsymlink(destination)
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('Private destination must be outside the source tree')
    if destination.exists():
        raise ValueError('Destination already exists; existing installations are preserved')
    # Enumerate all input paths before writing. Personal saves and Wine prefixes
    # are never inputs: only the supplied game directory is copied.
    report = inspect(source)
    if not report['matches_evidence_build']:
        raise ValueError('Preparation refused: ' + '; '.join(report['admission']['blockers']))
    entries = list(source_files(source))
    source_inventory = inventory(source)
    if any(source_inventory[name]['sha256'] != report['source_sha256'][name]
           for name in REQUIRED):
        raise ValueError('Source changed during identity inspection')
    if (source / 'steam_appid.txt').exists() and (source / 'steam_appid.txt').read_text().strip() != '5335510':
        raise ValueError('Source Steam application identity differs')
    destination.mkdir(parents=True, exist_ok=False)
    game = destination / 'game'
    copied = 0
    try:
        game.mkdir()
        for item, relative in entries:
            nonsymlink(item)
            target = game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            # O_NOFOLLOW prevents a swapped final source component from turning
            # a private copy into a symlink. Outputs are always new regular files.
            descriptor = os.open(item, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
                                 getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(descriptor, 'rb') as original, target.open('xb') as output:
                if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
                    raise ValueError(f'Non-regular input refused: {item}')
                shutil.copyfileobj(original, output)
            copied += 1
        if inventory(game) != source_inventory or inventory(source) != source_inventory:
            raise ValueError('Source changed during private-copy preparation')
        if not (game / 'steam_appid.txt').exists():
            (game / 'steam_appid.txt').write_text('5335510\n')
        manifest = dict(report, mode='prepared-headless-client-experiment',
                        owner='dedicated-host', prepared_at_ns=time.time_ns(),
                        source_game=str(source), private_game=str(game), files_copied=copied,
                        fresh_profile_required=True, saves_copied=False,
                        source_files_changed=False, server_bootstrap_available=False,
                        game_inventory=inventory(game))
        write_json(destination / OWNERSHIP, manifest)
        return manifest
    except BaseException:
        # Never remove a partial copy automatically; retain it for inspection.
        (destination / 'PREPARATION_FAILED.txt').write_text(
            'Partial private copy. No ownership manifest was published. Remove manually.\n')
        raise


def load_installation(path: Path) -> tuple[Path, dict]:
    path = nonsymlink(path)
    manifest_path = nonsymlink(path / OWNERSHIP)
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or manifest.get('owner') != 'dedicated-host':
        raise ValueError('Not an owned experimental installation')
    if (manifest.get('mode') != 'prepared-headless-client-experiment' or
            manifest.get('dedicated_server') is not False or
            manifest.get('server_bootstrap_available') is not False or
            manifest.get('fresh_profile_required') is not True or
            manifest.get('saves_copied') is not False):
        raise ValueError('Experimental installation admission flags changed')
    game = game_path(path / 'game')
    if str(game) != manifest.get('private_game'):
        raise ValueError('Installation moved; prepare a new private copy')
    if {name: digest(game / name) for name in REQUIRED} != manifest.get('source_sha256'):
        raise ValueError('Private game identity changed')
    if inventory(game) != manifest.get('game_inventory'):
        raise ValueError('Private game inventory changed; prepare a new copy')
    if (game / 'steam_appid.txt').read_text().strip() != '5335510':
        raise ValueError('Private Steam application identity differs')
    if not inspect(game)['matches_evidence_build']:
        raise ValueError('Experimental installation no longer matches the inspected build')
    return game, manifest


def run_client(installation: Path, wine: Path) -> dict:
    if sys.platform == 'win32':
        raise ValueError('Native Windows profile isolation is unresolved; this experiment requires Wine')
    game, manifest = load_installation(installation)
    installation = game.parent
    wine = nonsymlink(wine)
    if not wine.is_file() or not os.access(wine, os.X_OK):
        raise ValueError('Choose an existing Wine executable; no dependency is installed')
    run = installation / ('run-' + str(time.time_ns()))
    run.mkdir()
    prefix = run / 'prefix'
    # A unique, initially nonexistent prefix prevents reuse of Steam/Bottles
    # personal profiles. Each invocation is an independent client experiment.
    env = dict(os.environ)
    env['WINEPREFIX'] = str(prefix)
    env['WINEARCH'] = 'win64'
    for name in list(env):
        if name.startswith('MOONLAB_'):
            env.pop(name)
    env.update(SteamAppId='5335510', SteamGameId='5335510')
    log = run / 'process.log'
    unity_log = run / 'unity.log'
    command = [str(wine), str(game / 'MoonSweepers.exe'), '-batchmode', '-nographics',
               '-logFile', 'Z:' + str(unity_log).replace('/', '\\')]
    receipt = {'schema': SCHEMA, 'mode': 'headless-client-experiment',
               'dedicated_server': False, 'server_bootstrap_available': False,
               'command': command, 'prefix': str(prefix), 'log': str(log),
               'unity_log': str(unity_log), 'source_sha256': manifest['source_sha256'],
               'note': 'This launches a Unity client only. No lobby/world/host startup is asserted.'}
    with log.open('xb') as stream:
        process = subprocess.Popen(command, cwd=game, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    receipt['pid'] = process.pid
    receipt['started_at_ns'] = time.time_ns()
    write_json(run / 'receipt.json', receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='Companion configuration path (reserved; never imports saves)')
    commands = parser.add_subparsers(dest='command', required=True)
    scan = commands.add_parser('inspect', help='Read the current game identity and hosting architecture gaps')
    scan.add_argument('--game', required=True, type=Path)
    prep = commands.add_parser('prepare', help='Copy only game files into a new private experiment')
    prep.add_argument('--game', required=True, type=Path)
    prep.add_argument('--destination', required=True, type=Path)
    run = commands.add_parser('run-client', help='Launch a headless CLIENT in a unique fresh Wine prefix')
    run.add_argument('--installation', required=True, type=Path)
    run.add_argument('--wine', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'inspect':
            result = inspect(game_path(args.game))
        elif args.command == 'prepare':
            result = prepare(game_path(args.game), args.destination)
        else:
            result = run_client(args.installation, args.wine)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print(f'dedicated-host: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
