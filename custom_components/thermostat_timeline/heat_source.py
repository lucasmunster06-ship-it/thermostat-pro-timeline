"""Pick the cheapest heat source per room: gas boiler, heat pump / air conditioner or electric heater.

Configuration lives in the instance settings under ``heat_sources`` (so it is part of
Timeline's backup/restore)::

    heat_sources:
      enabled: true
      gas_price:   {mode: fixed, value: 1.30}            # EUR per m3
      power_price: {mode: entity, entity: sensor.frank_current_price}   # EUR per kWh
      gas_kwh_per_m3: 9.77        # calorific value (NL: 9.77 kWh/m3)
      boiler_efficiency: 0.90     # 0.9 = 90 %
      outdoor_sensor: sensor.nefit_outdoor_temperature
      margin_pct: 5               # only switch when the other source is this much cheaper
      backup_offset: 2.0          # radiators stay this much lower as gas back-up
      rooms:
        climate.living:
          - entity: climate.eurom
            kind: heat_pump       # heat_pump | electric
            mode: auto            # auto | always | never
            cop: [[-7, 2.2], [2, 3.0], [7, 3.8], [12, 4.5]]
            min_outdoor: -10

Runtime decisions are kept in memory and exposed as ``heat_status`` in the state API.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, SIGNAL_UPDATED
from .thermal import celsius, numeric

_LOGGER = logging.getLogger(__name__)

DEFAULT_COP = [[-7.0, 2.2], [2.0, 3.0], [7.0, 3.8], [12.0, 4.5]]
MIN_DWELL_S = 10 * 60


def cop_at(points, outdoor_c):
    """Linear interpolation of the COP curve, clamped at both ends."""
    pts = []
    for row in points or DEFAULT_COP:
        try:
            t, c = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if c > 0:
            pts.append((t, c))
    if not pts:
        pts = [tuple(p) for p in DEFAULT_COP]
    pts.sort()
    if outdoor_c is None:
        # Unknown outdoor temperature: assume a mild day.
        outdoor_c = 7.0
    if outdoor_c <= pts[0][0]:
        return pts[0][1]
    if outdoor_c >= pts[-1][0]:
        return pts[-1][1]
    for (t1, c1), (t2, c2) in zip(pts, pts[1:]):
        if t1 <= outdoor_c <= t2:
            return c1 + (c2 - c1) * (outdoor_c - t1) / (t2 - t1) if t2 > t1 else c1
    return pts[-1][1]


def gas_heat_cost(gas_m3, kwh_per_m3=9.77, efficiency=0.9):
    """EUR per kWh of heat delivered by the boiler."""
    if gas_m3 is None:
        return None
    denom = max(0.1, float(kwh_per_m3 or 9.77)) * max(0.3, min(1.1, float(efficiency or 0.9)))
    return gas_m3 / denom


def device_heat_cost(power_kwh, device, outdoor_c):
    """EUR per kWh of heat from an electric device and the efficiency used."""
    if power_kwh is None:
        return None, None
    if device.get("kind") == "electric":
        return power_kwh, 1.0
    cop = cop_at(device.get("cop"), outdoor_c)
    return power_kwh / cop, cop


def choose(device, gas_cost, alt_cost, outdoor_c, current, margin_pct=5.0):
    """Return ("gas"|"alt", reason) for one device; `current` gives hysteresis."""
    mode = device.get("mode") or "auto"
    if mode == "always":
        return "alt", "always"
    if mode == "never":
        return "gas", "never"
    lowest = numeric(device.get("min_outdoor"))
    if device.get("kind") != "electric" and lowest is not None and outdoor_c is not None and outdoor_c < lowest:
        return "gas", "too_cold"
    if gas_cost is None and alt_cost is None:
        return current or "gas", "no_prices"
    if gas_cost is None:
        return "alt", "no_gas_price"
    if alt_cost is None:
        return "gas", "no_power_price"
    margin = max(0.0, float(margin_pct or 0)) / 100.0
    if current == "alt":
        return ("gas", "gas_cheaper") if gas_cost < alt_cost * (1 - margin) else ("alt", "alt_cheaper")
    return ("alt", "alt_cheaper") if alt_cost < gas_cost * (1 - margin) else ("gas", "gas_cheaper")


class HeatSources:
    def __init__(self, hass, entry, managers):
        self.hass, self.entry, self.managers = hass, entry, managers
        self.status = {}  # iid -> {"prices": {...}, "rooms": {room: {...}}}
        self.choice = {}  # "iid/room" -> {"source": str, "since": float}
        self.task = None
        self.dirty = False

    # ---- called by the apply manager -------------------------------------------------
    def backup_offset_c(self, iid, room) -> float:
        """How many degrees radiators of this room stay below target while another source heats it."""
        choice = self.choice.get(f"{iid}/{room}")
        if not choice or choice.get("source") != "alt":
            return 0.0
        cfg = self._cfg(iid)
        value = numeric(cfg.get("backup_offset"))
        return max(0.0, min(10.0, value if value is not None else 2.0))

    # ---- lifecycle --------------------------------------------------------------------
    def start(self):
        self.entry.async_on_unload(async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self.wake))
        self.entry.async_on_unload(async_track_time_interval(self.hass, self.wake, timedelta(minutes=1)))
        self.entry.async_on_unload(self.close)
        self.wake()

    def close(self):
        if self.task:
            self.task.cancel()

    @callback
    def wake(self, *_):
        self.dirty = True
        if self.task is None or self.task.done():
            self.task = self.entry.async_create_background_task(
                self.hass, self.run(), "Timeline heat sources", eager_start=False
            )

    async def run(self):
        while self.dirty:
            self.dirty = False
            for iid, mgr in list(getattr(self.managers, "_managers", {}).items()):
                try:
                    await self.update(iid, mgr)
                except Exception:
                    _LOGGER.exception("Heat source update failed for %s", iid)

    # ---- helpers ----------------------------------------------------------------------
    def _cfg(self, iid):
        mgr = getattr(self.managers, "_managers", {}).get(iid)
        if mgr is None:
            return {}
        _schedules, settings = mgr._get_data()
        cfg = settings.get("heat_sources")
        return cfg if isinstance(cfg, dict) else {}

    def _price(self, spec, want_unit):
        """Fixed number or the state of a price sensor. Converts cents to euros when the unit says so."""
        if not isinstance(spec, dict):
            return None
        if spec.get("mode") == "entity":
            state = self.hass.states.get(str(spec.get("entity") or ""))
            if state is None:
                return None
            value = numeric(state.state)
            if value is None:
                return None
            unit = str(state.attributes.get("unit_of_measurement") or "").lower()
            if "ct" in unit or "cent" in unit:
                value /= 100.0
            if want_unit == "kWh" and "mwh" in unit:
                value /= 1000.0
            return value
        return numeric(spec.get("value"))

    def _temperature(self, entity_id):
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        if entity_id.startswith("climate."):
            value = numeric(state.attributes.get("current_temperature"))
            if value is None:
                return None
            unit = self.hass.config.units.temperature_unit
            return celsius(value, unit if unit in ("°C", "°F") else "°C")
        unit = state.attributes.get("unit_of_measurement") or "°C"
        return celsius(state.state, unit)

    def _room_temp(self, settings, room):
        sensor = (settings.get("temp_sensors") or {}).get(room)
        value = self._temperature(sensor) if isinstance(sensor, str) and sensor else None
        return value if value is not None else self._temperature(room)

    async def _call(self, domain, service, data):
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except Exception:
            _LOGGER.warning("Heat source: %s.%s failed for %s", domain, service, data.get("entity_id"), exc_info=True)

    async def _drive(self, device, run, target_c, room_c):
        """Run or stop one device. climate gets heat + target, switch is thermostatically switched."""
        eid = str(device.get("entity") or "")
        state = self.hass.states.get(eid)
        if state is None or state.state in ("unknown", "unavailable"):
            return
        domain = eid.split(".", 1)[0]
        if domain == "climate":
            attrs = state.attributes or {}
            if not run or target_c is None:
                if state.state != "off":
                    await self._call("climate", "set_hvac_mode", {"entity_id": eid, "hvac_mode": "off"})
                return
            modes = [str(m) for m in attrs.get("hvac_modes") or []]
            # Plain "heat" when offered; many Tuya heaters only know "heat_cool" or "auto".
            want_mode = device.get("hvac_mode") or next((m for m in ("heat", "heat_cool", "auto") if m in modes), None)
            if want_mode and state.state != want_mode and want_mode in modes:
                await self._call("climate", "set_hvac_mode", {"entity_id": eid, "hvac_mode": want_mode})
            unit = self.hass.config.units.temperature_unit
            value = target_c * 1.8 + 32 if unit == "°F" else target_c
            lo, hi = numeric(attrs.get("min_temp")), numeric(attrs.get("max_temp"))
            if lo is not None:
                value = max(value, lo)
            if hi is not None:
                value = min(value, hi)
            current = numeric(attrs.get("temperature"))
            if current is None or abs(current - value) >= 0.1:
                await self._call("climate", "set_temperature", {"entity_id": eid, "temperature": round(value, 1)})
            return
        if domain in ("switch", "input_boolean"):
            on = state.state == "on"
            want = on
            if not run or target_c is None or room_c is None:
                want = False
            elif room_c < target_c - 0.3:
                want = True
            elif room_c >= target_c + 0.2:
                want = False
            if want != on:
                await self._call(domain, "turn_on" if want else "turn_off", {"entity_id": eid})

    # ---- main loop --------------------------------------------------------------------
    async def update(self, iid, mgr):
        schedules, settings = mgr._get_data()
        cfg = settings.get("heat_sources") if isinstance(settings.get("heat_sources"), dict) else {}
        rooms_cfg = cfg.get("rooms") if isinstance(cfg.get("rooms"), dict) else {}
        enabled = bool(cfg.get("enabled")) and settings.get("auto_apply_enabled", True) is not False

        gas_m3 = self._price(cfg.get("gas_price"), "m3")
        power_kwh = self._price(cfg.get("power_price"), "kWh")
        outdoor = self._temperature(str(cfg.get("outdoor_sensor") or "")) if cfg.get("outdoor_sensor") else None
        gas_cost = gas_heat_cost(gas_m3, numeric(cfg.get("gas_kwh_per_m3")) or 9.77,
                                 numeric(cfg.get("boiler_efficiency")) or 0.9)
        status = {"enabled": enabled, "prices": {
            "gas_m3": gas_m3, "power_kwh": power_kwh, "outdoor_c": outdoor, "gas_heat_kwh": gas_cost,
        }, "rooms": {}}

        now_min = mgr._now_min()
        changed_rooms = []
        for room, devices in rooms_cfg.items():
            if room not in schedules or not isinstance(devices, list):
                continue
            devices = [d for d in devices if isinstance(d, dict) and d.get("entity")]
            if not devices:
                continue
            key = f"{iid}/{room}"
            prev = (self.choice.get(key) or {}).get("source", "gas")
            target_c = mgr._room_target_c(room, schedules, settings, now_min)
            mode = mgr._block_mode(room, schedules, settings, now_min) if hasattr(mgr, "_block_mode") else None
            heating_wanted = target_c is not None and mode not in ("off", "cool", "fan_only", "dry")
            if hasattr(mgr, "_summer_off") and mgr._summer_off(room, settings):
                heating_wanted = False
            room_c = self._room_temp(settings, room)

            # Cheapest device for this room.
            best = None
            for device in devices:
                cost, eff = device_heat_cost(power_kwh, device, outdoor)
                source, reason = choose(device, gas_cost, cost, outdoor, prev, cfg.get("margin_pct", 5))
                entry = {"entity": device["entity"], "kind": device.get("kind", "heat_pump"),
                         "cost_kwh": cost, "cop": eff, "source": source, "reason": reason}
                if source == "alt" and (best is None or (cost or 0) < (best["cost_kwh"] or 0)):
                    best = entry
                status["rooms"].setdefault(room, {"devices": []})["devices"].append(entry)

            source = "alt" if (enabled and heating_wanted and best is not None) else "gas"
            active = best["entity"] if source == "alt" else None
            previous = self.choice.get(key) or {}
            since = previous.get("since", 0.0)
            prev_device = previous.get("device")
            if (source != prev and time.time() - since < MIN_DWELL_S and enabled and heating_wanted
                    and (prev == "gas" or any(d["entity"] == prev_device for d in devices))):
                # Avoid flapping on small price changes: keep the last choice for a while.
                source, active = prev, (prev_device if prev == "alt" else None)
            if source != prev:
                self.choice[key] = {"source": source, "since": time.time(), "device": active}
                changed_rooms.append(room)
                _LOGGER.info("Timeline heat source %s: %s -> %s", room, prev, source)
            else:
                self.choice[key] = {"source": source, "since": since or time.time(), "device": active}

            room_status = status["rooms"][room]
            room_status.update({"source": source, "active": active, "target_c": target_c, "room_c": room_c,
                                "since": self.choice[key]["since"]})
            for device in devices:
                if not enabled and device.get("mode") != "always" and prev != "alt":
                    continue  # feature off: leave devices alone (but stop one we were running)
                await self._drive(device, device["entity"] == active, target_c, room_c)

        self.status[iid] = status
        for room in changed_rooms:
            await mgr._apply_room_now(room)
        if changed_rooms:
            await mgr._maybe_control_boiler(force=True)
