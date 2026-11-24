"""Cheapest heat source per room, without a Home Assistant installation."""

import ast
import math
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


def load():
    path = Path(__file__).resolve().parents[1] / "custom_components/thermostat_timeline/heat_source.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.Assign))]

    def numeric(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    def celsius(value, unit):
        value = numeric(value)
        if value is None:
            return None
        return (value - 32) / 1.8 if unit == "°F" else value

    import logging
    scope = {"numeric": numeric, "celsius": celsius, "callback": lambda f: f, "time": time, "logging": logging,
             "_LOGGER": Mock(), "DOMAIN": "thermostat_timeline"}
    exec(compile(tree, str(path), "exec"), scope)
    return scope


class PureTests(unittest.TestCase):
    def setUp(self):
        self.s = load()

    def test_cop_curve_interpolates_and_clamps(self):
        cop = self.s["cop_at"]
        pts = [[-7, 2], [7, 4]]
        self.assertEqual(cop(pts, -20), 2)
        self.assertEqual(cop(pts, 0), 3)
        self.assertEqual(cop(pts, 30), 4)
        self.assertEqual(cop(None, 7), 3.8)  # default curve

    def test_costs(self):
        self.assertAlmostEqual(self.s["gas_heat_cost"](1.30, 9.77, 0.9), 0.1478, places=4)
        cost, cop = self.s["device_heat_cost"](0.25, {"kind": "heat_pump", "cop": [[0, 2.5], [10, 4.5]]}, 5)
        self.assertAlmostEqual(cop, 3.5)
        self.assertAlmostEqual(cost, 0.25 / 3.5)
        self.assertEqual(self.s["device_heat_cost"](0.25, {"kind": "electric"}, 5), (0.25, 1.0))

    def test_choose_with_margin_and_limits(self):
        choose = self.s["choose"]
        dev = {"kind": "heat_pump", "min_outdoor": -10}
        self.assertEqual(choose(dev, 0.15, 0.07, 5, "gas")[0], "alt")
        self.assertEqual(choose(dev, 0.15, 0.146, 5, "gas")[0], "gas")   # not 5 % cheaper
        self.assertEqual(choose(dev, 0.15, 0.146, 5, "alt")[0], "alt")   # hysteresis keeps it
        self.assertEqual(choose(dev, 0.15, 0.07, -12, "alt"), ("gas", "too_cold"))
        self.assertEqual(choose({**dev, "mode": "never"}, 0.15, 0.01, 5, "gas")[0], "gas")
        self.assertEqual(choose({**dev, "mode": "always"}, 0.01, 0.5, 5, "gas")[0], "alt")


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.s = load()
        self.states = {
            "climate.living": SimpleNamespace(state="heat", attributes={"current_temperature": 19.0, "temperature": 20}),
            "climate.eurom": SimpleNamespace(state="off", attributes={"hvac_modes": ["off", "heat", "cool"], "temperature": 18,
                                                                     "min_temp": 16, "max_temp": 30}),
            "switch.kachel": SimpleNamespace(state="off", attributes={}),
            "sensor.power": SimpleNamespace(state="25", attributes={"unit_of_measurement": "ct/kWh"}),
            "sensor.outdoor": SimpleNamespace(state="5", attributes={"unit_of_measurement": "°C"}),
        }
        self.calls = []

        async def call(domain, service, data, blocking=False):
            self.calls.append((domain, service, dict(data)))
            st = self.states[data["entity_id"]]
            if service == "set_hvac_mode":
                st.state = data["hvac_mode"]
            elif service == "set_temperature":
                st.attributes["temperature"] = data["temperature"]
            elif service in ("turn_on", "turn_off"):
                st.state = "on" if service == "turn_on" else "off"

        self.hass = SimpleNamespace(states=SimpleNamespace(get=self.states.get),
                                    services=SimpleNamespace(async_call=call),
                                    config=SimpleNamespace(units=SimpleNamespace(temperature_unit="°C")))
        self.settings = {"heat_sources": {
            "enabled": True, "gas_price": {"mode": "fixed", "value": 1.30},
            "power_price": {"mode": "entity", "entity": "sensor.power"}, "outdoor_sensor": "sensor.outdoor",
            "rooms": {"climate.living": [{"entity": "climate.eurom", "kind": "heat_pump", "cop": [[0, 2.5], [10, 4.5]]}]},
        }}
        self.schedules = {"climate.living": {}}
        self.mgr = SimpleNamespace(
            _get_data=lambda: (self.schedules, self.settings), _now_min=lambda: 600,
            _room_target_c=lambda *a: 21.0, _block_mode=lambda *a: None, _summer_off=lambda *a: False,
            _apply_room_now=AsyncMock(), _maybe_control_boiler=AsyncMock())
        self.hs = self.s["HeatSources"](self.hass, Mock(), SimpleNamespace(_managers={"default": self.mgr}))

    async def test_heat_pump_takes_over_when_cheaper_and_radiators_back_off(self):
        await self.hs.update("default", self.mgr)
        st = self.hs.status["default"]
        self.assertAlmostEqual(st["prices"]["power_kwh"], 0.25)
        room = st["rooms"]["climate.living"]
        self.assertEqual(room["source"], "alt")
        self.assertEqual(room["active"], "climate.eurom")
        self.assertEqual(self.states["climate.eurom"].state, "heat")
        self.assertEqual(self.states["climate.eurom"].attributes["temperature"], 21.0)
        self.assertEqual(self.hs.backup_offset_c("default", "climate.living"), 2.0)
        self.mgr._apply_room_now.assert_awaited_once_with("climate.living")
        self.mgr._maybe_control_boiler.assert_awaited()

        # Gas gets much cheaper, but the dwell time prevents flapping...
        self.settings["heat_sources"]["gas_price"]["value"] = 0.30
        await self.hs.update("default", self.mgr)
        self.assertEqual(self.hs.status["default"]["rooms"]["climate.living"]["source"], "alt")
        # ...until it has passed.
        self.hs.choice["default/climate.living"]["since"] -= 3600
        await self.hs.update("default", self.mgr)
        self.assertEqual(self.hs.status["default"]["rooms"]["climate.living"]["source"], "gas")
        self.assertEqual(self.states["climate.eurom"].state, "off")
        self.assertEqual(self.hs.backup_offset_c("default", "climate.living"), 0.0)

    async def test_electric_heater_switches_on_room_temperature(self):
        self.settings["heat_sources"]["rooms"]["climate.living"] = [{"entity": "switch.kachel", "kind": "electric"}]
        self.settings["heat_sources"]["power_price"] = {"mode": "fixed", "value": 0.10}
        await self.hs.update("default", self.mgr)
        self.assertEqual(self.states["switch.kachel"].state, "on")  # 19 < 21 - 0.3
        self.states["climate.living"].attributes["current_temperature"] = 21.3
        await self.hs.update("default", self.mgr)
        self.assertEqual(self.states["switch.kachel"].state, "off")

    async def test_disabled_leaves_devices_alone(self):
        self.settings["heat_sources"]["enabled"] = False
        await self.hs.update("default", self.mgr)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.hs.backup_offset_c("default", "climate.living"), 0.0)


if __name__ == "__main__":
    unittest.main()
