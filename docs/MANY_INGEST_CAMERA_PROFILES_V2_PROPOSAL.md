# Many Ingest — Analyse eerste testresultaten & voorstel camera-herkenning v2

**Status:** Analyse + voorstel — nog geen code/config aangepast
**Rol:** CTO ManyFast
**Datum:** 2026-08-03
**Betreft:** wat jullie "source_rules.yaml" noemen is inmiddels `config/camera_profiles.yaml`
(hernoemd tijdens de terminologie-opschoning — zelfde bestand, zelfde doel).

---

## 1. Analyse van de eerste testresultaten

Bevestigd op basis van de geautomatiseerde testsuite (36 tests) én een echte
dry-run tegen 64 bestanden uit een lopend ManyFast-project (Jan Rotmans):

| Conclusie | Status | Bewijs |
|---|---|---|
| Ingest-pipeline werkt | ✅ | Scan → classificeren → workspace-pad → dry-run/copy → schema → log, end-to-end getest, ook via de echte CLI. |
| Checksum werkt | ✅ | SHA-256 berekend en geverifieerd vóór registratie; getest met een geforceerde mismatch (`FAILED_VERIFICATION`-pad) zodat we weten dat een échte fout ook wordt opgemerkt, niet alleen de happy path. |
| Logging werkt | ✅ | JSON-lines actielogboek, `dry_run`-vlag klopt in beide standen, bevestigd op echte footage. |
| Audio-classificatie werkt | ✅ | Beide `.m4a`-interviews correct herkend als Audio met hoge confidence — via echte streamanalyse, dus onafhankelijk van bestandsnaam of extensie. |
| Camera-herkenning moet beter | ⚠️ | **62 van de 64** echte video-/MXF-bestanden kwamen uit op "Onbekend". |

De vorige twee analyse-rondes (docs van eerder vandaag) hebben al twee concrete
bugs gefixt (`.m4a`-extensie, `company_name`/`major_brand` als extra signalen). Dit
document gaat een stap verder: **wat is er, ná die fixes, nog steeds structureel mis
met camera-herkenning specifiek**, en wat stel ik voor.

---

## 2. Waarom camera-herkenning nog steeds zwak is — per bewijsstuk

| Bestandstype in de praktijk | Wat ffprobe teruggeeft | Waarom onze 6 profielen het nog missen |
|---|---|---|
| Sony XAVC `.MP4` (`C9666.MP4` e.d.) | `major_brand: XAVC`, **geen** make/model-tag | FX6 én FX3 gebruiken dezelfde XAVC-brand én hetzelfde `C####`-naampatroon. Zonder model-tag is dit **terecht** onbeslisbaar tussen die twee — geen bug, een echte informatiegrens. |
| Sony MXF (`611_3894.MXF` e.d.) | `company_name: Sony`, `product_name: "Mem "` | `company_name` bevestigt "Sony", maar `product_name: "Mem"` is **geen cameramodel** — vermoedelijk een opname-/geheugenmodule-aanduiding van Sony zelf. We hebben dus wél merkbevestiging, maar geen enkel signaal dat FX6 van FX3 (of van welke andere Sony-camera dan ook) onderscheidt. |
| Hernoemde exports (`Jan 1.mp4`, `Bas 1.mp4`, `CTA 1.mp4`, ...) | **Geen tags** | Dit zijn overduidelijk al bewerkte/geëxporteerde bestanden. Hier valt principieel niets meer te herkennen — geen fix lost dit op, dit hoort "Onbekend" te blijven. |
| DJI / GoPro | — | **Geen enkel echt DJI- of GoPro-bestand zat in deze testset.** Die twee profielen zijn dus nog altijd alleen op aannames gebaseerd, niet gevalideerd. |

**Kernconclusie:** het probleem zit niet in de matching-logica (die werkt aantoonbaar
correct — zie de FX6/FX3-tiebreak-tests), het zit in **wat de bestanden zelf aan
bruikbare metadata bevatten**. Voor Sony's professionele lijn (FX6/FX3) is er in de
praktijk vaak helemaal geen onderscheidend signaal aanwezig, alleen "dit is Sony".

---

## 2a. Aanvullend onderzoeksresultaat: volledige ffprobe-dump van `611_3894.MXF`

