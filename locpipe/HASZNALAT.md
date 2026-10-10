# LocPipe — Használati útmutató & Munkafolyamat

Ez a leírás a GameStringer és LocPipe rendszer napi használatát mutatja be gyakorlati lépésekkel és konkrét parancsokkal. A rendszer felépítésének elméleti leírása a `README.md`-ben található — ez a dokumentum a gyakorlati "hogyan használd" útmutató magyar nyelven.

---

## 1. Telepítés & Előkészületek

A rendszer a Google Antigravity CLI (`agy`) motorját használja a Gemini 3.8 Flash modellel. Nincs szükség külön API kulcsok kezelésére, amennyiben a gépén már be van jelentkezve az Antigravity CLI-be.

```bash
# Repository gyökerében vagy a locpipe mappában:
pip install -e .
```

Ellenőrizd az Antigravity CLI állapotát:
```bash
agy --version
```

A grafikus kezelőfelület (GUI) indítása:
```bash
gamestringer-gui
# vagy:
python -m gamestringer.desktop_gui.app
```

---

## 2. Projekt típusok: Játék vs. Szoftver Lokalizáció

A rendszer támogatja a szórakoztatóipari játékok és a professzionális asztali/webes szoftverek lokalizációját (`project_type: game | software`).

### A) Játék Mód (`project_type: game`)
- **Fókusz:** Élő dialógusok, karakterhangok, narratíva, gamer szleng és magával ragadó játékélmény.
- **Karakterhangok:** A `resources/character-voices.md` segítségével minden szereplőhöz külön hangnem, egyedi megszólítás és szószedet köthető.
- **Alapértelmezett kategóriák:** `dialogue` (magasabb minőségi effort, kontextusfüggő fordítás), `ui` (tömör menüfeliratok).

### B) Szoftver Mód (`project_type: software`)
- **Fókusz:** Menüpontok, funkciógombok, rendszerüzenetek, dokumentáció és technikai pontosság.
- **UI Akciók és Felszólítások:** Gomboknál és parancsoknál egységes főnévi igenevek vagy cselekvő formák (pl. *Megnyitás*, *Mentés*, *Mégse*).
- **Gyorsbillentyűk és Menü Hozzáférési Kulcsok (Access Keys):** Automatikusan védi és érvényesíti az accelerator billentyűket (`&File` -> `&Fájl`, `Save &As...` -> `Mentés má&sként...`) és a fizikai billentyűkombinációkat (`Ctrl+S`, `Ctrl+Shift+P`, `Alt+F4`).
- **Alapértelmezett kategóriák:** `action` (gombok és parancsok szigorú karakterkorláttal), `menu` (hierarchikus menüsorok), `dialog` (párbeszédpanelek, megerősítő kérdések), `ui` (általános felületi elemek).

---

## 3. Többnyelvűség & Keresztfordítás (Multilingual Support)

A LocPipe teljes körűen támogatja a többnyelvű fordítást bármely forrás- és célnyelv között:
- **Támogatott nyelvek:** `ja` (japán), `en` (angol), `hu` (magyar), `de`, `fr`, `es`, `zh`, `ko`, `it`, `pl`, `pt`, `ru` stb.
- **Kétirányú fordítás:** `en -> hu`, `ja -> hu`, `ja -> en`, `hu -> en` stb.
- **Nyelvspecifikus Regiszter (Formality):**
  - **Magyar (`hu`):** `informal` (közvetlen tegezés: *Gyere ide!*) vs `formal` (udvarias magázás: *Kérem, jöjjön ide!*).
  - **Japán (`ja`):** `informal` (普通体 / タメ口 - da/dearu) vs `formal` (丁寧語 / 敬語 - desu/masu).
  - **Angol (`en`):** `informal` (természetes, beszélt / gamer fordulatok) vs `formal` (hivatalos, professzionális).
