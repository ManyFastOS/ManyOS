# Many Ingest — Opslagstructuur voor externe SSD/NAS

**Status:** Ontwerpvoorstel — nog geen code/config aangepast
**Rol:** CTO ManyFast
**Datum:** 2026-08-03
**Aanleiding:** de huidige `config/ingest_config.example.yaml` wijst naar
`~/ManyFast/Ingest` — lokale Mac-opslag. Dat is expliciet niet wat ManyFast wil.

Dit document beschrijft, vóórdat we ooit een niet-dry-run doen: (1) een aanbevolen
folderstructuur op externe SSD/NAS, (2) een aangepaste `ingest_config.yaml`, en
(3) waarom deze structuur schaalbaar is naar meerdere klanten/projecten en naar een
toekomstige cloudmigratie.

---

## 1. Aanbevolen folderstructuur

```
{EXTERNE_ROOT}/                              bijv. /Volumes/Extreme SSD/ManyFast
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

## 2. Aangepaste `ingest_config.yaml`

```yaml
# ManyOS Many Ingest — lokale configuratie (v0.1)
#
# Alles staat op de externe SSD/NAS, bewust niets op de Mac zelf. Pas het volumepad
# aan naar de daadwerkelijke schijfnaam/NAS-mount op de machine die dit draait —
# die naam kan per Mac verschillen.

storage_root: "/Volumes/Extreme SSD/ManyFast/Footage"
manifest_path: "/Volumes/Extreme SSD/ManyFast/ManyOS/AssetSchema/asset_schema.json"
log_dir: "/Volumes/Extreme SSD/ManyFast/ManyOS/Logs"
```

Geen enkel veld hoeft te veranderen in `config.py` of elders in de code — dit is
uitsluitend een andere invulling van dezelfde drie bestaande configuratiewaarden.
Precies zoals bedoeld: "extern zonder lokale Mac-opslag" is een config-keuze, geen
architectuurwijziging.

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

## Openstaand vóór dit wordt toegepast

1. Akkoord op de tweedeling `Footage/` vs `ManyOS/` en de exacte namen.
2. Bevestigen: welke externe schijf/NAS is de daadwerkelijke primaire opslag (nu
   getest op `/Volumes/Extreme SSD/ManyFast` — is dat ook de bedoelde definitieve
   locatie, of komt er een NAS bij?).
3. Pas dan: `config/ingest_config.example.yaml` daadwerkelijk aanpassen en een echte
   (niet-dry-run) test draaien.