Naar aanleiding van sectie 4, vraag 1 (welke camera schoot de MXF-bestanden): een
volledige `ffprobe -show_format -show_streams` op `611_3894.MXF` (niet alleen
`-show_format` zoals eerder) leverde nieuwe, controleerbare details op:

- **8 losse mono PCM-audiokanalen** (24-bit/48kHz).
- **4K (3840×2160), H.264 "High 4:2:2 Intra", 25fps** — een broadcast/cinema
  intraframe-opnameprofiel (XAVC-I-stijl).
- Een **SMPTE-436M ANC-datatrack** (ancillary data — bevatte in dit geval geen
  extra cameranaam, wel timecode-gerelateerde info).
- Herbevestigd: `company_name: "Sony"`, `product_name: "Mem "` — nog steeds geen
  cameramodel.

**Betekenis (oorspronkelijk, inmiddels achterhaald):** hier stond eerder dat 8
audiokanalen "ongebruikelijk veel" zou zijn voor een FX6/FX3 en eerder op een
externe mixer zou wijzen. **Dat klopt niet — zie sectie 2b hieronder.**

---

## 2b. Bevestiging: `611_####.MXF` = Sony FX6, en 8 kanalen is normaal voor deze camera

Vervolgonderzoek op de externe SSD leverde het antwoord op vraag 1 uit sectie 4 op:
er bestaan **drie onafhankelijke ManyFast-projecten** (DTC, ALS/Clips Andros, Big
Buffalo) met elk een map die de crew zelf al **"FX6"** heeft genoemd. Al deze mappen
bevatten uitsluitend clips met het `611_####.MXF`-patroon.

Een volledige `ffprobe -show_format -show_streams` op zo'n bevestigde FX6-clip
(`611_3907.MXF` uit de "Fx6"-map van project DTC) laat **exact hetzelfde profiel**
zien als de Jan Rotmans-MXF's uit sectie 2a: `company_name: Sony`,
`product_name: "Mem "`, 4K H.264 High 4:2:2 Intra @ 25fps, en **8 mono
PCM-audiokanalen**.

**Correctie op de eerdere hypothese (sectie 2a):** 8 audiokanalen is dus **geen**
aanwijzing voor een externe mixer of een ander toestel — het blijkt binnen de
ManyFast-workflow juist een **consistent, herhaalbaar FX6-signaal** te zijn, bevestigd
over drie onafhankelijke projecten. De eerdere aanname dat dit "waarschijnlijk niet
rechtstreeks van de camerabody" komt, was onjuist en is hierbij ingetrokken.

**Praktisch gevolg:** de originele `611_3894.MXF`–`611_3905.MXF`-bestanden uit de
Jan Rotmans-map zijn met grote waarschijnlijkheid eveneens FX6-footage — zelfde
patroon, zelfde technische signatuur. Het `611_####.MXF`-bestandsnaam-patroon is
hiermee, in tegenstelling tot de andere patronen in `camera_profiles.yaml`, niet
langer illustratief maar **bewijs-onderbouwd**. Toegevoegd aan het profiel — zie
sectie 3c.

---

## 3. Wat ik daadwerkelijk voorstel

### 3a. Nu al toepasbaar — geen codewijziging nodig — **toegepast**

Kleine, evidence-based aanvullingen op de bestaande `camera_profiles.yaml`, binnen de
huidige matching-logica:

```yaml
# Toevoeging aan het bestaande "audio"-profiel:
  - id: audio
    label: "Audio"
    category: Audio
    audio_only: true
    filename_patterns:
      - ".*\\.(wav|mp3|m4a|aif|aiff)$"   # .aif/.aiff toegevoegd: gangbaar bij
                                          # professionele audiorecorders (Zoom,
                                          # Sound Devices) — nog niet met echte
                                          # bestanden getest, wel een voor de hand
                                          # liggende aanvulling gezien de categorie
                                          # "audio recorders" die jullie noemen.
```

Verder blijven A7IV/FX3/DJI/GoPro qua matching-regels ongewijzigd — er is geen extra
bestandsnaam- of metadata-signaal dat we voor die vier vandaag eerlijk kunnen
toevoegen zonder te gaan gokken. Sony FX6 is de uitzondering — zie 3c.