- **Japán Idézőjelek & Írásjelek:** A japán sarokzárójelek (`「...」`, `『...』`) automatikusan felismerésre kerülnek és hibátlanul illeszkednek a latin idézőjelekhez (`"..."`, `„...”`).
- **Szkript-tudatos Expanzió:** Japán forrásszöveg (`ja`) esetén a tömör kandzsi írásjelek miatt automatikusan megengedett a magasabb expanziós arány (akár 3.5x hosszabbodás) fals büntetés nélkül.
- **Nyelvhelyesség:** A magyar helyesírás-ellenőrzés csak akkor fut, ha a célnyelv magyar (`target_lang: hu`), így más célnyelvek esetén nem okoz félreértést.

---

## 4. Játékipari Szabályok, Vezérlőkódok & Karakterkorlátok

A LocPipe determinisztikus ellenőrző és javító motorja megvédi a játékok belső formátumait:
- **RPG Maker & Visual Novel Kódok:** Automatikusan érintetlenül maradnak: `\C[#]` (színkód), `\V[#]` (változó), `\N[#]` (név), `\I[#]` (ikon), `\G` (arany), `\.`, `\|`, `\!`, `\^`, `\>`, `\<`, `\{`, `\}`.
- **Escape Szekvenciák:** Szigorúan védett újsorok és tabulátorok: `\n`, `\r`, `\t`, `\\`.
- **Furigana / Ruby Jelölések:** HTML és BBCode jellegű olvasási segédletek megőrzése: `<ruby>`, `</ruby>`, `<rt>`, `<rp>`, `[ruby]`.
- **Intelligens Gender Slotok:** `{ms|...}{fs|...}` tokeneknél a rendszer megvédi a slot szerkezetet, miközben engedélyezi a belső szöveg szabad magyarítását.
- **Multiplicitás Ellenőrzés:** Minden format tokenből (pl. `{0}`, `%s`, `<color>`) pontosan ugyanannyi darabnak kell szerepelnie a célban, mint a forrásban.
- **Fizikai Hosszkorlát (`max_length`):** Ha a motor mezőhossz-korlátot definiál (pl. `default_max_length: 35`), a rendszer `MAX_LENGTH_EXCEEDED` hibát jelez és a Tier 1 mechanikus javítómotorral automatikusan tömöríti a szöveget.

---

## 5. Az Erőforrás-fájlok szerepe (resources/)

Egy projekt `resources/` mappájában kulcsfontosságú Markdown fájlok találhatók:

| Fájl | Mi ez? | Hogyan kell kitölteni? |
|---|---|---|
| **`anti-fabrication-checklist.md`** | Hallucináció- és torzításgátló szabályok az LLM számára. | **Automatikus:** a projekt létrehozásakor készen létrejön, nem kell módosítani. |
| **`lang-style.md`** | Stílusútmutató (hangvétel, tegezés/magázás, szórend, szleng). | **Preset vagy Kézi:** A GUI-ban vagy CLI-ben választható előre beépített stíluspresetekből. |
| **`glossary.md`** | Kötött terminológia (tulajdonnevek, skillek, tárgyak, UI gombok). | **Bootstrap vagy Kézi:** Tesztfutás után a `bootstrap-resources` automatikusan kigyűjti a TM-ből. |
| **`character-voices.md`** | Szereplők egyedi hangneme (játék mód esetén). | **Bootstrap vagy Kézi:** Automatikusan generálható meglévő TM-ből beszélő metaadatok alapján. |

