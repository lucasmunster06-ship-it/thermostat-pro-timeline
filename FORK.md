# Fork: boost + thermostaat als ketel

Deze fork bouwt voort op [qlerup/thermostat-pro-timeline](https://github.com/qlerup/thermostat-pro-timeline)
en voegt het volgende toe:

## 1. Thermostaat als ketel (bijv. Nefit Easy)

Bij **Instellingen → Ketel** kun je naast `switch` en `input_boolean` nu ook **Climate (thermostaat)**
kiezen. Zodra een ruimte warmte vraagt (gemeten temperatuur lager dan doel − AAN-offset), wordt de
setpoint van die thermostaat verhoogd, zodat de ketel gaat branden. Vraagt geen enkele ruimte nog warmte,
dan gaat de setpoint weer terug.

Instellingen (verschijnen zodra je een climate-entiteit kiest):

| Instelling | Standaard | Betekenis |
|---|---|---|
| Verhogen boven gemeten temp | 2 °C | Setpoint = gemeten temperatuur bij de thermostaat + dit getal |
| Hoogste setpoint | 24 °C | Beschermt de ruimte waar de thermostaat hangt tegen oververhitting |
| Setpoint zonder warmtevraag | 15 °C | Alleen als de thermostaat **geen** eigen ruimte in Timeline is |

Is de Nefit zelf ook een ruimte in je Timeline (bijv. de woonkamer), dan volgt hij buiten de warmtevraag
gewoon het schema van die ruimte, en tijdens warmtevraag nooit lager dan dat schema. Het verhogen door de
ketelsturing wordt niet aangezien voor een handmatige wijziging.

**Tips voor de Nefit**
- Zet het klokprogramma van de Nefit zelf uit (handmatige modus). Als de thermostaat een `manual`- of
  `hold`-preset heeft, kiest Timeline die automatisch.
- Zet **"Use manual thermostat targets for boiler demand"** aan. Dan laat het ook de ketel aanslaan
  als je met de hand aan een radiatorknop draait.

## 2. Meerdere radiatoren per ruimte

Gebruik **Merge thermostats** om meerdere knoppen (bijv. Nedis Zigbee) onder één ruimte te hangen.
Nieuw: alle radiatoren van een ruimte tellen mee voor de ketelvraag, ook als je bij de ketel alleen de
ruimtes aanvinkt. Eén koude radiator is genoeg om de ketel te laten aanslaan.

## 3. Boost per ruimte of voor alles

- **Per ruimte:** het 🔥-knopje bij de ruimte. **Alles:** de 🔥-knop in de kop van de kaart.
- Kies hoeveel warmer (+0,5 tot +4 °C) en hoe lang: **tot het volgende blok**, of 30 min tot 3 uur.
- Een lopende boost zie je aan het oranje label bij de ruimte ("Boost 22° tot 20:16") en aan de
  gestreepte balk in de tijdlijn. Een handmatige wijziging aan een knop krijgt een blauw label.
- Alle radiatoren in de ruimte gaan omhoog, en de ketel volgt. Daarna gaat de ruimte vanzelf terug
  naar het programma, ook na een herstart.
- Pauzeren heeft een eigen venster (pauzeknop).

Voor automatiseringen of dashboardknoppen:

```yaml
action: thermostat_timeline.boost
data:
  entity_id: climate.slaapkamer   # weglaten = alle ruimtes
  delta: 1.5                      # of: temperature: 22
  duration_minutes: 60            # weglaten = tot het volgende blok
```

```yaml
action: thermostat_timeline.cancel_boost
data:
  entity_id: climate.slaapkamer   # weglaten = alle boosts stoppen
```

## 4. Grafiek per ruimte

Het 📈-knopje bij een ruimte opent een grafiek met de gemeten temperatuur (en die van de andere
radiatoren), de ingestelde temperatuur, het programma (ook vooruit), de buitentemperatuur, en
tijdbalken voor verwarmen, ketel, extra warmtebron en boost/handmatig. Je kiest 6 uur tot 7 dagen
terug en tot 24 uur vooruit. Beweeg over de grafiek of tik erop voor de waarden. De geschiedenis komt
uit de recorder van Home Assistant.

## 5. Goedkoopste warmtebron (gas, airco/warmtepomp, elektrisch)

Ga naar **Instellingen → Ketel → Warmtebronnen & kosten → Instellen**.

- **Gasprijs** in €/m³ en **stroomprijs** in €/kWh: als vast bedrag of uit een sensor (bijv. Frank
  Energie). Een sensor in ct/kWh wordt automatisch omgerekend. Gebruik all-in prijzen.
- **Ketelrendement** (standaard 90 %) en **kWh per m³** (9,77 in NL).
- **Buitentemperatuur-sensor**, bijv. die van de Nefit.
- **Per ruimte een extra apparaat**: een airco/warmtepomp (`climate`) met het rendement (COP) bij
  −7, 2, 7 en 12 °C buiten, of een elektrische kachel (`switch`, COP 1).

Elke minuut rekent Timeline uit wat 1 kWh warmte kost:
gas = gasprijs ÷ (9,77 × rendement), airco = stroomprijs ÷ COP bij de huidige buitentemperatuur.
Is het apparaat minstens de ingestelde marge (5 %) goedkoper, dan:
- gaat de airco op verwarmen naar de doeltemperatuur van de ruimte, en wordt een kachel aan/uit
  geschakeld op de kamertemperatuur;
- blijven de radiatoren 2 °C lager als back-up, zodat de ketel alleen bijspringt als het apparaat
  het niet redt.

Om pendelen te voorkomen wisselt hij hooguit eens per 10 minuten. Onder de ingestelde
buitentemperatuur (standaard −10 °C) wordt de airco niet gebruikt. Zet je de functie uit, dan wordt
een draaiend apparaat uitgezet. Het groene €-knopje in de kop van de kaart opent het overzicht met
de actuele prijzen per kWh warmte.

## Backup

Alles wordt opgeslagen in de instellingen van de integratie: de ketelinstellingen (`boiler_climate`),
de warmtebronnen en prijzen (`heat_sources`) en lopende boosts (`room_pause` met `source: boost`). Daardoor zit het automatisch in de bestaande
backup en restore. Je bestaande schema's en backups blijven behouden, omdat de integratie hetzelfde
domein (`thermostat_timeline`) gebruikt.

## Installeren via HACS

1. HACS → ⋮ → **Custom repositories** → `https://github.com/lucasmunster06-ship-it/thermostat-pro-timeline`,
   type **Integration**
2. Heb je het origineel al via HACS? Verwijder dat eerst in HACS. Je data blijft staan.
3. Installeer deze fork en herstart Home Assistant
4. Ververs de browser (Ctrl+F5), zodat de nieuwe kaart (versie 3.6.0) laadt

Deze fork werkt zonder GitHub-releases. HACS installeert direct vanaf de `main`-branch.

## Gepland / ideeën

- Kosten per dag bijhouden (wat heeft verwarmen gekost, wat heeft de airco bespaard)
