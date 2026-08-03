# Many Ingest — v0.1 Implementatieplan

**Status:** **Definitief voor v0.1 — akkoord, nog geen code**
**Rol:** CTO ManyFast
**Datum:** 2026-08-03 (bijgewerkt: JSON i.p.v. SQLite, move verwijderd, dry-run
toegevoegd, camera-profielen uitgewerkt, bouwvolgorde toegevoegd)
**Sluit aan op:** `docs/VISION.md` (Core Principles, Decision Filter) en `CLAUDE.md`
(terminologie, vastgelegde architectuurkeuzes). Aanvulling: `MANY_INGEST_CLOUD_READY_ARCHITECTURE.md`.

Dit vervangt het vorige implementatieplan. Vier architectuurwijzigingen t.o.v. de
vorige versie, allemaal in de richting van **eenvoudiger**:

1. ManyFast Asset Schema wordt tijdelijk in **JSON** opgeslagen i.p.v. SQLite.
2. **Move-functionaliteit is verwijderd** uit v0.1 — alleen veilig kopiëren.
3. Er komt een **dry-run/preview-modus** vóór er daadwerkelijk iets gekopieerd wordt.
4. Bronherkenning is uitgewerkt tot **uitbreidbare camera-profielen** (specifieke
   apparaten, niet alleen brede categorieën).

---

## 0. Doel en toetsing aan VISION.md

**Doel:** een lokale Mac-tool die een geselecteerde inputmap met videobestanden
analyseert en automatisch organiseert. Geen UI.

Getoetst aan VISION.md's Decision Filter (volledige ROI-onderbouwing in
`MANYOS_V0.1_PROPOSAL.md`):

1. **Lost dit een echt ManyFast-probleem op?** Ja.
2. **Bespaart het meetbare tijd?** Ja — tijd tot editor kan beginnen met snijden.
3. **Herbruikbaar?** Ja — elke shoot, elk project.
4. **Simpelste oplossing?** Ja, en dit plan maakt v0.1 nóg simpeler dan de vorige
   versie: JSON in plaats van SQLite, move geschrapt, geen speculatieve complexiteit.
5. **Zinvol over 3 jaar?** Ja.

---

## 1. Functionaliteit v0.1 (scope)

- Scan een geselecteerde inputmap.
- Herken bestandstype (video / audio / onbekend).
- Herken vermoedelijke bron via **camera-profielen** (zie sectie 4): Sony FX6, Sony
  A7IV, Sony FX3, DJI, GoPro, Audio — of "Onbekend" als niets matcht.
- Bepaal automatisch een georganiseerde Project Workspace-structuur.
- **Dry-run/preview-modus**: toon wat er zou gebeuren, zonder iets te wijzigen.
- Kopieer bestanden veilig (checksum-geverifieerd). **Geen move in v0.1.**
- Schrijf een logbestand van alle uitgevoerde (of, bij dry-run, voorgestelde) acties.
- Werk de ManyFast Asset Schema bij (lokaal, **JSON-bestand**).

Geen UI — alles via CLI. Geen cloud. Geen move. Geen SQLite (nog niet).

---

## 2. Architectuur & modulestructuur

Nog steeds ports & adapters (CLAUDE.md), maar met een simpelere v0.1-adapterkeuze:

```
many_ingest/
├── cli.py                       # dunne CLI-laag: parse args → composition root → run()
├── config.py                    # laadt ingest_config.yaml + camera_profiles.yaml
├── core/
│   └── ingest_service.py        # kernlogica: scan → classify → metadata → workspace-pad
│                                 # → dedupe → (dry-run? preview : copy → verify → schema
│                                 # bijwerken) → rapport
├── ports/
│   ├── storage.py               # interface: list/read/copy/exists/checksum
│   │                             # (géén move-methode in v0.1 — zie sectie 7)
│   └── manifest.py               # interface: register/is_duplicate/query (ManyFast Asset Schema)
├── adapters/
│   ├── local_fs_storage.py      # Storage-implementatie voor lokaal bestandssysteem
│   └── json_manifest.py          # Manifest-implementatie via een JSON-bestand
│                                 # (later: sqlite_manifest.py / postgres_manifest.py —
│                                 #  zelfde interface, geen wijziging aan ingest_service.py)
├── classification/
│   ├── file_types.py            # bestandstype-herkenning (extensie + lichte ffprobe-sniff)
│   └── camera_profiles.py       # past camera_profiles.yaml toe op bestandsnaam + metadata
├── metadata_extractor.py         # ffprobe-wrapper
└── logger.py                     # structured JSON-lines logging (actielog)

config/
├── ingest_config.yaml            # opslag-root, naamgevingsregels, bekende klanten/projecten
└── camera_profiles.yaml          # camera-profielen (zie sectie 4) — apart van code

tests/
├── test_file_types.py
├── test_camera_profiles.py
├── test_ingest_service.py       # inclusief dry-run-gedrag
└── ...
```