### Elérhető Stíluspresetek (Language Style Presets):
1. **Modern, laza (kortárs akció/kaland)** — Rövid, pergő mondatok, bevett gamer szleng megtartása (loot, buff, spawn).
2. **Fantasy/archaikus (RPG, epikus fantasy)** — Emelkedett stílus, magyarosított szakkifejezések (zsákmány, küldetés), irodalmibb mondatszerkezetek.
3. **Semleges/technikai (szimulátor, stratégia, UI-nehéz)** — Tömör, pontos, funkcionális megfogalmazások, szakzsargon megőrzése.
4. **Humoros/ironikus (comedy/paródia)** — Szabadabb fordítói mozgástér a magyar poénok és szójátékok érvényesüléséhez.
5. **Szoftver UI / Asztali alkalmazás** — Professzionális szoftver menük, dialógusok, egységes gombfeliratok és gyorsbillentyűk.
6. **Szoftver Műszaki / Dokumentáció** — Rendszerdokumentáció, hibaelhárítás, technikai kézikönyvek.
7. **Szoftver Eszköz / CLI & Fejlesztői** — Fejlesztői eszközök, terminál parancsok, paraméterek leírásai.

---

## 6. Ajánlott Munkafolyamat (Lépésről lépésre)

A legbiztonságosabb és legköltséghatékonyabb lokalizációs sorrend:

### 1. Lépés: Adatkinyerés (Dump)
Exportáld ki a játék szövegeit a megfelelő formátumban:
- **Naninovel (Visual Novel):** Naninovel által generált `.txt` lokalizációs dokumentumok (`Scripts/*.txt` párbeszédek és gyökérkönyvtári Managed Text fájlok, pl. `DefaultUI.txt`, `CharacterNames.txt`).
- **Unity:** UABEA JSON dump (MonoBehaviour vagy TextAsset CSV) vagy Unity Localization CSV.
- **Unreal Engine 4/5:** Localization Dashboard `.po` fájlok.
- **Egyéb / Szoftver:** Standard `.po`, GNU gettext, XLIFF 1.2 vagy egyszerű `.json` kulcs-érték párok.

### 2. Lépés: Projekt létrehozása
A **Projects** fülön kattints a **+ Create New Project** gombra, vagy használd a CLI-t:
```bash
# Játék projekt (pl. japán -> magyar):
locpipe init JatekNev --type game --source ja --target hu --format uabea_json

# Szoftver projekt (pl. angol -> magyar):
locpipe init SzoftverNev --type software --source en --target hu --format po_gettext
```
- Másold be a kinyert fájlokat a projekt mappájába (pl. `projects/JatekNev/batches/`).

### 3. Lépés: Audit vizsgálat (Zajszűrés ellenőrzése)
Nyisd meg az **Audit** fület, vagy futtasd:
```bash
locpipe audit --project "projects/JatekNev"
```
Ellenőrizd az `audit_report.md` jelentést. Ha motor-belső technikai azonosítók (GUID, belső elérési utak) maradtak a megtartott (`kept`) sorok között, kattints az **Exclude Selected Path** gombra a GUI-ban.

### 4. Lépés: Preflight Terv (Költség- és tokenszámítás)
A **Run** fülön kattints a **📋 Run Plan (Dry Estimate)** gombra (vagy CLI-ben: `locpipe plan --project "projects/JatekNev"`).
Ez **0 API hívással** és 0 költséggel pontosan megmutatja a bejegyzések számát, a dedup arányt és a becsült tokenszükségletet.

### 5. Lépés: Kis tesztfutás & Erőforrás-generálás (Bootstrap)
Futtass le egy tesztet pl. 1 batch-re vagy 20 API hívásos korláttal:
```bash
locpipe run --project "projects/JatekNev" --limit 1 --max-api-calls 20
```
A sikeres próba után generáld le az erőforrás-vázlatokat:
- Kattints a Projects fülön a **⚡ Bootstrap Resources (from TM)** gombra (vagy CLI-ben: `locpipe bootstrap-resources --project "projects/JatekNev"`).
- Nyisd meg a `resources/` mappát, nézd át a `glossary.draft.md` és `character-voices.draft.md` fájlokat.
- Ha elégedett vagy velük, nevezd át őket végleges `glossary.md` és `character-voices.md` névre.

