# Many Ingest — Ontwerp voor Lokaal-nu, Cloud-later

**Status:** Architectuurontwerp — nog geen code
**Rol:** Senior software architect
**Datum:** 2026-08-03
**Aanvulling op:** [MANY_INGEST_BUILD_PLAN.md](./MANY_INGEST_BUILD_PLAN.md)

Het bouwplan beschrijft **wat** Many Ingest v0.1 doet. Dit document beschrijft **hoe** we
het zo bouwen dat de latere stap naar een server/cloud-omgeving een kwestie van
componenten vervangen is — niet van herschrijven. De kernregel: **de bedrijfslogica mag
nooit weten of hij lokaal of in de cloud draait.**

---

## 1. Welke technische keuzes we nu moeten maken

### 1.1 Ports & Adapters (hexagonale architectuur)

De kernlogica (scannen, organiseren, dupliceren-checken, registreren) wordt geschreven
tegen **abstracte interfaces** ("ports"), niet tegen concrete techniek. Lokale
implementaties ("adapters") vullen die interfaces nu in; cloud-implementaties vullen ze
later in — zonder dat de kernlogica verandert.

```
              ┌─────────────────────────────┐
              │      IngestService          │   ← kernlogica, kent alleen interfaces
              │  (scan → metadata → organize │
              │   → dedupe → copy → register)│
              └──────────────┬──────────────┘
                             │ gebruikt
                 ┌───────────┴───────────┐
                 ▼                       ▼
          ┌─────────────┐         ┌─────────────┐
          │  Storage     │         │  Manifest    │   ← interfaces ("ports")
          │  (interface) │         │  (interface) │
          └──────┬──────┘         └──────┬──────┘
                 │                       │
        ┌────────┴────────┐     ┌────────┴────────┐
        ▼                 ▼     ▼                 ▼
  LocalFilesystem      S3Storage  JSONManifest    SQLiteManifest / PostgresManifest
  Storage (v0.1)       (later)    (v0.1)          (later)
```

Concreet betekent dit nu al drie interfaces vastleggen:
- **`Storage`** — lezen/schrijven/kopiëren/bestaat-check van bestanden, ongeacht of de
  bron een gemount volume, een netwerkschijf of straks een S3-bucket is.
- **`Manifest`** — vastleggen en opvragen van ingestgegevens volgens de ManyFast Asset
  Schema (asset, checksum, bestemming, tijdstip), ongeacht of dat een JSON-bestand
  (v0.1), SQLite of straks Postgres is.
- **`MetadataExtractor`** — ffprobe-wrapper; blijft technisch vrijwel identiek lokaal en
  in de cloud, maar toch als interface zodat een cloud-variant (bijv. een managed
  transcodeservice) er later achter kan.

### 1.2 Identiteit op basis van inhoud, niet op basis van pad

Een asset krijgt een ID op basis van zijn **checksum (SHA-256)**, niet op basis van
bestandspad of een oplopend nummer. Dit is de belangrijkste keuze voor latere migratie:
een pad-gebaseerde of autoincrement-ID is alleen zinvol op één machine; een
inhoud-gebaseerde ID blijft correct als er straks meerdere ingest-stations of machines
tegelijk naar één centrale opslag schrijven.

### 1.3 ManyFast Asset Schema alvast "multi-tenant-vormig" ontwerpen

Ook al draait v0.1 single-user op één Mac: de ManyFast Asset Schema krijgt nu al velden
als `client_id`, `project_id`, `ingest_run_id` en `operator`/`source_machine` (lokaal
gevuld met bijv. de Mac-gebruikersnaam en hostnaam). Zo is de latere overstap naar een
gedeelde database een **data-migratie**, geen **schema-herontwerp**.

### 1.4 Configuratie extern, nooit hardcoded

Opslaglocatie, klant/project-regels en welke adapter actief is (`storage.backend:
local`) staan in een config-bestand, niet in code. Dat is nu een lokaal YAML-bestand;
later kan dezelfde configuratiestructuur uit environment variables of een
secrets-manager komen zonder dat de code die het leest verandert.

