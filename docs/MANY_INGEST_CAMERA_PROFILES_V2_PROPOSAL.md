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
- [ ] Eén originele clip (ongewijzigde bestandsnaam)
- [ ] Volledige `ffprobe`-output
- [ ] Opnameformat in de praktijk
- [ ] Aantal audiokanalen dat déze crew doorgaans gebruikt op de FX3 — als dit
      structureel lager is dan bij de FX6-opstelling, is kanaalaantal mogelijk zelf
      een bruikbaar onderscheidend signaal tussen de twee
- [ ] Bestandsnaam-patroon (huidige aanname deelt `C####` met de FX6 — bevestigen of
      dat ook in de praktijk zo is)

Zodra dit binnen is per toestel, kan sectie 3b (of een verdere verfijning daarvan)
op bewijs in plaats van aannames gebouwd worden.

---

## Openstaand

1. ~~Akkoord op 3a (audio-extensies)~~ — **toegepast.**
2. ~~Confidence-tier-uitbreiding (was hier "3b" genoemd)~~ — **toegepast, zie 3d.**
3. **Nog open: 3b, het generieke "Sony (model onbekend)"-fallback-niveau** — een
   ánder idee dan 3d: een vierde/vijfde tier die een Sony-bestand zonder specifieke
   model-match toch als "Camera / Sony (model onbekend)" i.p.v. "Onbekend"
   classificeert. Dit staat los van de nu doorgevoerde confidence-tiers en vereist
   nog steeds een aparte, expliciete `classify()`-uitbreiding.
4. Antwoord op de drie vragen in sectie 4 en de checklist in sectie 5, zodat
   FX6/FX3/DJI/GoPro verder op echte data in plaats van aannames gebaseerd kunnen
   worden.
