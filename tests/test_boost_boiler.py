"""Boost per room/all, rooms with several radiators and a climate (thermostat) boiler."""
import ast
import asyncio
import copy
import logging
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


class BoostBoilerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
        source = Path(__file__).resolve().parents[1] / "custom_components/thermostat_timeline/__init__.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        manager = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AutoApplyManager")
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), manager], type_ignores=[])
        g = {
            "asyncio": asyncio, "timedelta": timedelta, "DOMAIN": "thermostat_timeline",
            "BOILER_DOMAINS": ("switch", "input_boolean", "climate"),
            "_norm_instance_id": lambda value: value or "default", "callback": lambda fn: fn,
            "_LOGGER": logging.getLogger("test"), "_f_to_c": lambda f: (f - 32) * 5 / 9,
            "_c_to_f": lambda c: c * 9 / 5 + 32, "UnitOfTemperature": SimpleNamespace(FAHRENHEIT="°F"),
            "time": SimpleNamespace(time=lambda: self.now.timestamp()),
            "dt_util": SimpleNamespace(now=lambda: self.now, utcnow=lambda: self.now),
        }
        exec(compile(ast.fix_missing_locations(module), "timeline_manager", "exec"), g)
        self.living, self.bed, self.bed2, self.nefit = "climate.living", "climate.bed", "climate.bed_2", "climate.nefit"
        blocks = [{"startMin": 360, "endMin": 720, "temp": 20}, {"startMin": 1020, "endMin": 1320, "temp": 20}]
        self.schedules = {eid: {"defaultTemp": 16, "blocks": copy.deepcopy(blocks)} for eid in (self.living, self.bed)}
        self.settings = {
            "auto_apply_enabled": True, "storage_temp_unit": "C", "room_pause": {}, "max_temp": 25,
            "merges": {self.bed: [self.bed2]},
            "boiler_enabled": True, "boiler_switch": self.nefit, "boiler_on_offset": 0.3, "boiler_off_offset": 0.0,
            "boiler_rooms": [self.living, self.bed],
            "boiler_climate": {"on_offset": 2, "max_temp": 24, "idle_temp": 15},
        }

        def climate(cur, target):
            return SimpleNamespace(state="heat", attributes={
                "temperature": target, "current_temperature": cur, "hvac_modes": ["heat", "off"],
                "min_temp": 5, "max_temp": 30})

        self.states = {
            self.living: climate(20.0, 20), self.bed: climate(20.0, 20), self.bed2: climate(20.0, 20),
            self.nefit: climate(20.0, 15),
        }
        self.calls = []

        async def call(domain, service, data, blocking=False):
            self.calls.append((domain, service, copy.deepcopy(data)))
            if domain == "thermostat_timeline":
                self.settings.update(copy.deepcopy(data["settings"]))
            elif domain == "climate" and service == "set_temperature":
                self.states[data["entity_id"]].attributes["temperature"] = data["temperature"]

        self.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get),
                                    services=SimpleNamespace(async_call=call),
                                    config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°C")))
        mgr = g["AutoApplyManager"](self.hass)
        mgr._get_data = lambda: (self.schedules, self.settings)
        mgr._effective_blocks_today = lambda row, settings, primary=None: row["blocks"]
        mgr._desired_for = lambda eid, schedules, settings, minute: 20
        mgr._now_min = lambda: 600  # 10:00, next block boundary at 12:00
        mgr._summer_off = lambda eid, settings: False
        mgr._block_mode = lambda *a: None
        mgr._owd_mgr = lambda: None

        async def noop(*a, **k):
            return None
        mgr._schedule_next = noop
        mgr._apply_room_now = noop
        self.mgr = mgr

    def temps(self, eid):
        return [c[2]["temperature"] for c in self.calls if c[1] == "set_temperature" and c[2]["entity_id"] == eid]

    async def test_boost_room_reaches_every_radiator_until_next_block(self):
        boosted = await self.mgr.async_boost(rooms=[self.bed], delta_c=2)
        self.assertEqual(boosted, {self.bed: 22.0})
        hold = self.settings["room_pause"][self.bed]
        self.assertEqual(hold["source"], "boost")
        self.assertEqual(hold["until_ms"], (self.now.replace(hour=12)).timestamp() * 1000)
        self.assertEqual(self.temps(self.bed), [22.0])
        self.assertEqual(self.temps(self.bed2), [22.0])
        self.assertNotIn(self.living, self.settings["room_pause"])
        # The bedroom now asks for heat, so the Nefit is raised above its own room temperature.
        self.assertEqual(self.temps(self.nefit), [22.0])

    async def test_boost_all_with_duration_and_cancel(self):
        await self.mgr.async_boost(temperature_c=30, minutes=45)  # capped at the card's max_temp
        pauses = self.settings["room_pause"]
        self.assertEqual(set(pauses), {self.living, self.bed})
        self.assertTrue(all(p["target_c"] == 25 for p in pauses.values()))
        self.assertEqual(pauses[self.living]["until_ms"], self.now.timestamp() * 1000 + 45 * 60000)
        self.settings["room_pause"]["climate.other"] = {"indef": True}
        ended = await self.mgr.async_cancel_boost()
        self.assertEqual(set(ended), {self.living, self.bed})
        self.assertEqual(self.settings["room_pause"], {"climate.other": {"indef": True}})
        # No demand any more: Nefit back to its idle temperature.
        self.assertEqual(self.temps(self.nefit)[-1], 15)

    async def test_turning_up_a_merged_radiator_raises_the_boiler(self):
        self.settings["boiler_manual_targets"] = True
        self.settings["room_pause"] = {self.bed: {"indef": False, "until_ms": self.now.timestamp() * 1000 + 60000,
                                                  "source": "manual_target", "target_c": 22}}
        await self.mgr._maybe_control_boiler()
        self.assertEqual(self.temps(self.nefit), [22.0])
        self.assertEqual(self.mgr._boiler_raised_c[self.nefit], 22.0)
        # While the Nefit is still above its own room temperature nothing is resent.
        await self.mgr._maybe_control_boiler()
        self.assertEqual(len(self.temps(self.nefit)), 1)
        # Room warm again -> back to idle.
        self.settings["room_pause"] = {}
        self.states[self.bed2].attributes["current_temperature"] = 20.5
        await self.mgr._maybe_control_boiler()
        self.assertEqual(self.temps(self.nefit)[-1], 15)
        self.assertNotIn(self.nefit, self.mgr._boiler_raised_c)

    async def test_boiler_thermostat_that_is_also_a_room_keeps_its_own_schedule(self):
        self.schedules[self.nefit] = self.schedules[self.living]
        self.settings["boiler_rooms"] = [self.living, self.bed, self.nefit]
        self.states[self.bed].attributes["current_temperature"] = 18
        await self.mgr._maybe_control_boiler()
        self.assertEqual(self.temps(self.nefit), [22.0])  # 20 measured + 2 offset
        # Our own write must not be mistaken for a manual change of the living room.
        self.mgr._desired_for = lambda *a, **k: 20
        await self.mgr._remember_manual_target(self.nefit, 22.0)
        self.assertNotIn(self.nefit, self.settings["room_pause"])
        self.states[self.bed].attributes["current_temperature"] = 21
        await self.mgr._maybe_control_boiler()
        self.assertEqual(self.temps(self.nefit)[-1], 20)  # back to the living room schedule, not idle

    async def test_disabling_boiler_control_forgets_the_raise(self):
        self.settings["boiler_enabled"] = False
        self.mgr._boiler_raised_c[self.nefit] = 22.5
        await self.mgr._maybe_control_boiler()
        self.assertEqual(self.mgr._boiler_raised_c, {})

    async def test_boiler_never_exceeds_its_max(self):
        self.states[self.nefit].attributes["current_temperature"] = 23.5
        self.states[self.bed].attributes["current_temperature"] = 18
        await self.mgr._maybe_control_boiler()
        self.assertEqual(self.temps(self.nefit), [24.0])

    async def test_switch_boilers_still_work(self):
        self.settings["boiler_switch"] = "switch.boiler"
        self.states["switch.boiler"] = SimpleNamespace(state="off", attributes={})
        self.states[self.bed].attributes["current_temperature"] = 18
        await self.mgr._maybe_control_boiler()
        self.assertIn(("switch", "turn_on", {"entity_id": "switch.boiler"}), self.calls)


if __name__ == "__main__":
    unittest.main()