**Waarom JSON i.p.v. SQLite geen architectuurrisico is:** de `Manifest`-interface
(`ports/manifest.py`) verandert niet. `json_manifest.py` is een adapter zoals elke
andere; de overstap naar `sqlite_manifest.py` of `postgres_manifest.py` later is precies
het scenario waar de ports & adapters-opzet voor bedoeld is (zie
`MANY_INGEST_CLOUD_READY_ARCHITECTURE.md`). JSON is voor v0.1 simpelweg eenvoudiger:
geen schema-migraties nodig terwijl het schema nog kan veranderen, en het bestand is
met het blote oog te lezen tijdens ontwikkelen/debuggen.

**Waarom dry-run geen aparte implementatie is:** `ingest_service.run(..., dry_run=True)`
doorloopt exact dezelfde stappen (scan, classificeren, metadata, workspace-pad bepalen,
duplicaatcontrole) — alleen de bijwerkende stappen (kopiëren, schema bijwerken) worden
overgeslagen en vervangen door een preview-regel in het rapport. Eén codepad, geen
tweede "nep"-implementatie die kan gaan afwijken van de echte run (Simplicity Wins).

---

## 3. Welke technologieën we nodig hebben

| Behoefte | Keuze | Waarom |
|---|---|---|
| Taal | **Python 3.11+** | Sterk in file-automatisering, goede ffmpeg-integratie |
| Media-inspectie | **ffprobe** | Standaard, betrouwbaar, leest codec/resolutie/framerate/opnamedatum en (indien aanwezig) apparaat-tags |
| Bestandsoperaties | Python `pathlib`/`shutil` | Ingebouwd; v0.1 heeft alleen kopiëren nodig, geen move |
| Integriteitscontrole | `hashlib` (SHA-256) | Garandeert dat een kopie identiek is aan het origineel |
| Camera-profielen | YAML-bestand (`camera_profiles.yaml`) | Voorspelbaar, uitbreidbaar zonder code — bewust geen AI/ML |
| Configuratie | YAML | Leesbaar/aanpasbaar zonder code te wijzigen |
| **ManyFast Asset Schema** | **JSON-bestand** (tijdelijk, i.p.v. SQLite) | Eenvoudiger te starten, geen schema-migraties nodig terwijl het schema nog vorm krijgt; makkelijk te inspecteren tijdens ontwikkelen. Achter dezelfde `Manifest`-interface, dus later zonder herontwerp te vervangen door SQLite/Postgres. |
| CLI | `click` (of `argparse`) | Simpele command-line bediening, geen UI nodig |
| Logging | Python `logging`, JSON-lines | Nodig om exact te kunnen nagaan wat er tijdens een run (of dry-run) is gebeurd |

Bewust **geen** database-server, geen cloud-dienst, geen ML-model, geen move-logica —
dit moet volledig lokaal en zo simpel mogelijk kunnen draaien op de dag van een shoot.

---

## 4. Camera- en bronherkenning via uitbreidbare camera-profielen

Geen Asset Intelligence (CLAUDE.md-terminologie) — dit is deterministische,
uitlegbare regelmatching, geen AI/ML. Elke camera/bron is een **profiel** in
`camera_profiles.yaml`, niet in code, zodat een nieuw apparaat toevoegen geen
codewijziging is.

**v0.1 start met deze profielen** (illustratieve matching-signalen; exacte
bestandsnaam-patronen en metadata-signatures moeten met echte testbestanden van
ManyFast geverifieerd worden vóór implementatie):

