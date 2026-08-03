# Many Ingest — Technisch Bouwplan (v0.1)

**Status:** Bouwplan — nog geen code
**Rol:** CTO ManyFast
**Datum:** 2026-08-03
**Doel van de module:** automatisch ruwe videobestanden organiseren na een shoot.

Dit is het eerste module binnen ManyOS die we daadwerkelijk gaan bouwen. De scope is
bewust smal: georganiseerde, geverifieerde footage op de juiste plek — niets meer.
Transcriptie/tagging (uit het eerdere v0.1-voorstel) bouwt hier bovenop in een
vólgende stap, zodra Many Ingest bewezen betrouwbaar footage aanlevert om op te werken.

---

## 1. Welke technologieën we nodig hebben

| Behoefte | Keuze | Waarom |
|---|---|---|
| Taal | **Python 3.11+** | Sterk in file-automatisering, goede ffmpeg-integratie, makkelijk lokaal te draaien zonder server |
| Media-inspectie | **ffprobe** (onderdeel van ffmpeg) | Standaard, betrouwbaar, leest codec/resolutie/framerate/opnamedatum uit vrijwel elk camera-bestand |
| Bestandsoperaties | Python `pathlib` / `shutil` | Ingebouwd, geen extra afhankelijkheden voor iets kritisch als bestanden kopiëren |
| Integriteitscontrole | `hashlib` (SHA-256 checksums) | Garandeert dat een kopie identiek is aan het origineel — cruciaal, footage is onvervangbaar |
| Configuratie | YAML-bestand | Leesbaar en aanpasbaar zonder code te wijzigen (naamgeving, opslaglocatie, klant/project-regels) |
| Lokale status (ManyFast Asset Schema) | **SQLite** | Eén bestand, geen server, prima voor "wat is wanneer ingest, met welke checksum" |
| CLI | `click` (of `argparse`) | Simpele command-line bediening, geen UI nodig voor v0.1 |
| Logging | Python `logging` | Nodig om te kunnen nagaan wat er tijdens een ingest is gebeurd, zeker bij fouten |

Bewust **geen** database-server, geen cloud-dienst, geen webframework — dit moet volledig
lokaal en offline kunnen draaien op de dag van een shoot.

---

## 2. Welke bestanden/modules we moeten maken

```
many_ingest/
├── cli.py           # entry point: `many-ingest run --source ... --client ... --project ...`
├── config.py        # laadt en valideert ingest_config.yaml
├── scanner.py        # vindt videobestanden op de bron (SD-kaart/schijf), filtert op extensie
├── metadata.py         # ffprobe-wrapper: codec, resolutie, fps, duur, opnamedatum
├── organizer.py         # bepaalt doelpad o.b.v. config + metadata + klant/project
├── copier.py             # veilig kopiëren + checksum-verificatie + voortgang
├── manifest.py             # schrijft/leest ingestgeschiedenis (SQLite): bestand, checksum, tijdstip, bestemming
└── logger.py                # logging-configuratie (console + logbestand)

config/
└── ingest_config.yaml        # opslag-root, naamgevingsregels, bekende klanten/projecten

tests/
├── test_scanner.py
├── test_metadata.py
└── test_organizer.py
```

Elke module heeft één verantwoordelijkheid, zodat we later makkelijk kunnen aanpassen
(bijv. andere naamgevingsregel) zonder de rest te raken.

---

## 3. Welke stappen de software uitvoert

1. **Trigger** — gebruiker start na de shoot handmatig:
   `many-ingest run --source /Volumes/SD_CARD_1 --client "Nike" --project "Zomer Campagne"`
2. **Scan** — doorzoek de bron naar videobestanden (mp4/mov/mxf e.d.), negeer
   systeembestanden.
3. **Asset Intelligence Data extraheren** — per bestand via ffprobe: codec, resolutie,
   framerate, duur, opnamedatum (uit bestandsmetadata, met bestandsdatum als terugval).
4. **ManyOS Project Workspace bepalen** — o.b.v. config + klant/project + opnamedatum
   wordt de doelmap (workspace) bepaald, bijv.:
   `/Opslag/Klanten/Nike/ZomerCampagne/2026-08-03_Raw/Kaart1/`
5. **Duplicaatcontrole** — checksum berekenen en vergelijken met de ManyFast Asset
   Schema, zodat dezelfde footage niet twee keer wordt geïmporteerd.
6. **Kopiëren** — bestanden kopiëren (nooit direct verplaatsen of van de bron
   verwijderen) naar de ManyOS Project Workspace, met voortgang gelogd.
