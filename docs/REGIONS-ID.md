# Indonesia offline maps

This fork implements an Indonesia map builder and regional map display. It uses `IDN` for the country;
`ID` remains Idaho. It does not pretend that an Indonesia download exists on Lastbook's upstream server.

## Included and omitted

The first version builds streets, mapped building outlines, waterways, water polygons, land use,
towns and help points from OpenStreetMap. Indonesian names are preserved. Towns and named help points
are searchable offline. Multipolygon relations are assembled before encoding them in Lastbook's existing
SQLite geometry format. There is no additional desktop dependency or network request.

This is a country extract, not separate provincial packs. The
[Geofabrik Indonesia extract](https://download.geofabrik.de/asia/indonesia.html) also includes Timor-Leste.
That extra coverage is disclosed in the map's source metadata and panel; no political boundary clipping is claimed.
Extract coverage is not a place address. Search results omit that generic note, including
when reading early IDN packs that stored it as every place's detail.
A `--bbox` build is explicitly marked as a sample. Its bounding box is an intersection filter, so complete
features crossing the box are retained. Coverage and completeness depend on OpenStreetMap contributors.

Not included: elevation/contours, aerial imagery, a national crop or dam inventory, or house-number search.
Coastlines retain the bundled world-outline detail; this builder does not produce high-resolution ocean polygons.
OSM building footprints are used; Microsoft and Overture data are not silently claimed as included.
The initial implementation does not download from Indonesian government portals or assume that publicly
viewable data is licensed for redistribution. It does not translate the library's manuals or guides.

## Build

Build dependencies are separate from desktop dependencies. Pyosmium is BSD-2-Clause and used only by the
build tool. The tool reads a local input and opens no network connection. It records the source SHA-256,
data date, coverage, feature counts and caught geometry-conversion errors in its output. It refuses to overwrite an existing build.

```sh
python3 -m venv derived/map-tools
derived/map-tools/bin/pip install -r packaging/requirements_maps.txt
mkdir -p derived/indonesia
curl --fail --location --continue-at - \
  --output derived/indonesia/indonesia-261101.osm.pbf \
  https://download.geofabrik.de/asia/indonesia-261101.osm.pbf
curl --fail --location \
  --output derived/indonesia/indonesia-261101.osm.pbf.md5 \
  https://download.geofabrik.de/asia/indonesia-261101.osm.pbf.md5
(cd derived/indonesia && md5sum --check indonesia-261101.osm.pbf.md5)
derived/map-tools/bin/python tools/build_indonesia.py \
  derived/indonesia/indonesia-261101.osm.pbf derived/indonesia/IDN \
  --data-as-of 2026-11-01 \
  --preview-library derived/indonesia/MapPreview
```

The dated source is about 1.74 GB. The publisher's MD5 detects transfer damage; it is not a signature.
Budget additional disk for the node-location cache, SQLite databases, zip and optional preview copy.
The builder uses a disk-backed node cache and streams features rather than holding the whole country in Python lists.
It writes `IDN/`, `IDN.zip`, and `IDN.zip.sha256`. The archive follows the existing `map-set.json` and
map-member contract. Keep the checksum with your build records.

For a faster renderer test, add `--bbox=106.7,-6.3,106.9,-6.1` and use a different output directory.
The whole input is still scanned to resolve way nodes and multipolygon relations.

## Open or install

The optional preview is a separate **map-only** library with an empty catalog. It is for browsing the map;
it does not claim to include downloaded manuals, videos, a local AI model or an address index.
Run the fork with its normal desktop dependencies:

```sh
python3 app/main.py --data derived/indonesia/MapPreview
```

To put the map into an existing library, close every Lastbook app/server using that library first.
Use a SHA-256 from your own build or a trusted publisher. The importer checks that digest, uses Lastbook's
existing safe zip unpacker, checks database integrity, and preserves the old map in a sibling backup directory.
It never downloads, changes the catalog, deletes the backup, or weakens the downloader's allowlist.

```sh
python3 tools/install_indonesia_map.py derived/indonesia/IDN.zip \
  --library /path/to/LastbookData --sha256 YOUR_BUILD_SHA256 --library-closed
```

Open the library using this fork for the regional framing, source labels and setup text. To restore the old
map, close the app and use `engine.setup.install.swap_map(library, backup, another_empty_backup_directory)`;
keep both backups until the restored map is verified. The importer's lock serializes importer runs only:
it cannot prove that another app instance has closed its SQLite connections.

The normal online selector only lists maps from its validated manifest, plus an installed local Indonesia map.
US `All` covers US states only; selecting a published non-US map replaces it. A downloadable Indonesia choice
requires a separately published archive and its real URL, size and SHA-256 in a release manifest. This fork
has not added a fabricated URL, enabled a new download host, or published large map data in Git.

## Validation

```sh
derived/map-tools/bin/python -B -m unittest app.tests.test_indonesia app.tests.test_address_search
```

The tests build a small OSM XML fixture with roads, buildings, a water multipolygon, a town and a hospital;
exercise the real renderer and place finder; check map-pack installation and preservation of the existing
catalog and old map; reject a checksum mismatch; and run setup selection logic in Node to preserve US behavior.
The parser tests require the build-only dependency. Synthetic tests do not establish real-world completeness.

## Attribution

Map data: © OpenStreetMap contributors, [ODbL 1.0](https://www.openstreetmap.org/copyright),
provided through [Geofabrik](https://www.geofabrik.de/data/download.html).
Redistributors must retain attribution and meet ODbL obligations for derived databases, including offering
the applicable database/source. Generated data is intentionally excluded from Git. The build recipe and
source snapshot identifier are recorded so recipients can reproduce it.

### Fork checks on Linux

The regression run on 17 November 2026 executed 325 tests: 320 passed, 3 skipped, and 2 failed.
Both failures also reproduce in the untouched upstream checkout at `256d52f`:
`Recommend.test_hardware_here` accepts only macOS/Windows, and `UpdateTest.test_available`
checks a macOS minimum OS version while running on Linux. They are not treated as passing.
All seven new Indonesia tests pass, as do the eight existing address-search tests.
The web/content shipping scan passes (34 files). A synthetic map was exercised in Chrome,
including place search and regional attribution, and the Linux GTK app opened with an isolated test profile.
These checks do not substitute for full-country data validation or tests on Windows/macOS.

### Full extract validation

On 18 November 2026, code at `aed51ce` built the complete Geofabrik
`indonesia-261101.osm.pbf` snapshot (`2026-11-01T21:21:36Z`). Its publisher MD5
was checked after download; the source SHA-256 is
`ad78147654bb00cf777ed5e6a3270f29503c99670e6e1fa05ed4a11d7ebb3dda`.

The result contains 50,638,845 features, 130,086 points of interest and 221,000
searchable places. Thirty geometry-conversion errors were caught and skipped.
Map files occupy 8,438,902,847 bytes; the archive is 2,930,228,976 bytes with SHA-256
`e915da4bb348b2ed21d5109a2af41e62d7249998cc36038f7c85f768d05fa99d`.
All SQLite quick checks, ZIP integrity and archive checksum verification passed.

Offline city searches and nonempty rendered tiles passed for Jakarta, Bandung,
Surabaya, Denpasar, Makassar and Jayapura. Jakarta search, roads and help points
were also inspected in the browser. This verifies those paths on the full extract;
it does not certify every mapped feature or establish complete real-world coverage.
The archive is generated locally, not published as a hosted download.
