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

- **Per ruimte:** het 🔥-knopje naast de pauzeknop van een ruimte
- **Alles:** de pauzeknop in de kop van de kaart, met de boost-sectie onderaan
- Kies **+0,5 tot +4 °C** en de duur: **tot het volgende blok**, of 30, 60, 120 of 180 minuten
- Alle radiatoren in de ruimte gaan omhoog, en de ketel volgt
- Daarna gaat de ruimte automatisch terug naar het programma. Dat werkt ook na een herstart van Home Assistant.

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

## Backup

Alles wordt opgeslagen in de instellingen van de integratie: de ketelinstellingen (`boiler_climate`)
en lopende boosts (`room_pause` met `source: boost`). Daardoor zit het automatisch in de bestaande
backup en restore. Je bestaande schema's en backups blijven behouden, omdat de integratie hetzelfde
domein (`thermostat_timeline`) gebruikt.

## Installeren via HACS

1. HACS → ⋮ → **Custom repositories** → `https://github.com/lucasmunster06-ship-it/thermostat-pro-timeline`,
   type **Integration**
2. Heb je het origineel al via HACS? Verwijder dat eerst in HACS. Je data blijft staan.
3. Installeer deze fork en herstart Home Assistant
4. Ververs de browser (Ctrl+F5), zodat de nieuwe kaart (versie 3.6.0) laadt

Deze fork werkt zonder GitHub-releases. HACS installeert direct vanaf de `main`-branch.

## Gepland

- **Fase 2:** grafieken per ruimte (gemeten temperatuur, programma, buitentemperatuur, tijdbalken voor ketel, boost en raam)
- **Fase 3:** goedkoopste warmtebron kiezen tussen gas, airco/warmtepomp en elektrisch, met een vaste prijs of een prijssensor (bijv. Frank Energie)
