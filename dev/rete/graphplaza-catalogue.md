# The rete catalogue on data.graphplaza.com

**64 published `.rete` graphs**, 2,550,223,985 triples between them. Listed by the MCP
server's `list_datasets` on 2026-08-18.

A `.rete` file is a single-file RDF graph on plain HTTP storage, read lazily by byte
range — a query fetches only the bytes it touches, so a 673M-triple graph is usable
without downloading it. Every URL below is also a SPARQL 1.1 endpoint at
`/sparql/<key>`, usable in a federated `SERVICE` clause.

Grouping is mine, for navigation; the catalogue itself is a flat list. A `—` in the
triples column means the listing carries no count for that graph, not that it is empty.

## Research software, scholarship & citations

| key | triples | what it is |
|---|---|---|
| [`causenet`](https://data.graphplaza.com/causenet-full-typed/causenet-full-typed.rete) | 256,100,000 | a causality graph mined from the web (256M triples, typed + full-text, remote, lazy) |
| [`zenodo-records`](https://data.graphplaza.com/zenodo-records/zenodo-records.rete) | 215,000,000 | every Zenodo record (7.76M) as a DataCite scholarly graph |
| [`biosyslit`](https://data.graphplaza.com/biosyslit/biosyslit.rete) | 106,000,000 | Zenodo Biodiversity Literature Repository: taxonomic treatments with Darwin Core taxonomy + IIIF |
| [`gotriple`](https://data.graphplaza.com/gotriple/gotriple.rete) | 57,700,000 | GoTriple: 2.7M Social-Sciences & Humanities publications, DOI-federated |
| [`chebi-full`](https://data.graphplaza.com/chebi-full/chebi-full.rete) | 8,830,000 | the complete ChEBI chemical ontology |
| [`open-pulse`](https://data.graphplaza.com/open-pulse/open-pulse.rete) | 3,700,000 | EPFL/SDSC research-software knowledge graph |
| [`chemotion`](https://data.graphplaza.com/chemotion/chemotion.rete) | 1,530,000 | the Chemotion chemistry-ELN knowledge graph |
| [`orkg`](https://data.graphplaza.com/orkg/orkg.rete) | 37,314 | research contributions |
| [`openalex-astrocytes`](https://data.graphplaza.com/openalex-astrocytes/openalex-astrocytes.rete) | 24,042 | astrocyte research graph (OpenAlex) |
| [`ontoneurolog`](https://data.graphplaza.com/ontoneurolog/ontoneurolog.rete) | 17,658 | a neuroimaging data-sharing ontology (OntoNeuroLOG v2.2, remote, lazy) |
| [`opencitations`](https://data.graphplaza.com/opencitations/opencitations.rete) | 8,103 | a citation neighborhood |
| [`monarch`](https://data.graphplaza.com/monarch/monarch.rete) | 7,811 | disease/gene/phenotype graph |
| [`scholar`](https://data.graphplaza.com/scholar/scholar.rete) | 6,954 | synthetic scholarly world |
| [`scholar-noisy`](https://data.graphplaza.com/scholar-noisy/scholar-noisy.rete) | 6,671 | same world, 25% noise |
| [`nidm`](https://data.graphplaza.com/nidm/nidm.rete) | 3,153 | Neuroimaging Data Model: a real cohort + PROV provenance (federates with ontoneurolog) |
| [`causalgraph`](https://data.graphplaza.com/causalgraph/causalgraph.rete) | 315 | causal graphs in knowledge graphs (Fraunhofer IWU ontology + example, remote, lazy) |
| [`causal`](https://data.graphplaza.com/causal/causal.rete) | 170 | cardiometabolic causal model (confounders, mediators, colliders, loops) |

## Libraries, archives & cultural heritage

| key | triples | what it is |
|---|---|---|
| [`databnf`](https://data.graphplaza.com/databnf-full/databnf-full.rete) | 673,500,000 | data.bnf.fr - the whole BnF linked open data (716M triples, ONE file) |
| [`bne`](https://data.graphplaza.com/bne-full/bne-full.rete) | 267,000,000 | datos.bne.es - the whole Spanish National Library LOD (267M triples, ONE file) |
| [`biblissima`](https://data.graphplaza.com/biblissima-full/biblissima-full.rete) | 254,000,000 | Biblissima+ - medieval written heritage (full Wikibase, ONE file) |
| [`bcul`](https://data.graphplaza.com/bcul/bcul.rete) | 117,000,000 | BCU Lausanne: a digital twin of the whole library, incl. where every book is held |
| [`mmm`](https://data.graphplaza.com/mmm/mmm.rete) | 23,300,000 | the full Mapping Manuscript Migrations provenance graph (23.3M triples, remote, lazy) |
| [`bcn`](https://data.graphplaza.com/bcn/bcn.rete) | 10,400,000 | Arxiu Municipal de Barcelona (the whole city archive) |
| [`arxiu`](https://data.graphplaza.com/arxiu/arxiu.rete) | 7,280,000 | Arxius en Línia: complete Catalan archives, with scanned documents |
| [`bph`](https://data.graphplaza.com/bph/bph.rete) | 5,770,000 | Embassy of the Free Mind - 2,307 hermetic & esoteric books (Bibliotheca Philosophica Hermetica, remote, lazy) |
| [`memoria`](https://data.graphplaza.com/memoria/memoria.rete) | 1,210,000 | Memòria - Spanish Civil War victims & mass graves (open data) |
| [`ecal`](https://data.graphplaza.com/ecal/ecal.rete) | 1,020,000 | ECAL: the art & design school library, as a graph |
| [`albala`](https://data.graphplaza.com/albala/albala.rete) | 927,000 | Institución Colombina (ARCAS, Sevilla) - Seville cathedral + archdiocese archives, 69,900 ISAD records (remote-lazy) |
| [`boe`](https://data.graphplaza.com/boe/boe.rete) | 465,000 | BOE — Legislación Consolidada: every in-force Spanish law (12,330) as an ELI citation graph |
| [`vidy`](https://data.graphplaza.com/vidy/vidy.rete) | 457,000 | Archives de la Ville de Lausanne (the city archive, on AtoM) |
| [`jonas`](https://data.graphplaza.com/jonas/jonas.rete) | 234,000 | medieval texts & their manuscript witnesses (LostMa-ERC / Jonas) |
| [`getty-ulan`](https://data.graphplaza.com/getty-ulan/getty-ulan.rete) | 205,000 | artist mentorship lineage |
| [`ustc`](https://data.graphplaza.com/ustc/ustc.rete) | 88,500 | USTC - Universal Short Title Catalogue (727 Embassy/BPH-cited editions, remote, lazy) |
| [`postscriptum`](https://data.graphplaza.com/postscriptum/postscriptum.rete) | 63,000 | Post Scriptum - Portuguese & Spanish everyday letters, 1500-1800 (CLUL) |
| [`lineara`](https://data.graphplaza.com/lineara/lineara.rete) | 38,306 | the Linear A corpus (undeciphered Minoan script, remote, lazy) |
| [`peirce`](https://data.graphplaza.com/peirce/peirce.rete) | 36,179 | Charles S. Peirce papers - the Houghton finding aid as a graph, with 45k IIIF pages |
| [`factgrid-illuminati`](https://data.graphplaza.com/factgrid-illuminati/factgrid-illuminati.rete) | 34,979 | Order of the Illuminati prosopography |
| [`theographic-graph`](https://data.graphplaza.com/theographic-graph/theographic-graph.rete) | 31,945 | biblical narrative graph |
| [`mimotext`](https://data.graphplaza.com/mimotext/mimotext.rete) | 27,389 | French Enlightenment novels + stylometry |
| [`scrolls`](https://data.graphplaza.com/scrolls/scrolls.rete) | 18,125 | Vesuvius Challenge - Herculaneum scrolls: the data browser as a graph, with download links |
| [`smithsonian3d`](https://data.graphplaza.com/smithsonian3d/smithsonian3d.rete) | 15,639 | 2,199 interactive 3D models (Smithsonian Open Access, CC0, remote, lazy) |
| [`linked-jazz`](https://data.graphplaza.com/linked-jazz/linked-jazz.rete) | 9,466 | jazz musician social network |
| [`mira`](https://data.graphplaza.com/mira/mira.rete) | 5,044 | early Irish manuscripts (MIrA, Wikidata-aligned, IIIF, remote, lazy) |
| [`ramon_llull`](https://data.graphplaza.com/ramon_llull/ramon_llull.rete) | 2,877 | BVPB - Ramón Llull: 142 digitised editions & manuscripts |
| [`fuero_juzgo`](https://data.graphplaza.com/fuero_juzgo/fuero_juzgo.rete) | 1,539 | Fuero Juzgo — the manuscript tradition: 52 witnesses, IIIF, Wikidata & Biblissima |
| [`antarctic-expeditions`](https://data.graphplaza.com/antarctic-expeditions/antarctic-expeditions.rete) | 275 | Heroic-Age expeditions, crews & ships |

## Reference & encyclopaedic

| key | triples | what it is |
|---|---|---|
| [`wikidata-ontology`](https://data.graphplaza.com/wikidata-ontology/wikidata-ontology.rete) | 185,500,000 | every Wikidata class: the ontology without the instances |
| [`nomisma`](https://data.graphplaza.com/nomisma/nomisma.rete) | 53,535 | coinage of Alexander the Great (PELLA) |
| [`wikidata-zenodo`](https://data.graphplaza.com/wikidata-zenodo/wikidata-zenodo.rete) | — | wikidata with Zenodo DOI (1 GB) - citable source, stable R2 delivery |

## Life sciences & biodiversity

| key | triples | what it is |
|---|---|---|
| [`gbif-birds`](https://data.graphplaza.com/gbif-birds/gbif-birds.rete) | 334,000,000 | GBIF Birds — Spain & Switzerland: 50M sightings with a taxonomic zoom + map |
| [`bioexplora`](https://data.graphplaza.com/bioexplora/bioexplora.rete) | 7,890,000 | 207k natural-history specimens (Museu de Ciencies Naturals de Barcelona, Darwin Core, remote, lazy) |

## Geospatial & historical geography

| key | triples | what it is |
|---|---|---|
| [`ohm-full`](https://data.graphplaza.com/ohm-full/ohm-full.rete) | 6,100,000 | all of OpenHistoricalMap |
| [`geoadmin`](https://data.graphplaza.com/geoadmin/geoadmin.rete) | 372,500 | world admin boundaries (geoBoundaries/OSM, 52k polygons, GeoSPARQL, remote, lazy) |
| [`geoadmin-tiles`](https://data.graphplaza.com/geoadmin-tiles/geoadmin-tiles.rete) | 372,500 | the admin-boundaries graph AND a PMTiles vector basemap in ONE file |
| [`history`](https://data.graphplaza.com/history/history.rete) | 14,430 | historical world borders (GeoSPARQL, remote, lazy) |

## Sport, games & media

| key | triples | what it is |
|---|---|---|
| [`tracking`](https://data.graphplaza.com/tracking/tracking.rete) | 2,550,000 | a full match as a spatiotemporal graph (player + ball 2D positions over time) |
| [`mtg`](https://data.graphplaza.com/mtg/mtg.rete) | 1,230,000 | Magic: The Gathering: the whole card pool as a graph (34,633 cards, with images) |
| [`subtitles`](https://data.graphplaza.com/subtitles/tears_of_steel.rete) | 14,400 | one film, 20 languages, over time (Tears of Steel · CC-BY) |
| [`worldcup`](https://data.graphplaza.com/worldcup/worldcup.rete) | 8,100 | FIFA World Cup 2022: stats, player careers & multi-source predictions |
| [`worldcup2026`](https://data.graphplaza.com/worldcup2026/worldcup2026.rete) | 4,600 | FIFA World Cup 2026: a LIVE snapshot, cross-checked vs live sources (results + player careers) |
| [`mtg-game`](https://data.graphplaza.com/mtg-game/mtg_game.rete) | 341 | a live Magic game state as OWL + SPARQL (no engine) |

## Everything else

| key | triples | what it is |
|---|---|---|
| [`mira-wikidata`](https://data.graphplaza.com/mira-wikidata/mira-wikidata.rete) | 125 | MIrA↔Wikidata mappings (SSSOM linkset, remote, lazy) |
| [`wikidata`](https://data.graphplaza.com/wikidata-1GB/wikidata.rete) | — | 1GB.rete - a real 1 GB Wikidata graph |
| [`wikidata-100mb`](https://data.graphplaza.com/wikidata-100MB/wikidata.rete) | — | wikidata-100MB.rete - a real 100 MB Wikidata slice |