7. **Verificatie** — checksum van de kopie vergelijken met het origineel; pas na een
   match wordt een bestand als "veilig binnengehaald" gemarkeerd.
8. **ManyFast Asset Schema bijwerken** — ingest-run vastleggen in de lokale
   SQLite-database: bestand, bestemming, checksum, Asset Intelligence Data, tijdstip.
   Dit is de eerste, ruwe vorm van een "Asset"-record en later de brug naar de ManyOS
   Core.
9. **Rapportage** — overzicht tonen: aantal bestanden, totale omvang, duur, eventuele
   fouten of overgeslagen duplicaten; logbestand wegschrijven.
10. **Bron leegmaken (optioneel, expliciet)** — pas ná succesvolle verificatie vraagt de
    tool of de bron (SD-kaart) leeggemaakt mag worden. Altijd een bevestigde, handmatige
    actie — nooit automatisch.

---

## 4. Wat we bewust NIET bouwen in v0.1

- **Geen achtergrond-daemon/continue file-watcher.** v0.1 is een bewust getriggerde
  actie na een shoot, geen altijd-actieve service die stil kan falen.
- **Geen transcriptie of AI-tagging van de inhoud.** Many Ingest levert georganiseerde
  bestanden + basale Asset Intelligence Data; inhoudelijke analyse is een volgende
  module die hierop voortbouwt.
- **Geen cloud-opslag of sync.** v0.1 draait volledig lokaal (externe SSD/NAS via
  Finder-mount). Cloud is een latere stap.
- **Geen multi-user of rechtensysteem.** Eén persoon draait dit lokaal; geen login,
  geen rollen.
- **Geen automatisch verwijderen van bronbestanden.** Bron leegmaken gebeurt nooit
  automatisch — altijd een expliciete bevestiging, om dataverlies te voorkomen.
- **Geen webinterface of dashboard.** Puur command-line voor v0.1.
- **Geen koppeling met een centrale ManyOS Core-database.** Die bestaat nog niet; de
  ManyFast Asset Schema wordt lokaal en losstaand bijgehouden. Koppeling volgt in een
  latere versie.
- **Geen ondersteuning voor exotische/proprietary camera-formaten** buiten de gangbare
  (mp4/mov/mxf). Uitbreiden is later werk, op basis van wat we in de praktijk tegenkomen.
- **Geen automatische klant/project-herkenning via AI.** v0.1 vraagt dit expliciet aan
  de gebruiker of uit eenvoudige configuratie — geen slimme herkenning.

---

## 5. Hoe we dit lokaal op een Mac laten draaien

- **Vereisten (eenmalig installeren):**
  - Python 3.11+ (via Homebrew: `brew install python`)
  - ffmpeg/ffprobe (via Homebrew: `brew install ffmpeg`)
- **Installatie van de tool:** als lokaal Python-project, bijvoorbeeld via `pipx install`
  of een virtualenv — geen server, geen achtergrondproces nodig.
- **Gebruik na een shoot:**
  1. SD-kaart/externe schijf aansluiten — macOS mount deze automatisch onder
     `/Volumes/...`.
  2. In Terminal: `many-ingest run --source /Volumes/SD_CARD_1 --client "Nike" --project "Zomer Campagne"`.
  3. Tool kopieert, verifieert en rapporteert; vraagt daarna optioneel om de kaart leeg
     te maken.
- **Configuratie:** lokaal bestand, bijv. `~/.many-ingest/config.yaml`, met opslag-root
  (bijv. een gekoppelde externe SSD of NAS-map) en naamgevingsregels.
- **ManyFast Asset Schema:** lokaal SQLite-bestand, bijv. `~/.many-ingest/manifest.db`
  — geen server.
- **Logs:** lokaal weggeschreven, bijv. `~/.many-ingest/logs/`.
- **Geen netwerkafhankelijkheid.** Alles werkt offline, wat belangrijk is op locatie
  (shoot-dag, mogelijk geen betrouwbare wifi).
- **Later, voor niet-technische teamleden:** eventueel een dubbelklikbaar
  `.command`-scriptje dat het CLI-commando met de juiste vaste parameters aanroept,
  zodat niemand Terminal-syntax hoeft te onthouden. Niet nodig voor de eerste,
  technische validatie van v0.1.

---

## Openstaand vóór de bouw

1. Akkoord op deze scope (met name punt 4 — wat we niet bouwen).
2. Bevestigen: opslaglocatie (welke externe schijf/NAS) en gewenste mapstructuur/naamgeving.
3. Lijst van te ondersteunen camera/bestandsformaten uit de praktijk.
4. Pas dan: eerste code voor `many_ingest/`.