### 1.5 De CLI is een dunne laag, geen plek voor logica

`cli.py` parseert argumenten, bouwt op basis van config de juiste adapters (de
"composition root"), en roept `IngestService.run(...)` aan. Er staat geen
bedrijfslogica in de CLI-laag. Dat is precies het punt waar later een HTTP-API of
worker-proces dezelfde `IngestService` kan aanroepen, zonder dat er iets herschreven
hoeft te worden.

### 1.6 Structured logging als kiem van een toekomstig event-systeem

Elke belangrijke stap (asset gevonden, gekopieerd, geverifieerd, geregistreerd) wordt
gelogd als een gestructureerde regel (JSON-lines: `event`, `asset_id`, `timestamp`,
...), niet als vrije tekst. Er komt nu geen message-bus — dat zou overbouwen zijn voor
v0.1 — maar de logs zijn al in de vorm die een latere event-bus (zoals beschreven in de
ManyOS-architectuur) direct kan consumeren.

---

## 2. Welke keuzes we moeten vermijden

- **Geen bedrijfslogica die rechtstreeks `os.path`/`shutil` aanroept.** Alles via de
  `Storage`-interface — anders zit lokale-bestandssysteemkennis straks overal
  verspreid in code die niet cloud-bewust hoeft te zijn.
- **Geen opslagformaat-specifieke aannames in de kernlogica.** Toegang tot de ManyFast
  Asset Schema loopt via de `Manifest`-interface — geen JSON-bestandsstructuur-specifieke
  trucs nu, en geen SQL-specifieke aannames straks, diep in de businesslogica.
- **Geen autoincrement-ID's of pad-als-identiteit.** Zoals hierboven: dit werkt lokaal
  prima, maar botst zodra er meerdere bronnen tegelijk schrijven. Checksum-gebaseerde
  ID's nu voorkomt een pijnlijke identiteitsmigratie later.
- **Geen bedrijfslogica in `cli.py`.** Zodra argumentparsing en kernlogica door elkaar
  lopen, moet je bij een API/worker-laag later alles reverse-engineeren uit de
  CLI-handlers.
- **Geen zelfgebouwd, in-memory "event-systeem"** (bijv. callbacks/observers binnen één
  proces) alsof het al een message-bus is. Dat geeft schijnzekerheid en moet toch
  weggegooid worden zodra er een echte bus komt. Structured logs nu, echte bus later.
- **Geen macOS-specifieke aannames in de kernlogica.** Dingen als "welke volumes zijn
  gemount" mogen Mac-specifiek zijn, maar geïsoleerd in een klein, apart adapter-laagje
  — niet vermengd met scan/organize/copy-logica die overal moet kunnen draaien.
- **Niet nu al een message-queue, microservices of multi-server opzet bouwen.** Dat is
  overbouwen voor v0.1. Het punt is niet "bouw de cloud-versie alvast", maar "laat de
  naad open" via de interfaces hierboven.
- **Geen hardcoded lokale paden diep in de logica** (bijv. `~/.many-ingest/asset_schema.json`
  letterlijk in `organizer.py`). Alles komt uit config, ook al is er nu maar één
  waarde mogelijk.

---

## 3. Hoe we lokaal beginnen

Dit volgt exact het bouwplan (v0.1: Python CLI, lokaal bestandssysteem, JSON-opslag
voor de ManyFast Asset Schema, ffprobe), maar nu opgebouwd achter de interfaces uit
sectie 1:

```
many_ingest/
├── core/
│   └── ingest_service.py     # kernlogica — kent alleen Storage/Manifest-interfaces
├── ports/
│   ├── storage.py             # interface: list/read/copy/exists/checksum
│   └── manifest.py             # interface: register/is_duplicate/query
├── adapters/
│   ├── local_fs_storage.py       # implementatie van Storage voor lokaal bestandssysteem
│   └── json_manifest.py           # implementatie van Manifest via een JSON-bestand
├── config.py                       # laadt YAML, bepaalt welke adapters actief zijn
└── cli.py                            # dunne laag: parse args → composition root → run()
```

