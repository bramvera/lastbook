"""Indonesia maps: real OSM parsing, map-pack installation, renderer and US selection regressions."""
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.build_indonesia import build, archive, preview, sha256
from tools.install_indonesia_map import install_map
from engine.setup import install

ROOT = Path(__file__).resolve().parents[2]
try:
    import osmium
except ImportError:
    osmium = None

OSM = '''<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="lastbook-test">
 <node id="1" lat="-6.20" lon="106.80"><tag k="place" v="city"/><tag k="name" v="Kota Contoh"/></node>
 <node id="2" lat="-6.21" lon="106.81"><tag k="amenity" v="hospital"/><tag k="name" v="Rumah Sakit Contoh"/></node>
 <node id="3" lat="-6.22" lon="106.82"/>
 <node id="4" lat="-6.23" lon="106.82"/>
 <node id="5" lat="-6.23" lon="106.83"/>
 <node id="6" lat="-6.22" lon="106.83"/>
 <node id="7" lat="-7.00" lon="110.00"><tag k="amenity" v="pharmacy"/><tag k="name" v="Outside sample"/></node>
 <way id="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/><tag k="name" v="Jalan Contoh"/></way>
 <way id="2"><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="6"/><nd ref="3"/><tag k="building" v="yes"/></way>
 <way id="3"><nd ref="3"/><nd ref="4"/><tag k="waterway" v="river"/><tag k="name" v="Sungai Contoh"/></way>
 <way id="4"><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="6"/><nd ref="3"/></way>
 <relation id="1"><member type="way" ref="4" role="outer"/><tag k="type" v="multipolygon"/><tag k="natural" v="water"/><tag k="name" v="Danau Contoh"/></relation>
</osm>'''