| Profiel | Categorie (voor Project Workspace-submap) | Herkenningssignalen (illustratief) |
|---|---|---|
| **Sony FX6** | Camera | Bestandsnaam-patroon (bijv. `C####.*`-stijl) + ffprobe `model`-tag bevat "FX6"/"ILME-FX6" |
| **Sony A7IV** | Camera | Bestandsnaam-patroon + ffprobe `model`-tag bevat "A7IV"/"ILCE-7M4" |
| **Sony FX3** | Camera | Bestandsnaam-patroon + ffprobe `model`-tag bevat "FX3"/"ILME-FX3" |
| **DJI** | Drone | Bestandsnaam begint met `DJI_` + ffprobe `make`-tag bevat "DJI" |
| **GoPro** | Camera | Bestandsnaam-patroon (`GH*`/`GOPR*`/`GX*`) + ffprobe `make`-tag bevat "GoPro" |
| **Audio** | Audio | Bestand bevat alleen een audiostream (ffprobe: geen videostream) en/of extensie `.wav`/`.mp3` |
| *(fallback)* **Onbekend** | Onbekend | Geen enkel profiel matcht |

**Elk profiel in `camera_profiles.yaml` bevat:**
- `id` / `label` — bijv. `sony_fx6` / "Sony FX6"
- `category` — Camera / Drone / Audio (bepaalt de Project Workspace-submap)
- `confirmed_filename_patterns` — regex-patronen die tegen échte ManyFast-footage
  zijn getoetst (optioneel; bepaalt HIGH-confidence, zie hieronder)
- `filename_patterns` — nog illustratieve/niet-bevestigde regex-patronen (optioneel;
  bepaalt MEDIUM-confidence)
- `metadata_match` — te zoeken waarden in ffprobe `make`/`model`/brand/
  containerformaat-tags (optioneel)
- Voor **Audio**: `audio_only` (alleen-audiostream-check) i.p.v. bestandsnaam/metadata,
  want dat is feitelijk vast te stellen, niet te gokken.

**Prioriteit en confidence** (bijgewerkt na validatie tegen echte footage — zie
`docs/MANY_INGEST_CAMERA_PROFILES_V2_PROPOSAL.md` voor de volledige aanleiding):
generiek getierd naar betrouwbaarheid van het *signaal*, niet per camera:
- **HIGH** — exacte make/model-metadata-match (of, voor Audio, echte
  streamanalyse), een `confirmed_filename_patterns`-match, **of** een bevestigde
  `container_contains`-match (bijv. MXF vs. XAVC-MP4 als bevestigde
  ManyFast-workflowconventie — zie de kanttekening hierover in
  `camera_profiles.yaml`: dit is een workflow-conventie, geen onveranderlijk
  hardware-feit).
- **MEDIUM** — een brand-match (major_brand/compatible_brands, bijv. Sony's
  "XAVC"), **of** een generieke, nog niet-bevestigde `filename_patterns`-match.
- **LOW ("Onbekend")** — niets matcht, of meerdere profielen matchen op hetzelfde
  niveau. Een conflict lost nooit stilzwijgend op naar een zwakker niveau — nooit
  stilzwijgend fout classificeren.
- Elk asset krijgt in de ManyFast Asset Schema drie velden: `category` (voor de
  mapstructuur), `camera_profile` (het specifieke profiel, bijv. "Sony FX6") en
  `confidence`.
- Een editor kan een classificatie later altijd corrigeren. Het systeem suggereert, het
  beslist niet definitief (VISION.md: *AI as an Assistant*, ook al is dit geen AI).

**Uitbreidbaarheid:** een nieuwe camera (bijv. een 7e profiel) toevoegen = een nieuwe
entry in `camera_profiles.yaml`. Geen codewijziging, geen redeploy van logica.

---

## 5. Welke stappen de software uitvoert

1. **Trigger** — gebruiker start handmatig:
   `many-ingest run --source /pad/naar/inputmap --client "Nike" --project "Zomer Campagne" [--dry-run]`
2. **Scan** — doorzoek de inputmap naar bestanden, negeer systeembestanden.
3. **Bestandstype herkennen** — extensie + lichte ffprobe-sniff (video/audio/onbekend).
   Onbekende bestanden worden overgeslagen én gelogd, niet de hele run laten falen.
4. **Metadata-extractie** — codec, resolutie, framerate, duur, opnamedatum,
   apparaat-tags indien aanwezig. Pure technische extractie, geen AI.
