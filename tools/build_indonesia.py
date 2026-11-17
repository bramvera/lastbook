#!/usr/bin/env python3
"""Build an offline Indonesia map from a local OSM extract. Never downloads anything.

Requires build-only osmium (see packaging/requirements_maps.txt). See docs/REGIONS-ID.md.
The Geofabrik Indonesia extract includes Timor-Leste; this is not a political boundary clip.
"""
import argparse
from contextlib import closing
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_map import ROAD_Z, PLACE_Z, POI_KEEP, LANDUSE_KEEP, pack, bbox, area_z, wkb_parts

SCHEMA = '''CREATE TABLE feat(id INTEGER PRIMARY KEY, layer TEXT, cls TEXT, name TEXT, minz INTEGER, geom BLOB);
CREATE VIRTUAL TABLE feat_idx USING rtree(id,minx,maxx,miny,maxy,minz,maxz);
CREATE TABLE poi(id INTEGER PRIMARY KEY, cls TEXT, name TEXT, lon REAL, lat REAL);
CREATE INDEX poi_cls ON poi(cls,lon,lat);
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);'''
SOURCE = '© OpenStreetMap contributors (ODbL 1.0), via Geofabrik Indonesia extract'
COVERAGE = 'Indonesia; the source extract also includes Timor-Leste'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def build(source, output, data_as_of, bounds=None):
    """Write a new map directory only after a successful build. Existing output is never replaced."""
    import osmium
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError('The OSM source file does not exist.')
    if output.exists():
        raise ValueError('Output already exists; choose a new directory.')
    if bounds and (len(bounds) != 4 or not all(math.isfinite(x) for x in bounds)
                   or not -180 <= bounds[0] < bounds[2] <= 180 or not -85 <= bounds[1] < bounds[3] <= 85):
        raise ValueError('Bounds must be west,south,east,north in longitude/latitude degrees.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.indonesia-', dir=output.parent) as work:
        stage = Path(work) / 'map'
        geo = stage / 'derived/geo'
        geo.mkdir(parents=True)
        db = sqlite3.connect(geo / 'national.sqlite')
        db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;' + SCHEMA)
        places = sqlite3.connect(geo / 'places.sqlite')
        places.executescript('''CREATE TABLE place(id INTEGER PRIMARY KEY,name TEXT,kind TEXT,detail TEXT,lat REAL,lon REAL,rank INTEGER,source TEXT);
CREATE VIRTUAL TABLE place_fts USING fts5(name,detail,content='place',content_rowid='id',tokenize='unicode61');''')
        counts = {'features': 0, 'pois': 0, 'places': 0, 'invalid_geometries': 0}
        factory = osmium.geom.WKBFactory()

        def inside(parts):
            if not parts or not any(parts):
                return False
            w, e, s, n = bbox(parts)
            return not bounds or (e >= bounds[0] and w <= bounds[2] and n >= bounds[1] and s <= bounds[3])

        def add(layer, cls, name, z, parts):
            if not inside(parts):
                return
            counts['features'] += 1
            fid = counts['features']
            db.execute('INSERT INTO feat VALUES(?,?,?,?,?,?)', (fid, layer, cls, name, z, pack(parts)))
            db.execute('INSERT INTO feat_idx VALUES(?,?,?,?,?,?,?)', (fid, *bbox(parts), z, 30))
            if fid % 100000 == 0:
                db.commit()
                places.commit()
                print(json.dumps(counts), flush=True)

        def place(name, kind, lon, lat, rank):
            if name:
                counts['places'] += 1
                places.execute('INSERT INTO place(name,kind,detail,lat,lon,rank,source) VALUES(?,?,?,?,?,?,?)',
                               (name, kind.replace('_', ' '), '', lat, lon, rank, 'OpenStreetMap'))

        def point(tags, lon, lat):
            if not inside([[lon, lat]]):
                return
            name = tags.get('name') or tags.get('name:id') or tags.get('name:en')
            cls = tags.get('place')
            if cls in PLACE_Z:
                add('place', cls, name, PLACE_Z[cls], [[lon, lat]])
                place(name, cls, lon, lat, 1)
            aliases = {'toilets': 'toilet', 'camp_site': 'camp_site', 'wastewater_plant': 'wastewater_plant'}
            for key in ('amenity', 'shop', 'tourism', 'natural', 'man_made', 'emergency'):
                cls = aliases.get(tags.get(key), tags.get(key))
                if cls in POI_KEEP:
                    counts['pois'] += 1
                    db.execute('INSERT INTO poi VALUES(?,?,?,?,?)', (counts['pois'], cls, name, lon, lat))
                    place(name, cls, lon, lat, 4)
                    break

        class Handler(osmium.SimpleHandler):
            def node(self, node):
                if not len(node.tags):
                    return  # the location handler still indexes these nodes for way geometry
                if node.location.valid():
                    point(node.tags, node.location.lon, node.location.lat)

            def way(self, way):
                tags = way.tags
                road, water, rail = tags.get('highway'), tags.get('waterway'), tags.get('railway')
                if not (road or water or rail) or tags.get('area') == 'yes':
                    return
                try:
                    parts = [[v for node in way.nodes for v in (node.lon, node.lat)]]
                except osmium.InvalidLocationError:
                    counts['invalid_geometries'] += 1
                    return
                if len(parts[0]) < 4:
                    return
                name = tags.get('name') or tags.get('ref')
                if road:
                    add('road', road, name, ROAD_Z.get(road, 15), parts)
                elif water:
                    add('waterway', water, name, 10 if water == 'river' else 13, parts)
                elif rail:
                    add('rail', rail, name, 10, parts)

            def area(self, area):
                tags = area.tags
                natural, land = tags.get('natural'), tags.get('landuse')
                relevant = (tags.get('building') or land in LANDUSE_KEEP or natural in ('water', 'wetland', 'beach', 'wood', 'scrub')
                            or tags.get('waterway') == 'riverbank' or tags.get('leisure') == 'park'
                            or any(tags.get(k) in POI_KEEP or tags.get(k) == 'toilets' for k in ('amenity', 'shop', 'tourism', 'natural', 'man_made', 'emergency')))
                if not relevant:
                    return
                try:
                    parts = wkb_parts(bytes.fromhex(factory.create_multipolygon(area)), 0)[0]
                except (RuntimeError, ValueError):
                    counts['invalid_geometries'] += 1
                    return
                if not inside(parts):
                    return
                w, e, s, n = bbox(parts)
                point(tags, (w + e) / 2, (s + n) / 2)
                name = tags.get('name')
                if tags.get('building') and tags.get('building') != 'no':
                    add('bldg', tags.get('building'), name, 15, parts)
                elif natural == 'water' or tags.get('waterway') == 'riverbank':
                    add('water', tags.get('water') or 'water', name, area_z(w, e, s, n, 7), parts)
                elif natural in ('wetland', 'beach'):
                    add('wet' if natural == 'wetland' else 'beach', natural, name, area_z(w, e, s, n, 10), parts)
                elif land in LANDUSE_KEEP or natural in ('wood', 'scrub') or tags.get('leisure') == 'park':
                    cls = LANDUSE_KEEP.get(land, 'park' if tags.get('leisure') == 'park' else 'green')
                    add('land', cls, name, area_z(w, e, s, n, 9), parts)

        try:
            Handler().apply_file(str(source), locations=True, idx='sparse_file_array,' + str(Path(work) / 'nodes.idx'))
            if not counts['features']:
                raise ValueError('No map features found; refusing to publish an empty map.')
            extent = db.execute('SELECT min(minx),min(miny),max(maxx),max(maxy) FROM feat_idx').fetchone()
            meta = {'source': SOURCE, 'source_url': 'https://download.geofabrik.de/asia/indonesia.html',
                    'source_sha256': sha256(source), 'data_as_of': data_as_of, 'coverage': COVERAGE,
                    'country': 'IDN', 'bounds': ','.join(str(x) for x in (bounds or extent)),
                    'built': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
            db.executemany('INSERT INTO meta VALUES(?,?)', meta.items())
            db.commit()
            places.execute("INSERT INTO place_fts(place_fts) VALUES('rebuild')")
            places.commit()
        finally:
            db.close()
            places.close()
        # The renderer opens map.sqlite first; national.sqlite carries the actual regional features.
        with closing(sqlite3.connect(geo / 'map.sqlite')) as empty:
            empty.executescript(SCHEMA)
        (geo / 'national-status.json').write_text(json.dumps({'state': 'complete', 'stage': 'ready', 'features': counts['features']}))
        (geo / 'socal-cells.json').write_text('[]')
        files = {p.relative_to(stage).as_posix(): p.stat().st_size for p in sorted(stage.rglob('*')) if p.is_file()}
        info = {'name': 'IDN', 'title': 'Indonesia' if not bounds else 'Indonesia (selected area)',
                'files': files, 'bytes': sum(files.values()), 'coverage': COVERAGE,
                'bounds': list(bounds or extent), 'source': SOURCE, 'data_as_of': data_as_of,
                'source_sha256': meta['source_sha256'], 'sample': bool(bounds), 'counts': counts}
        (stage / 'map-set.json').write_text(json.dumps(info, indent=2) + '\n')
        stage.rename(output)
    return info


def archive(output):
    output = Path(output)
    dest = output.with_suffix('.zip')
    if dest.exists():
        raise ValueError('Map zip already exists; choose a new output name.')
    with zipfile.ZipFile(dest, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for p in sorted(output.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(output).as_posix())
    digest = sha256(dest)
    dest.with_suffix('.zip.sha256').write_text(f'{digest}  {dest.name}\n')
    return dest


def preview(output, destination):
    """A separate map-only library for exercising the real app without downloading library content."""
    output, destination = Path(output), Path(destination)
    if destination.exists():
        raise ValueError('Preview destination already exists.')
    shutil.copytree(output, destination)
    (destination / 'library-data').mkdir(exist_ok=True)
    (destination / 'library-data/catalog.json').write_text('[]\n')
    (destination / 'pack.json').write_text(json.dumps({'name': 'indonesia-map-preview', 'map': 'IDN',
        'known_gaps': ['Map only: no manuals, videos, AI, terrain, crops or street-address index.']}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--data-as-of', required=True, help='Timestamp/date from the source snapshot')
    parser.add_argument('--bbox', help='Optional sample area west,south,east,north; intersection filter, not polygon clipping')
    parser.add_argument('--preview-library', type=Path, help='Also create a separate map-only library for testing in the app')
    a = parser.parse_args()
    info = build(a.source, a.output, a.data_as_of, tuple(map(float, a.bbox.split(','))) if a.bbox else None)
    print(json.dumps(info, indent=2))
    print('Map pack:', archive(a.output))
    if a.preview_library:
        preview(a.output, a.preview_library)
        print('Map-only preview:', a.preview_library)


if __name__ == '__main__':
    main()
