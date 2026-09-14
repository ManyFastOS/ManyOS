# Many Ingest — Opslagstructuur voor externe SSD/NAS

**Status:** Geïmplementeerd (Fase 3.5 — Dynamic Destination Selection, 2026-09-14)
**Rol:** CTO ManyFast
**Datum:** 2026-08-03, bijgewerkt 2026-09-14
**Aanleiding (bijgewerkt):** ManyFast bleek in de praktijk niet één vaste
bestemmingsschijf te gebruiken, maar meerdere externe schijven, per ingest
verschillend. Een vaste `storage_root: "/Volumes/<naam>/..."` in config.yaml (het
oorspronkelijke voorstel hieronder) veroorzaakte een echte bug: een ingest faalde
omdat config.yaml naar een specifieke schijf wees die die dag niet aangesloten was.
**De mapstructuur hieronder is ongewijzigd gebleven — alleen wélke fysieke schijf de
root van die structuur is, is sinds Fase 3.5 een runtime-keuze per ingest (GUI-
kiezer of CLI's verplichte `--destination`), niet meer een vaste config-waarde.**

Dit document beschrijft: (1) de folderstructuur op elke externe SSD/NAS die als
bestemming wordt gekozen, (2) hoe die structuur runtime tot stand komt (sectie 2), en
(3) waarom deze schaalbaar is naar meerdere klanten/projecten, meerdere
bestemmingsschijven, en een toekomstige cloudmigratie.

---

## 1. Aanbevolen folderstructuur

```
{EXTERNE_ROOT}/                              een per ingest gekozen bestemmingsschijf,
│                                             bijv. /Volumes/Chris/ManyFast — verschilt
│                                             per keer, staat niet in config.yaml
├── Footage/                                 <- klantmateriaal (storage_root)
│   └── Klanten/
│       ├── Nike/
│       │   └── Zomer Campagne/
│       │       └── 2026-08-03_Raw/
│       │           ├── Camera/
│       │           ├── Drone/
│       │           ├── Audio/
│       │           └── Onbekend/
│       └── ManyFast/
│           └── Jan Rotmans/
│               ├── 2026-06-26_Raw/
│               │   ├── Camera/
│               │   ├── Audio/
│               │   └── Onbekend/
│               └── 2026-07-27_Raw/
│                   └── Onbekend/
│
└── ManyOS/                                  <- systeemdata, geen klantmateriaal
    ├── AssetSchema/
    │   └── asset_schema.json                <- manifest_path (ManyFast Asset Schema)
    └── Logs/
        └── {run_id}.jsonl                   <- log_dir (actielogboek per run)
```

**Twee toplevel-mappen, met opzet:**
- **`Footage/`** — het enige wat editors/freelancers ooit hoeven te zien. Puur
  klantmateriaal, ingedeeld per klant en project (`Klanten/{Client}/{Project}/...`),
  precies de bestaande Project Workspace-conventie uit het bouwplan — die verandert
  hier niet.
- **`ManyOS/`** — systeemdata: de ManyFast Asset Schema en de actielogs. Nooit
  klantmateriaal, nooit iets waar een editor in hoeft te kijken.

Dit vervangt de huidige situatie waarin `storage_root` op de Mac zelf staat en
`manifest_path`/`log_dir` daarnaast óók nog eens lokaal op de Mac staan
(`~/.many-ingest/...`). In dit voorstel staat **alles** — footage, schema, logs — op
de externe SSD/NAS. Niets persistents blijft op de Mac achter.

---

## 2. `ingest_config.yaml` — alleen de relatieve structuur, nooit een schijf

```yaml
# ManyOS Many Ingest — lokale configuratie (v0.1, Fase 3.5)
#
# Geen schijfnaam hier — ManyFast gebruikt meerdere externe bestemmingsschijven,
# niet één vaste. Welke fysieke schijf gebruikt wordt, kies je per ingest (GUI-
# kiezer of --destination op de CLI). Dit bestand legt alleen de vaste, relatieve
# mapstructuur vast die onder ELKE gekozen bestemmingsschijf wordt aangemaakt.

footage_subpath: "ManyFast/Footage"
manifest_subpath: "ManyFast/ManyOS/AssetSchema/asset_schema.json"
log_subpath: "ManyFast/ManyOS/Logs"
```

**Hoe dit runtime tot stand komt** (`config.py`, `service_factory.py`):
`load_storage_layout(config_path)` leest deze drie relatieve subpaden (en weigert
expliciet een absolute waarde — een leftover van het oude formaat zou anders stil
verkeerd geïnterpreteerd worden). `resolve_ingest_config(layout, destination_root)`
plakt ze aan de per ingest gekozen `destination_root` (het mount-pad van de fysieke
schijf, bijv. `/Volumes/Chris`) tot precies dezelfde `IngestConfig`-vorm die
`IngestService` al sinds v0.1 aanneemt. **`IngestService` zelf, `ActionLogger`,
`JSONManifest` en alle adapters zijn door deze verandering niet aangeraakt** — ze
krijgen nog steeds gewoon een kant-en-klare, absolute `IngestConfig`; alleen hoe die
wordt samengesteld, veranderde.

`destination_root` komt binnen via, en moet in beide gevallen precies hetzelfde zijn
voor preview én de echte ingest:
- **GUI:** een expliciete stap in de flow (Bron → Klant → Project →
  **Bestemmingsschijf kiezen** → Preview → Start Ingest) — `desktop/volumes.py`'s
  `list_destination_volumes()` toont aangesloten, schrijfbare externe schijven (naam
  + vrije ruimte), met de gekozen bronschijf al uitgesloten.
- **CLI:** een verplichte `--destination`-optie, geen terugval op een oude
  `storage_root`-config-sleutel — één architectuur.

**Harde safety rule, overal hetzelfde gecontroleerd (`device_identity.py`):** bron
en bestemming mogen nooit dezelfde fysieke schijf zijn — vergeleken via `st_dev`,
nooit via padstrings of volumenamen. De GUI biedt de bronschijf domweg nooit aan als
bestemmingskeuze; CLI en worker controleren dit daarnaast zelf, als vangnet.

**Kanttekening om nu al te noemen, niet later te ontdekken:** de JSON-based ManyFast
Asset Schema heeft in v0.1 geen schrijf-locking. Zolang maar één Mac tegelijk
schrijft naar dezelfde schijf (het huidige uitgangspunt — v0.1 is expliciet
single-user, zie CLAUDE.md), is dit geen probleem. Zodra er twee ingest-stations
tegelijk naar dezelfde externe schijf zouden schrijven, is dat een reden om eerder
naar SQLite/Postgres te migreren (sectie 3), niet om zelf iets te bouwen.

---

## 3. Waarom deze structuur schaalbaar is

**Meerdere klanten en projecten.** De structuur was dat al (`Klanten/{Client}/{Project}/...`)
en blijft ongewijzigd — dit voorstel raakt alleen wáár die boom staat (extern i.p.v.
lokaal), niet de boom zelf. Een nieuwe klant of project toevoegen is een nieuwe map,
geen configuratie- of codewijziging.

**Scheiding footage / ManyOS-metadata / logs.** Dit is niet alleen netjes, het maakt
straks verschillend beleid per soort data mogelijk zonder dat iets anders hoeft te
verhuizen:
- `Footage/` is groot (terabytes) en verandert nooit na ingest — kandidaat voor
  langetermijnarchivering/back-up op zichzelf.
- `ManyOS/` is klein, verandert vaak, en is precies de data die je zou willen
  synchroniseren naar een centrale plek zodra er meer dan één ingest-station is —
  zonder de footage zelf te hoeven verplaatsen.
- Omdat ze nu al fysiek gescheiden zijn, is "alleen de kleine map syncen" straks een
  kwestie van een sync-target instellen, niet van eerst alles reorganiseren.

**Toekomstige cloudmigratie.** Dit voorstel is bewust zo gekozen dat het één-op-één
aansluit op de al vastgelegde migratieroute in
`MANY_INGEST_CLOUD_READY_ARCHITECTURE.md`:
- `Footage/Klanten/{Client}/{Project}/...` wordt straks de key-structuur in een
  S3/R2-bucket, ongewijzigd — een `S3Storage`-adapter implementeert dezelfde
  `Storage`-interface en gebruikt exact dezelfde relatieve paden.
- `ManyOS/AssetSchema/asset_schema.json` wordt straks een `SQLiteManifest`- of
  `PostgresManifest`-adapter — dezelfde `Manifest`-interface, andere backend.
- `ManyOS/Logs/` wordt straks input voor een echte event-bus.

Met andere woorden: de mapstructuur van vandaag is niet toevallig praktisch, hij is
letterlijk de lokale, tastbare vorm van de architectuur die al vastligt. Verhuizen
naar cloud betekent later "root wijzigt van een Volumes-pad naar een bucket/database",
niet "structuur herontwerpen."

---

## Historisch: wat hier openstond, en hoe het is opgelost

De oorspronkelijke versie van dit document (2026-08-03) ging nog uit van **één**
vaste primaire opslaglocatie, met als open vraag welke schijf/NAS dat zou worden.
Die aanname bleek niet te kloppen met de echte ManyFast-workflow (meerdere
bestemmingsschijven, wisselend per ingest) en is losgelaten met Fase 3.5 (2026-09-14,
zie `CLAUDE.md`'s "Architectural decisions already locked in"): de fysieke
bestemming is sinds dan altijd een runtime-keuze, nooit een vaste config-waarde. De
tweedeling `Footage/` vs `ManyOS/` uit sectie 1 hierboven staat wél nog steeds vast,
onder elke gekozen bestemmingsschijf, ongewijzigd sinds het oorspronkelijke voorstel.

Elke bestaande lokale `~/.many-ingest/config.yaml` die nog een absoluut
`storage_root`/`manifest_path`/`log_dir` bevat, moet handmatig gemigreerd worden naar
het relatieve formaat in sectie 2 — `load_storage_layout()` geeft een duidelijke
foutmelding (i.p.v. de oude waarde stil verkeerd te interpreteren) als dat nog niet
gebeurd is.