@unittest.skipUnless(osmium, 'Install packaging/requirements_maps.txt for the OSM build tests')
class IndonesiaBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'sample.osm'
        self.source.write_text(OSM)
        self.output = self.root / 'map'
        self.info = build(self.source, self.output, '2026-11-01', (106.7, -6.3, 106.9, -6.1))

    def test_real_geometry_and_place_search(self):
        self.assertTrue(self.info['sample'])
        self.assertEqual(self.info['name'], 'IDN')
        self.assertEqual(self.info['counts']['invalid_geometries'], 0)
        with sqlite3.connect(self.output / 'derived/geo/national.sqlite') as db:
            layers = {r[0] for r in db.execute('SELECT DISTINCT layer FROM feat')}
            self.assertTrue({'road', 'place', 'bldg', 'waterway', 'water'} <= layers)
            self.assertEqual(db.execute('SELECT name FROM poi').fetchall(), [('Rumah Sakit Contoh',)])
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone(), ('ok',))
        with sqlite3.connect(self.output / 'derived/geo/places.sqlite') as db:
            self.assertEqual(db.execute("SELECT name FROM place_fts WHERE place_fts MATCH 'Kota'").fetchall(), [('Kota Contoh',)])
        install.check_map(self.output)

    def test_install_keeps_catalog_and_old_map(self):
        library = self.root / 'LastbookData'
        (library / 'library-data').mkdir(parents=True)
        (library / 'library-data/catalog.json').write_text('{"records": []}')
        (library / 'derived/geo').mkdir(parents=True)
        old = library / 'derived/geo/map.sqlite'
        old.write_bytes(b'old US map')
        zip_path = archive(self.output)
        backup = install_map(zip_path, library, sha256(zip_path))
        self.assertEqual((backup / 'derived/geo/map.sqlite').read_bytes(), b'old US map')
        self.assertEqual((library / 'library-data/catalog.json').read_text(), '{"records": []}')
        self.assertEqual(install.map_name(library), 'IDN')
        self.assertEqual(install.check_map(library)['files'], self.info['files'])
        install.check_map(backup)
        undo = self.root / 'undo'
        undo.mkdir()
        install.swap_map(library, backup, undo)
        self.assertEqual(old.read_bytes(), b'old US map')
        self.assertEqual(install.map_name(library), 'base')

    def test_bad_checksum_never_touches_library(self):
        library = self.root / 'LastbookData'
        (library / 'library-data').mkdir(parents=True)
        (library / 'library-data/catalog.json').write_text('{}')
        zip_path = archive(self.output)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            install_map(zip_path, library, '0' * 64)
        self.assertFalse((library / 'derived').exists())

    def test_refuses_existing_output_and_bad_bounds(self):
        before = (self.output / 'map-set.json').read_bytes()
        with self.assertRaises(ValueError):
            build(self.source, self.output, '2026-11-01')
        self.assertEqual(before, (self.output / 'map-set.json').read_bytes())
        with self.assertRaises(ValueError):
            build(self.source, self.root / 'bad', '2026-11-01', (110, 2, 100, -5))

    def test_real_renderer_and_metadata(self):
        library = self.root / 'preview'
        preview(self.output, library)
        # Separate interpreter so global paths/caches are exactly as on app startup.
        script = '''
import json, math, server
server.catalog()
info = server.map_info()
assert info['bounds'] == [106.7, -6.3, 106.9, -6.1], info
assert not any('USGS' in x['label'] or 'whole US' in x['label'] for x in info['datasets'])
assert server.places('Kota Contoh')[0]['source'] == 'OpenStreetMap'
from unittest.mock import patch
with patch.object(server.address_search, 'find_addresses') as lookup:
    assert server.places('123 Example Road') == []
    lookup.assert_not_called()
z=15; lon=106.805; lat=-6.205
x=int((lon+180)/360*2**z)
y=int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*2**z)
tile=json.loads(server._map_tile(z,x,y,0))
assert 'road' in tile['layers'], tile
print('Renderer, regional metadata and place search passed')
'''
        import os, sys
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env={**os.environ, 'LASTBOOK_DATA': str(library)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(shutil.which('node'), 'Node is required for setup selection tests')
class IndonesiaSetupTests(unittest.TestCase):
    def test_us_all_does_not_include_indonesia(self):
        # Execute the actual setup closure with a small DOM stub, avoiding any network or UI startup.
        source = (ROOT / 'web/setup.js').read_text()
        end = source.index('  load().then(')
        source = source[:end] + '''
S={manifest:{maps:[{id:'CA',title:'California',available:true},{id:'IDN',title:'Indonesia',available:true}]},current:{map:'us',pack:'all'}};
choice={pack:null,map:'IDN'};
if (!mapAdds() || mapAdds().id !== 'IDN') throw Error('US All incorrectly includes Indonesia');
choice.map='ID'; if (mapAdds()) throw Error('US All lost Idaho');
choice.map='CA'; if (mapAdds()) throw Error('US All lost California');
S.current={map:'IDN'}; choice.map='IDN'; if (mapAdds()) throw Error('Installed Indonesia downloaded again');
choice.map='CA'; if (!mapAdds()) throw Error('Cannot switch from Indonesia to California');
S.manifest.maps=[]; if (mapName('IDN') !== 'Indonesia' || !maps().some(m=>m.id==='IDN')) throw Error('Local Indonesia missing');
S.manifest.packs=[{id:'small',map:null}]; choice={pack:'small',map:'IDN'};
if (!mapMissing()) throw Error('Unpublished local map offered as a new download');
})();'''
        stub = "global.navigator={platform:'Linux'}; global.location={hash:''}; global.document={documentElement:{classList:{contains:()=>false}},querySelector:()=>({}),querySelectorAll:()=>[]}; global.addEventListener=()=>{};\n"
        result = subprocess.run(['node', '-e', stub + source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_indonesia_controls_and_attribution(self):
        source = (ROOT / 'web/map.js').read_text()
        source = source.replace('return { page, stop };', """
st={style:{relief:true,contours:true},on:new Set(['hospital'])};
describe({region:'IDN',data_as_of:'2026-11-01',terrain:false,aerial:false,contours:false,layers:['hospital'],bounds:[95,-11,141,6]});
if (!nodes['map-3d'].removed || !nodes['map-photo'].removed) throw Error('Unavailable terrain or imagery offered');
if (st.style.relief || st.style.contours) throw Error('Missing terrain still rendered');
if (!nodes['.mapattr'].textContent.includes('ODbL') || nodes['.mapattr'].textContent.includes('USGS')) throw Error('Wrong sources');
if (!nodes['.maphead p'].textContent.includes('2026-11-01')) throw Error('Wrong source date');
if (nodes['pq'].placeholder !== 'Find a town or named place') throw Error('Promises unsupported address search');
return { page, stop };
""")
        stub = """global.location={protocol:'http:',hostname:'localhost'};
const nodes={};
const element=k=>nodes[k] || (nodes[k]={removed:false,remove(){this.removed=true},setAttribute(){},closest(){return {}}});
global.document={getElementById:element,querySelector:element};
"""
        result = subprocess.run(['node', '-e', stub + source], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