### 6. Lépés: Teljes fordítási futás
Indítsd el a teljes projekt fordítását a **Run** fülön, vagy külön külső **PowerShell** terminál ablakban:
```powershell
locpipe run --project "projects/JatekNev"
```
- **Tipp:** Ha AI asszisztenst használsz, a teljes fordítást mindig a **saját külső terminálodban** futtasd le az AI chat helyett, hogy a konzol napló tiszta maradjon.
- A folyamat valós időben menti az eredményeket a `checkpoint.json`-ba és a Translation Memory-ba (`tm/translation_memory.sqlite3`).
- Megszakítás esetén egyszerűen indítsd újra a parancsot: automatikusan ott folytatja, ahol abbahagyta.

### 7. Lépés: Utólagos integritás-ellenőrzés
Bizonyítsd be, hogy a motor-specifikus és kizárt adatok 100%-ban érintetlenek maradtak:
```bash
locpipe verify --project "projects/JatekNev"
```

### 8. Lépés: Minőségellenőrzés & Riportok
A futás végén a `review/` mappában három elemző riport áll rendelkezésre:
- **`review/full_bilingual_report.md`** — Teljes kétnyelvű táblázat az összes lefordított szövegről lektoráláshoz.
- **`review/consistency_report.md`** — Hasonló forrásszövegek eltérő fordításainak összehasonlítása.
- **`review/review_report.md`** — A hibás vagy QA által javított sorok részletes naplója.

### 9. Lépés: Visszaimportálás a játékba / szoftverbe
Importáld vissza az elkészült JSON / CSV / PO fájlokat a célalkalmazásba. Unity IL2CPP játékok esetén a GUI **Fix CRC** gombjával állítsd helyre a bináris integritást.

---

## 7. `project.yaml` Konfigurációs Referencia

```yaml
project: Sunderfolk PC
project_type: game          # game | software
profile: fast               # fast (70% sebesség / 30% minőség — ajánlott) | balanced | thorough
source_lang: en             # en | ja | hu | stb.
target_lang: hu             # hu | en | ja | stb.
target_register: informal   # informal | formal
format: uabea_json          # naninovel | uabea_json | unity | ue4_5_po | po_gettext | generic_kv | xliff

batches:
  glob: "*/*.json"          # bemeneti fájlok mintázata

resources:
  glossary: resources/glossary.md
  lang_style: resources/lang-style.md
  character_voices: resources/character-voices.md
  anti_fabrication_checklist: resources/anti-fabrication-checklist.md

categories:
  - name: dialogue
    match_speaker_present: true
    needs_character_voice: false # false, ha a dump kulcsai nem tartalmaznak konkrét karakterneveket
    batch_size: 100              # 80-100: megszünteti a JSON csonkolást, 20-30 mp/hívás
    max_expansion_ratio: 1.8
  - name: ui
    default: true
    needs_character_voice: false
    batch_size: 120              # 100-120: optimális tömör UI gombokhoz
    max_expansion_ratio: 1.3

provider:
  name: antigravity_cli     # Google Antigravity CLI motor
  model: gemini-3.8-flash   # fordítási modell
  effort: low               # low | high
  review_model: gemini-3.8-flash
  review_effort: low        # low (fast) | high (thorough)
  max_concurrency: 2

format_options:
  noise_filter: true
  character_replacements:
    ő: ô
    ű: û
    Ő: Ô
    Ű: Û
  uabea_json_path_exclude:
    - "^m_LocaleId\.m_Code$"
    - "^m_Identifier\.m_Code$"
    - "^m_LocaleName$"
    - "^references\."

tm:
  db_path: tm/translation_memory.sqlite3

confidence:
  review_threshold: 0.65    # fast: 0.65 | balanced: 0.70 | thorough: 0.75
  tier1_repair_attempts: 2
  allow_identical_proper_nouns: true  # Nem küldi felesleges felülvizsgálatra a változatlan neveket (Rosa, Balder, OK)
```

### Profilok & Token Optimalizálás (Sebesség vs. Költség vs. Minőség)

