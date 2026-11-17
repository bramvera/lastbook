#!/usr/bin/env python3
"""Install a locally built Indonesia zip into a CLOSED library, preserving the old map.

The SHA-256 must come from your own build or a trusted publisher, not an untrusted zip.
This tool never downloads and does not relax the desktop downloader's host allowlist.
"""
import argparse
from contextlib import closing
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.setup import install, download
from engine.paths import ro_uri, try_lock, unlock
from tools.build_indonesia import sha256


def install_map(archive, library, expected_sha256):
    archive, library = Path(archive).resolve(), Path(library).resolve()
    if not install.is_library(library):
        raise ValueError('Choose an existing Lastbook library with library-data/catalog.json.')
    if sha256(archive) != expected_sha256:
        raise ValueError('Map archive SHA-256 does not match.')
    # This lock serializes this tool, not the app: callers must close Lastbook before installation.
    with open(library / '.indonesia-map-install.lock', 'a+b') as lock:
        try_lock(lock)
        try:
            with tempfile.TemporaryDirectory(prefix='.indonesia-map-', dir=library.parent) as work:
                _, total = install.check_zip(archive, None, install.map_member)
                if shutil.disk_usage(library.parent).free < total + 512 * 1024**2:
                    raise ValueError('Not enough free space to stage this map.')
                staged = install.unpack(archive, work, total, download.Control(), lambda done, total: None,
                                        allowed=install.map_member)
                info = install.check_map(staged)
                if info.get('name') != 'IDN':
                    raise ValueError('This is not an Indonesia map (IDN).')
                required = {'derived/geo/map.sqlite', 'derived/geo/national.sqlite', 'derived/geo/places.sqlite'}
                if not required <= set(info['files']):
                    raise ValueError('The Indonesia map is missing a required database.')
                if set(install.map_files(staged)) != set(info['files']) | {'map-set.json'}:
                    raise ValueError('The archive contains unlisted map files.')
                for rel in required:
                    with closing(sqlite3.connect(ro_uri(staged / rel), uri=True)) as db:
                        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                            raise ValueError('A map database failed its integrity check.')
                # Persist the backup outside the temporary staging folder; never delete it on success.
                backup = Path(tempfile.mkdtemp(prefix=library.name + '.map-backup-', dir=library.parent))
                if not install.map_set(library):
                    old_files = {rel: (library / rel).stat().st_size for rel in install.map_files(library) if rel != 'map-set.json'}
                    (backup / 'map-set.json').write_text(json.dumps({'name': install.map_name(library), 'files': old_files}))
                install.swap_map(library, staged, backup)
                return backup
        finally:
            unlock(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--library', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--library-closed', required=True, action='store_true', help='Confirm all app/server copies using this library are closed')
    a = parser.parse_args()
    print('Previous map retained at:', install_map(a.archive, a.library, a.sha256))
    print('Open the library with the Indonesia fork to use the regional map labels.')


if __name__ == '__main__':
    main()