5. **Camera-/bronherkenning** — camera-profielen toepassen (sectie 4): `category`,
   `camera_profile`, `confidence` bepalen.
6. **Project Workspace bepalen** — o.b.v. config + klant/project + opnamedatum +
   `category`, bijv.:
   `/Opslag/Klanten/Nike/ZomerCampagne/2026-08-03_Raw/Drone/DJI_0001.MP4`
7. **Duplicaatcontrole** — checksum vergelijken met de ManyFast Asset Schema.
8. **Dry-run-afsplitsing:**
   - **Als `--dry-run` actief is:** stop hier per bestand. Voeg een preview-regel toe
     aan het rapport en het logbestand (voorgestelde bestemming, classificatie,
     duplicaat ja/nee) — **geen kopie, geen schema-wijziging.** Ga naar het volgende
     bestand.
   - **Anders:** ga door met stap 9.
9. **Kopiëren** — bestand wordt gekopieerd naar de Project Workspace. Bron blijft altijd
   onaangeroerd (geen move in v0.1).
10. **Verificatie** — checksum van de kopie vergelijken met het origineel. Bij een
    mismatch: fout loggen, bestand markeren als mislukt, doorgaan met de rest.
11. **ManyFast Asset Schema bijwerken** — bestand, bestemming, checksum, metadata,
    `category`/`camera_profile`/`confidence`, tijdstip. (Alleen bij een echte run, niet
    bij dry-run.)
12. **Logbestand schrijven** — elke actie hierboven als JSON-lines-regel, gemarkeerd als
    `dry_run: true/false`.
13. **Rapportage** — overzicht: aantal bestanden, per camera-profiel/categorie, totale
    omvang, duplicaten, fouten. Bij dry-run: expliciet "dit is een preview, er is niets
    gewijzigd" plus het exacte vervolgcommando zonder `--dry-run`.

---

## 6. Twee soorten output: ManyFast Asset Schema vs. actielogboek

- **ManyFast Asset Schema (JSON-bestand)** — de actuele staat: welk asset zit waar, met
  welke checksum, categorie, camera-profiel en confidence. Wordt alleen bijgewerkt bij
  een echte run, nooit bij dry-run.
- **Actielogboek (JSON-lines-bestand per run)** — chronologisch verslag van wat er
  tijdens déze run is gebeurd of, bij dry-run, zou gebeuren. Elke regel is gemarkeerd
  met `dry_run: true/false` zodat achteraf nooit verwarring ontstaat over wat écht is
  uitgevoerd.

---

## 7. Wat we bewust NIET bouwen in v0.1

- **Geen move/verplaats-functionaliteit.** v0.1 kopieert alleen; bronbestanden worden
  nooit aangeraakt of verwijderd. Move volgt in een latere versie, gebouwd op dezelfde
  `Storage`-interface (die dan een `move`- of `delete_verified`-methode erbij krijgt —
  een uitbreiding, geen herontwerp).
- **Geen SQLite/Postgres in v0.1.** JSON is bewust tijdelijk en simpel; migratie is een
  losse, latere adapter-vervanging (sectie 2 en 3).
- **Geen achtergrond-daemon/continue file-watcher.** Bewust getriggerde actie na een
  shoot.
- **Geen Asset Intelligence** (transcriptie, tagging, inhoudelijke AI-analyse).
  Camera-/bronherkenning in v0.1 is regelgebaseerd, geen AI.
- **Geen cloud-opslag of sync.** Volledig lokaal.
- **Geen multi-user of rechtensysteem.**
- **Geen webinterface of dashboard.** Puur command-line.
- **Geen koppeling met een centrale ManyOS Core-database.**
- **Geen ondersteuning voor exotische/proprietary camera-formaten** buiten de gangbare
  (mp4/mov/mxf/wav/mp3) en de profielen uit sectie 4.
- **Geen automatische klant/project-herkenning.** Klant en project geeft de gebruiker
  expliciet mee bij het starten van een run.

---

## 8. Hoe we dit lokaal op een Mac laten draaien

- **Vereisten:** Python 3.11+ en ffmpeg/ffprobe via Homebrew
  (`brew install python ffmpeg`).
- **Installatie:** lokaal Python-project (bijv. via `pipx install` of een virtualenv) —
  geen server, geen achtergrondproces.