A `project.yaml`-ban beállítható `profile:` közvetlenül szabályozza a felülvizsgálat szigorúságát és az API hívások sebességét:

| Profil | Review Küszöb | Review Effort | Mintavétel (Fidelity) | Eszkaláció | Mikor ajánlott? |
|---|---|---|---|---|---|
| **`fast`** | `0.65` | `low` | `0.0` (kikapcsolva) | Kikapcsolva | Óriási szövegtömegnél, gyors próbaköröknél. |
| **`balanced` (Ajánlott)** | `0.70` | `low` | `0.01` (1% szúrópróba) | Kikapcsolva | **Legjobb általános beállítás:** 2-3x gyorsabb, 0 felesleges thinking token, stabil végeredmény. |
| **`thorough`** | `0.75` | `high` | `0.03` (3% szúrópróba) | Bekapcsolva (`high`) | Erősen irodalmi RPG-khez, költői dialógusokhoz. |

> [!TIP]
> **Kell-e a felülvizsgálathoz `high` effort?**  
> **NEM feltétlenül.** Méréseink szerint a `gemini-3.8-flash` modellnél az `effort: low` **~6 másodperc** alatt fut le hívásonként, míg a `high` effort 12–40+ másodpercig gondolkodik, és több ezer thinking tokent éget el. Mivel a felülvizsgálati prompt konkrétan megjelöli a javítandó kifejezést vagy változót, a modell `low` effort mellett is hibátlanul javít. Általános játékokhoz a **`profile: balanced`** (vagy `review_effort: low`) nyújtja a leggyorsabb és legtakarékosabb, stabil működést!

---

## 8. LocPipe Parancsok Gyorsreferenciája

| Parancs | Leírás | LLM Hívás? |
|---|---|:---:|
| `locpipe init <név> [--type game|software] [--source en] [--target hu] [--format naninovel|uabea_json|...]` | Új projektstruktúra létrehozása megadott típussal és nyelvvel | ❌ |
| `locpipe plan --project <útvonal> [--limit N]` | Előzetes token-, duplikáció- és batch-számítás | ❌ |
| `locpipe audit --project <útvonal>` | Formátum zajszűrésének vizsgálata (`audit_report.md`) | ❌ |
| `locpipe verify --project <útvonal>` | Fordítás utáni integritás-ellenőrzés (bizonyítja a zaj érintetlenségét) | ❌ |
| `locpipe run --project <útvonal>` | Fordítási folyamat futtatása | ✔️ |
| `locpipe run ... --dry-run` | Pipeline tesztelés mock providerrel (fájlba ír, TM-et nem szennyez) | ❌ |
| `locpipe run ... --pseudo-loc` | Pszeudo-lokalizáció UI túlcsordulások tesztelésére (+30% hossz) | ❌ |
| `locpipe run ... --limit <N>` | Csak az első N fájl feldolgozása teszteléshez | ✔️ |
| `locpipe run ... --max-api-calls <N>` | Szigorú felső korlát az elküldhető API kérések számára | ✔️ |
| `locpipe tm-invalidate --project <útvonal> --key <szó>` | Adott fordítás törlése a TM-ből újrafordítás kényszerítéséhez | ❌ |
| `locpipe bootstrap-resources --project <útvonal>` | Glosszárium, stílus és hangnem vázlatok készítése a TM-ből | ✔️ |

---

## 9. Automatikus Tesztelés és Minőségellenőrzés (QA)

A rendszer teljes körű (190 tesztből álló, 100% zöld) automatizált tesztcsomaggal rendelkezik, amely garantálja a motorok, adapterek, vezérlőkód-védelmek, validátorok és a fordítási memória hibátlan működését.

A teljes tesztcsomag futtatása:
```bash
uv run pytest
```

Futtatás részletes lefedettségi riporttal (coverage):
```bash
uv run pytest --cov=locpipe --cov=gamestringer test_cli.py test_gui.py test_gamestringer_core_extra.py locpipe/tests
```

