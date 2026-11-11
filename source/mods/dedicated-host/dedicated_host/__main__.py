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

SCHEMA = 'moon-dedicated-host-experiment/v1'
OWNERSHIP = '.moon-dedicated-host.json'
REQUIRED = ('MoonSweepers.exe', 'GameAssembly.dll', 'UnityPlayer.dll',
            'MoonSweepers_Data/il2cpp_data/Metadata/global-metadata.dat')


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
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
    evidence = api_evidence()
    identity = {name: digest(game / name) for name in REQUIRED}
    return {'schema': SCHEMA, 'mode': 'inspection', 'game': str(game),
            'source_sha256': identity,
            'matches_evidence_build': all(identity.get(name) == expected for name, expected in evidence['game_hashes'].items()),
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
    for current, dirs, files in os.walk(source, followlinks=False):
        directory = nonsymlink(Path(current))
        for name in dirs:
            nonsymlink(directory / name)
        for name in files:
            item = nonsymlink(directory / name)
            if not item.is_file():
                raise ValueError(f'Non-regular input refused: {item}')
            yield item, item.relative_to(source)


def prepare(source: Path, destination: Path) -> dict:
    destination = nonsymlink(destination)
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('Private destination must be outside the source tree')
    if destination.exists():
        raise ValueError('Destination already exists; existing installations are preserved')
    # Enumerate all input paths before writing. Personal saves and Wine prefixes
    # are never inputs: only the supplied game directory is copied.
    entries = list(source_files(source))
    report = inspect(source)
    if not report['matches_evidence_build']:
        raise ValueError('Unknown game build; inspect and qualify a new host experiment before preparation')
    if (source / 'version.dll').exists() or (source / 'MoonLab').exists():
        raise ValueError('Choose a clean installed input; existing loaders are not copied into the experiment')
    if (source / 'steam_appid.txt').exists() and (source / 'steam_appid.txt').read_text().strip() != '5335510':
        raise ValueError('Source Steam application identity differs')
    destination.mkdir(parents=True, exist_ok=False)
    game = destination / 'game'
    game.mkdir()
    copied = 0
    try:
        for item, relative in entries:
            target = game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target, follow_symlinks=False)
            copied += 1
        readback = {name: digest(game / name) for name in REQUIRED}
        if readback != report['source_sha256']:
            raise ValueError('Source changed during private-copy preparation')
        if not (game / 'steam_appid.txt').exists():
            (game / 'steam_appid.txt').write_text('5335510\n')
        manifest = dict(report, mode='prepared-headless-client-experiment',
                        owner='dedicated-host', prepared_at_ns=time.time_ns(),
                        source_game=str(source), private_game=str(game), files_copied=copied,
                        fresh_profile_required=True, saves_copied=False,
                        source_files_changed=False, server_bootstrap_available=False)
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
    if manifest.get('schema') != SCHEMA or manifest.get('owner') != 'dedicated-host':
        raise ValueError('Not an owned experimental installation')
    game = game_path(path / 'game')
    if str(game) != manifest.get('private_game'):
        raise ValueError('Installation moved; prepare a new private copy')
    if {name: digest(game / name) for name in REQUIRED} != manifest.get('source_sha256'):
        raise ValueError('Private game identity changed')
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