### 3b. Voorgestelde uitbreiding die WEL een (kleine) logica-aanpassing vereist

Dit is de belangrijkste verbetering, maar hoort thuis in `classify()`, niet in de
YAML alleen — vandaar apart genoemd, **niet nu al doorvoeren**:

Voeg een **generiek "Sony — model onbekend"-niveau** toe, ónder de bestaande
model-specifieke matches maar bóven bestandsnaam-matching:

```yaml
  - id: sony_unidentified
    label: "Sony (model onbekend)"
    category: Camera
    metadata_match:
      make_contains:
        - "Sony"
```

**Waarom dit niet zomaar in de huidige YAML kan:** de huidige matching-tiers zijn
make/model (hoogste) → brand → bestandsnaam. Een generiek `make_contains: ["Sony"]`-
profiel zou op hetzelfde niveau meedoen als de specifieke FX6/FX3/A7IV-profielen.
Bij een bestand waar wél een model-tag aanwezig is (bijv. `ILME-FX6`), zou dit
generieke profiel dan ook meematchen (via make) naast het specifieke FX6-profiel
(via model) — precies dezelfde soort vals-ambigue situatie als de eerdere
brand-bug. Dit vereist dus een vierde matching-tier ("merk bekend, model
onbekend") die expliciet ná specifieke model-matches komt, niet ernaast.

**Waarde hiervan:** een MXF-bestand van Sony wordt dan `Camera / "Sony (model
onbekend)" / confidence middel` in plaats van gewoon `Onbekend` — bruikbaarder voor
een editor bij het uitzoeken, zonder dat we doen alsof we het specifieke model
weten. Dit is precies het "systeem suggereert, gokt niet"-principe, maar dan met een
extra tussenstap i.p.v. alleen ja/nee.

### 3c. `611_####.MXF` als bewijs-onderbouwd FX6-patroon — **toegepast**

Op basis van sectie 2b is `^611_\\d{4}\\.MXF$` toegevoegd aan het bestaande
`sony_fx6`-profiel in `camera_profiles.yaml`. Alleen dit profiel is aangepast;
A7IV/FX3/DJI/GoPro/Audio zijn qua matching-regels ongewijzigd.

Bij het toevoegen bleek de matching-logica in `classify()` geen onderscheid te
maken tussen dit bewijs-onderbouwde patroon en de andere, nog illustratieve
bestandsnaam-patronen — beide kregen altijd `Confidence.MEDIUM`. Dat is inmiddels
opgelost, zie 3d.

### 3d. Confidence-tier-uitbreiding — **toegepast**

`classify()` maakt nu generiek onderscheid tussen twee soorten bestandsnaam-signalen,
i.p.v. ze allebei hetzelfde te behandelen:

- **HIGH**: exacte make/model-metadata-match (of, voor Audio, echte streamanalyse),
  **of** een `confirmed_filename_patterns`-match — bestandsnaam-patronen die tegen
  échte footage zijn getoetst.
- **MEDIUM**: een brand-match (major_brand/compatible_brands), **of** een
  `filename_patterns`-match — nog illustratieve, niet-bevestigde patronen.
- **LOW ("Onbekend")**: niets matcht, of meerdere profielen matchen op hetzelfde
  niveau — een conflict lost nooit stilzwijgend op naar een zwakker niveau.

Dit is een **datakeuze, geen codekeuze**: een patroon in `confirmed_filename_patterns`
zetten in plaats van `filename_patterns` is een YAML-wijziging. Er is geen
FX6-specifieke uitzondering in `classify()` — het `sony_fx6`-profiel is vooralsnog
gewoon het enige profiel met een ingevulde `confirmed_filename_patterns`-lijst.

Resultaat op de echte Jan Rotmans-footage: alle `611_####.MXF`-bestanden gaan nu naar
`Camera / "Sony FX6" / confidence: hoog` (was: `Onbekend / laag`).

---

## 4. Wat ik nodig heb van ManyFast om dit verder te verbeteren

Dit zijn geen dingen die ik kan raden vanaf de bureaustoel — dit vereist iemand die
het echte materiaal en de apparatuur kent:

1. ~~Welke fysieke camera schoot `611_3894.MXF` e.d.?~~ **Beantwoord voor de
   `611_####.MXF`-bestanden, zie sectie 2b: bevestigd FX6 over drie onafhankelijke
   projecten.** Nog open: `C9666.MP4` e.d. (Sony XAVC MP4, `C####`-patroon) — dat
   patroon deelt FX6 en FX3 nog steeds, dus welke camera dát schoot staat nog niet vast.
2. **Een paar echte DJI- en GoPro-bestanden** (of gewoon de bestandsnamen +
   `ffprobe -show_format`-output, geen beeldmateriaal nodig) om de twee volledig
   ongeteste profielen eindelijk te valideren.
3. Bevestiging of de "Mem"-aanduiding in Sony MXF-bestanden inderdaad geen
   cameramodel is, of dat we iets missen in hoe we die tag interpreteren.

---

## 5. Checklist: welke signalen we per camera nodig hebben

Voor elk toestel geldt dezelfde basisvraag: **welke bestandsnaam- en
metadata-signalen zijn uniek genoeg om dit toestel betrouwbaar te onderscheiden van
de andere vijf?** Onderstaande checklist is wat we daarvoor nodig hebben — bij
voorkeur van een ongewijzigd, origineel bestand (geen export/hernoemde kopie).

### Sony FX6
- [ ] Eén originele clip (ongewijzigde bestandsnaam, rechtstreeks van de camera)
- [ ] Volledige `ffprobe -show_format -show_streams`-output van die clip
- [ ] Welk opnameformat wordt in de praktijk gebruikt (XAVC-I MP4? MXF? extern via
      ProRes-recorder?)
- [ ] Aantal audiokanalen dat déze crew-opstelling gebruikt (2? 4? meer via
      XLR-handle-unit?) — relevant na de 8-kanaals bevinding in 2a
- [ ] Het bestandsnaam-patroon zoals de camera het zelf genereert

### Sony A7IV
- [ ] Eén originele clip (ongewijzigde bestandsnaam)
- [ ] Volledige `ffprobe`-output — met name of `model`/`make` hier wél gevuld is
      (mirrorless Sony-camera's schrijven dit vaker dan de professionele lijn)
- [ ] Opnameformat in de praktijk (XAVC S? XAVC HS?)
- [ ] Bestandsnaam-patroon (huidige aanname `DSC####` nog niet bevestigd)

### DJI drones
- [ ] Welk specifiek DJI-model (Mavic 3, Air, Inspire, Mini, ...) — kan
      naamgeving/tags beïnvloeden
- [ ] Eén originele clip + volledige `ffprobe`-output
- [ ] Bevestiging bestandsnaam-patroon (huidige aanname `DJI_####.MP4` nog nooit
      tegen echt materiaal getest)

### GoPro
- [ ] Welk specifiek GoPro-model (Hero 10/11/12, ...)
- [ ] Eén originele clip + volledige `ffprobe`-output
- [ ] Bevestiging bestandsnaam-patroon (huidige aanname `GH*`/`GOPR*`/`GX*` nog
      nooit tegen echt materiaal getest)

### Audio recorders
- [ ] Welk(e) merk/model wordt daadwerkelijk gebruikt (Zoom, Sound Devices, Tascam,
      Rode, ...) — bepaalt zowel bestandsextensie als naamgevingsconventie
- [ ] Eén origineel bestand + volledige `ffprobe`-output
- [ ] Bestandsnaam-patroon van het toestel zelf

### Sony FX3
- [x] Eén originele clip (ongewijzigde bestandsnaam) — bevestigd via 3 crew-gelabelde
      "FX3"-mappen (sectie 7)
- [x] Volledige `ffprobe`-output — zie sectie 7
- [x] Opnameformat in de praktijk — **XAVC-MP4, consistent, geen MXF gezien**
- [ ] Aantal audiokanalen dat déze crew doorgaans gebruikt op de FX3 — nog niet apart
      onderzocht (niet nodig geweest: containerformaat bleek al voldoende)
- [x] Bestandsnaam-patroon — bevestigd: deelt inderdaad `C####` met de FX6, zoals
      aangenomen; daarom lost bestandsnaam dit niet op, containerformaat wel
      (sectie 7-8)

Zodra dit binnen is per toestel, kan sectie 3b (of een verdere verfijning daarvan)
op bewijs in plaats van aannames gebouwd worden.

---

## 6. Analyse: category vs. camera_profile na de eerste echte ingest

**Bevinding uit de praktijk:** Sony FX6 wordt correct herkend, maar `category`
blijft "Camera"; Sony FX3 komt nog steeds uit op "Onbekend".

**Analyse van de huidige classificatielogica:** dit eerste deel is **geen bug** —
het is precies hoe het schema is ontworpen, en dat ontwerp is nog steeds juist:

- `category` (Camera/Drone/Audio) bepaalt uitsluitend de Project Workspace-submap.
  Dit is bewust **generiek** gehouden (zie `MANY_INGEST_BUILD_PLAN.md`, sectie 6) —
  er komt geen aparte submap per cameramodel, dat zou de mapstructuur onnodig
  versnipperen voor iets dat de bestandsnaam/schema al vastlegt.
- `camera_profile` (bijv. "Sony FX6") bevat het specifieke apparaat, apart
  vastgelegd in de ManyFast Asset Schema, ook al bepaalt het geen eigen map.

Met andere woorden: **doel 1 en 2 uit deze vraag zijn al vervuld door het bestaande
ontwerp** — `category` is en blijft generiek, `camera_profile` draagt al het
specifieke apparaat. Dat FX6 "goed" voelt en FX3 "fout" is geen verschil in hóe het
systeem werkt, maar puur een verschil in **welk signaal beschikbaar is**: FX6 heeft
sinds 3c/3d een bevestigd signaal (`611_####.MXF`), FX3 nog niet. Sectie 7
hieronder onderzoekt of we daar nu wél een betrouwbaar signaal voor hebben.

---

## 7. Nieuwe bevinding: containerformaat (MXF vs. MP4) als FX3-signaal

Onderzoek op de externe SSD naar crew-gelabelde "FX3"-mappen (dezelfde methode als
bij FX6 in sectie 2b) leverde drie onafhankelijke, door de crew zelf "FX3" genoemde
mappen op, in drie verschillende projecten — waaronder **hetzelfde DTC-project**
dat ook de bevestigde FX6-map bevat:

| Map (crew-gelabeld) | Bestanden |
|---|---|
| `DTC/Video/Fx6` | 48× `611_####.MXF` |
| `DTC/Video/Fx3` | 85× `C####.MP4` |
| `Hype/FX3` | 22× `C####.MP4` |
| `VdPanne/Fx3` | 25× `C####.MP4` |
| (plus 5 andere FX6-mappen, zie sectie 2b) | uitsluitend `.MXF` |

**Over alle 9 gecontroleerde mappen, in 3 onafhankelijke projecten: FX6-mappen
bevatten uitsluitend `.MXF`, FX3-mappen uitsluitend `.MP4` — geen enkele
uitzondering.** Een volledige `ffprobe`-dump van een bevestigde FX3-clip
(`C0690.MP4` uit `DTC/Video/Fx3`) bevestigt: `major_brand: XAVC`,
`compatible_brands: XAVCmp42iso6` — **identiek** aan de FX6 XAVC-MP4-bevinding uit
sectie 2, en nog steeds **geen** make/model-tag.

**Wat dit wél en niet bewijst:**
- Het bevestigt **niet** iets nieuws over de camera's zelf — FX6 en FX3 zijn beide
  technisch prima in staat om zowel MXF als XAVC-MP4 op te nemen. Dit is een
  camera-menu-instelling (opnameformaat), geen vaste hardware-eigenschap.
- Het bevestigt wél, empirisch en zonder uitzondering, dat **binnen ManyFast's
  huidige workflow** de FX6 kennelijk altijd op MXF staat ingesteld en de FX3
  altijd op XAVC-MP4 — een consistente operationele conventie, net als bij de
  `611_####`-naamgeving.
- **Risico, expliciet benoemd:** dit is dus een iets ander soort bewijs dan
  `company_name`/`model_contains` (onveranderlijke hardware-feiten). Het is een
  workflow-conventie die in theorie kan veranderen als iemand ooit een
  camera-instelling wijzigt. Zolang de conventie standhoudt is het signaal
  betrouwbaar; als de conventie ooit doorbroken wordt, misclassificeert dit
  signaal in plaats van "Onbekend" te tonen.
- **Nog te bevestigen:** of Sony A7IV ook XAVC-MP4 met een vergelijkbaar
  `C####`-patroon gebruikt (staat al op de checklist in sectie 5). Zo ja, dan moet
  het containerformaat-signaal ook tegen A7IV getoetst worden vóór het als
  onderscheidend voor specifiek FX3 wordt vastgelegd.

---

## 8. Voorstel: containerformaat als generiek metadata-signaal — **toegepast**

Voeg **containerformaat** (ffprobe `format.format_name`, bijv. `"mxf"` versus
`"mov,mp4,m4a,3gp,3g2,mj2"`) toe als nieuw, generiek metadata-veld — net zo generiek
als `make`/`model`/`brand_contains` nu al zijn, geen FX6/FX3-specifieke code:

- `ProbeResult` krijgt een `container_format`-veld (rechtstreeks uit ffprobe).
- `CameraProfile`/`camera_profiles.yaml` krijgt een optioneel
  `metadata_match.container_contains` (net als `make_contains`/`brand_contains`).
- `sony_fx3` krijgt `container_contains: ["mov,mp4"]` (of specifieker) — dit is het
  stuk dat het "Onbekend"-probleem voor FX3 daadwerkelijk oplost.
- `sony_fx6` kan optioneel `container_contains: ["mxf"]` erbij krijgen — een tweede,
  onafhankelijke bevestiging naast de al bestaande `611_####.MXF`-match, niet
  strikt nodig maar wel consistent.

**Voorgestelde confidence-tier:** HIGH, net als `confirmed_filename_patterns` —
dit is qua bewijskracht (crew-conventie, 100% consistent over 9 mappen) gelijkwaardig
aan hoe de `611_####`-match tot stand kwam, dus verdient dezelfde behandeling. De
kanttekening uit sectie 7 (workflow-conventie i.p.v. hardware-feit) staat dan als
commentaar in de YAML, net zoals dat nu al bij `611_####.MXF` gebeurt.

**Akkoord ontvangen en geïmplementeerd** (`ProbeResult.container_format`,
`CameraProfile.metadata_container_contains`, `_matches_container()` in de HIGH-tier
van `classify()`). `sony_fx3` kreeg `container_contains: ["mp4"]`, `sony_fx6` kreeg
`container_contains: ["mxf"]` als tweede, onafhankelijke bevestiging. De
A7IV-onzekerheid uit sectie 7 blijft een open risico — nog niet weggenomen, alleen
bewust geaccepteerd voor dit besluit (zie sectie 5-checklist).

**Bevestigd op echte Jan Rotmans-footage:** `C9666.MP4` t/m `C9677.MP4` gaan nu naar
`Camera / "Sony FX3" / confidence: hoog` (was: `Onbekend`). Alle `611_####.MXF`
blijven `Camera / "Sony FX6" / confidence: hoog`. 40 tests slagen, inclusief 3
nieuwe die specifiek dit scenario en het "container onbekend, blijft ambigu"-randgeval
dekken.

---

## Openstaand

1. ~~Akkoord op 3a (audio-extensies)~~ — **toegepast.**
2. ~~Confidence-tier-uitbreiding (was hier "3b" genoemd)~~ — **toegepast, zie 3d.**
3. **Nog open: 3b, het generieke "Sony (model onbekend)"-fallback-niveau** — een
   ánder idee dan 3d: een vierde/vijfde tier die een Sony-bestand zonder specifieke
   model-match toch als "Camera / Sony (model onbekend)" i.p.v. "Onbekend"
   classificeert. Dit staat los van de nu doorgevoerde confidence-tiers en vereist
   nog steeds een aparte, expliciete `classify()`-uitbreiding.
4. ~~Akkoord op sectie 8 (containerformaat)~~ — **toegepast.**
5. Antwoord op de drie vragen in sectie 4 en de checklist in sectie 5 (met name
   A7IV nog volledig open), zodat verdere uitbreidingen op echte data in plaats van
   aannames gebaseerd kunnen worden.
5. **Nieuw: akkoord op sectie 8** (containerformaat als generiek HIGH-signaal,
   lost het FX3-"Onbekend"-probleem op) — inclusief bewust akkoord op het
   workflow-conventie-risico uit sectie 7, vóór implementatie.