- **Composition root** (bijv. in `cli.py` of een klein `bootstrap.py`): leest config,
  kiest op basis van `storage.backend`/`manifest.backend` welke adapters gebouwd
  worden, en geeft die aan `IngestService`. Vandaag is er maar één keuze per interface
  — dat is prima, het gaat om de plek waar die keuze straks bijkomt.
- **De ManyFast Asset Schema** bevat vanaf dag één: `asset_id (checksum)`, `client_id`,
  `project_id`, `ingest_run_id`, `operator`, `source_machine`, `original_path`,
  `destination_path`, `ingested_at`. Lokaal gevuld met verstandige standaardwaarden
  (huidige gebruiker, hostnaam van de Mac).
- **Logs**: JSON-lines per stap, lokaal weggeschreven — inhoudelijk al "eventvormig",
  technisch nog gewoon een bestand.

Dit is niet meer werk dan het oorspronkelijke bouwplan — het is vooral een kwestie van
de mappenstructuur en een paar interface-definities vooraf vastleggen, in plaats van
alles rechtstreeks tegen het JSON-bestand/bestandssysteem te schrijven.

---

## 4. Hoe de overgang naar server/cloud er later uit kan zien

Wanneer het moment komt (meerdere ingest-stations, meerdere locaties, of een centrale
ManyOS Core die moet meelezen), verandert dit **stap voor stap, adapter voor adapter** —
nooit in één keer:

1. **Opslag**: `storage.backend: local` → `storage.backend: s3`. Een nieuwe
   `S3Storage`-adapter implementeert dezelfde `Storage`-interface. `IngestService`
   verandert geen regel.
2. **ManyFast Asset Schema-opslag**: `manifest.backend: json` → `manifest.backend:
   sqlite` (tussenstap) of direct → `manifest.backend: postgres`. Een nieuwe
   `SQLiteManifest`- of `PostgresManifest`-adapter implementeert dezelfde
   `Manifest`-interface. Omdat het schema al `client_id`/`project_id`/checksum-ID's
   had, is dit een export/import, geen herontwerp.
3. **Aansturing**: naast de bestaande CLI komt er een dunne **HTTP-API** (bijv.
   FastAPI) of een **worker die jobs van een queue oppakt** (bijv. getriggerd doordat
   een bestand in een bucket verschijnt). Beide roepen dezelfde `IngestService.run(...)`
   aan — de CLI wordt dan één van meerdere "front-ends" in plaats van de enige.
4. **Events**: de gestructureerde JSON-log-regels worden ook gepubliceerd op een echte
   event-bus (bijv. "AssetIngested"), waarmee Many Ingest de eerste echte
   event-bron wordt voor de bredere ManyOS-architectuur (Core, latere Asset
   Intelligence-module, notificaties).
5. **Configuratie**: het lokale YAML-bestand wordt vervangen door environment
   variables/secrets-manager — dezelfde config-structuur, andere bron.
6. **Hybride tussenstap (optioneel):** voordat alles volledig cloud is, kan een
   ingest-station lokaal blijven werken (snelheid op locatie, geen wifi-afhankelijkheid
   tijdens een shoot) en enkel de ManyFast Asset Schema-deltas periodiek naar de
   centrale Postgres synchroniseren. Dit werkt zonder aanpassing van de kernlogica, puur
   door een extra sync-stap toe te voegen die de bestaande adapters gebruikt.

**Wat dit ontwerp oplevert:** de dag dat we van één Mac naar meerdere ingest-stations of
een centrale cloudopslag gaan, is dat een kwestie van nieuwe adapters schrijven en een
config-regel omzetten — niet van `many_ingest` opnieuw bouwen.