- **Gebruik na een shoot:**
  1. Inputmap aankoppelen (SD-kaart/externe schijf via Finder, of een lokale map).
  2. Eerst een preview: `many-ingest run --source /Volumes/SD_CARD_1 --client "Nike" --project "Zomer Campagne" --dry-run`.
  3. Als de preview klopt, dezelfde opdracht zonder `--dry-run` om daadwerkelijk te
     kopiëren.
- **Configuratie:** lokaal, bijv. `~/.many-ingest/config.yaml` en
  `~/.many-ingest/camera_profiles.yaml`.
- **ManyFast Asset Schema:** lokaal JSON-bestand, bijv.
  `~/.many-ingest/asset_schema.json`.
- **Actielogs:** lokaal weggeschreven, bijv. `~/.many-ingest/logs/`.
- **Geen netwerkafhankelijkheid.** Werkt volledig offline.

---

## 9. Hoe dit aansluit op de eisen

| Eis | Hoe dit plan eraan voldoet |
|---|---|
| **Bouw modulair** | Ports & adapters (sectie 2): storage, schema-opslag en camera-herkenning zijn los vervangbare onderdelen; camera-profielen zitten in config, niet in code. |
| **Schrijf begrijpelijke code** | Eén verantwoordelijkheid per module; dry-run als parameter op hetzelfde codepad i.p.v. een aparte implementatie (sectie 2); regelgebaseerde classificatie i.p.v. een ondoorzichtig model. |
| **Documenteer keuzes** | Dit document, plus `MANY_INGEST_CLOUD_READY_ARCHITECTURE.md`. Code-commentaar blijft beperkt tot niet-vanzelfsprekende beslissingen. |
| **Maak nog geen UI** | Alleen CLI (`cli.py`); geen web/GUI. |
| **Houd rekening met cloud-migratie** | `Storage`/`Manifest`-interfaces blijven ongewijzigd; JSON → SQLite/Postgres en copy-only → copy+move zijn allebei adapter-/interface-uitbreidingen, geen herontwerp. |

---

## 10. Bouwvolgorde

Bottom-up: eerst de contracten en de simpelste stukjes die niets anders nodig hebben,
pas daarna de orchestratie en de CLI die alles samenbrengt. Elke stap is los testbaar
voordat de volgende begint.

1. **`ports/storage.py` en `ports/manifest.py`** — de interfaces vastleggen, vóór er
   iets achter zit.
2. **`adapters/local_fs_storage.py` en `adapters/json_manifest.py`** — de eenvoudigste
   implementaties van die interfaces.
3. **`metadata_extractor.py`** — ffprobe-wrapper; hangt van niets anders in dit project
   af.
4. **`classification/file_types.py` en `classification/camera_profiles.py`** +
   `config/camera_profiles.yaml` — classificatielogica, apart testbaar met
   voorbeeldbestandsnamen/-metadata, los van de rest van de pijplijn.
5. **`core/ingest_service.py`** — de orchestratie (scan → classify → metadata →
   workspace-pad → dedupe → dry-run-afsplitsing → copy → verify → schema bijwerken →
   rapport). Bouwt op alles hierboven; dit is waar dry-run als parameter wordt
   ingebouwd, niet als losse implementatie.
6. **`logger.py`** — structured JSON-lines logging, ingehaakt in `ingest_service.py`.
7. **`config.py` en `cli.py`** — de composition root en de dunne CLI-laag, als laatste,
   omdat dit alles hierboven samenbrengt.
8. **Tests doorlopend, niet achteraf** — unit tests per module zodra hij bestaat, plus
   één end-to-end test met een voorbeeld-inputmap aan het eind van stap 7.

---

## Openstaand vóór de bouw

1. Akkoord op deze scope, met name sectie 4 (camera-profielen) en sectie 7 (wat niet).
2. Eerste versie van `camera_profiles.yaml` voor de 6 genoemde profielen (Sony FX6,
   Sony A7IV, Sony FX3, DJI, GoPro, Audio) — exacte bestandsnaam-patronen en
   metadata-signatures moeten met echte testbestanden van ManyFast geverifieerd worden.
3. Bevestigen: opslaglocatie (welke externe schijf/NAS) en gewenste
   Project Workspace-naamgeving.
4. Lijst van te ondersteunen bestandsformaten uit de praktijk.
5. Pas dan: eerste code voor `many_ingest/`.
