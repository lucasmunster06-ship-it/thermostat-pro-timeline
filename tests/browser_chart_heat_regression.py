"""Run with Playwright + Chromium; room chart from recorder history and the heat-source settings window."""
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(ROOT)))
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1000, 'height': 800})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}')
        source = (ROOT / 'thermostat-pro-timeline.js').read_text(encoding='utf-8')
        page.add_script_tag(content=source.replace('import.meta.url', '"http://localhost/card.js"'))
        result = page.evaluate('''async () => {
          const card = document.createElement('thermostat-timeline-card');
          card._loadStore = async () => {}; card._hydrateUiNamesFromBackend = async () => {};
          card._debouncedSaveStore = () => {}; card._startStoreWatchTimer = () => {};
          card._apiFetchState = async () => ({heat_status: {rooms: {'climate.living': {source: 'alt', active: 'climate.eurom'}}}});
          card.setConfig({entities:['climate.living'], auto_apply:false, storage_enabled:true,
                          boiler_enabled:true, boiler_switch:'climate.nefit', merges:{'climate.living':['climate.bed_2']}});
          document.body.replaceChildren(card);
          const calls = [], ws = [];
          card.hass = {states:{
              'climate.living':{state:'heat',attributes:{temperature:21,current_temperature:20}},
              'climate.eurom':{state:'heat',attributes:{friendly_name:'Eurom'}},
              'sensor.outdoor':{state:'3',attributes:{unit_of_measurement:'°C', friendly_name:'Outdoor temperature'}},
              'sensor.price':{state:'24',attributes:{unit_of_measurement:'ct/kWh'}}},
            locale:{language:'nl'}, language:'nl', user:{is_admin:true}, config:{unit_system:{temperature:'°C'}},
            callService: async (d, s, data) => { calls.push([d, s, data]); },
            callWS: async (msg) => { ws.push(msg); const t = Date.now()/1000;
              return {'climate.living': [{s:'heat', a:{current_temperature:19, temperature:20, hvac_action:'heating'}, lu:t-7200},
                                         {s:'heat', a:{current_temperature:20.5, temperature:20, hvac_action:'idle'}, lu:t-3600}],
                      'climate.nefit': [{s:'heat', a:{temperature:22, current_temperature:20, hvac_action:'heating'}, lu:t-7000}],
                      'sensor.outdoor': [{s:'2', a:{unit_of_measurement:'°C'}, lu:t-7000}]}; }};
          await new Promise(r => setTimeout(r, 0));
          card._schedules['climate.living'] = {defaultTemp:17,blocks:[{id:'a',startMin:0,endMin:1440,temp:20}]};

          card._config.heat_sources = {enabled: true, gas_price: {mode:'fixed', value: 1.3}, power_price: {mode:'entity', entity:'sensor.price'},
            outdoor_sensor: 'sensor.outdoor', rooms: {'climate.living': [{entity:'climate.eurom', kind:'heat_pump', cop:[[-7,2],[2,3],[7,4],[12,5]]}]}};
          card._render();
          const out = {chartBtn: !!card.shadowRoot.querySelector('.row-chart-btn')};
          card._openChart('climate.living');
          await new Promise(r => setTimeout(r, 100));
          const sr = card.shadowRoot;
          out.ws = ws[0];
          out.paths = [...sr.querySelectorAll('.chart-svg path.ln')].filter(p => (p.getAttribute('d') || '').length > 5).map(p => p.getAttribute('class'));
          out.lanes = [...sr.querySelectorAll('.chart-svg .lane-t')].map(t => t.textContent);
          out.foot = sr.querySelector('.chart-foot').textContent;
          card._openHeat();
          out.summary = sr.querySelector('.heat-summary').innerText;
          await card._saveHeat();
          out.calls = calls;
          return out;
        }''')
        print(result)
        assert result['chartBtn']
        assert result['ws']['type'] == 'history/history_during_period'
        assert set(result['ws']['entity_ids']) >= {'climate.living', 'climate.bed_2', 'climate.nefit', 'sensor.outdoor', 'climate.eurom'}
        assert {'ln meas', 'ln setp', 'ln plan', 'ln outd'} <= set(result['paths']), result['paths']
        assert result['lanes'][:2] == ['Verwarmen', 'Ketel'] and 'Eurom' in result['lanes']
        assert 'Eurom' in result['foot']
        assert '€ 0,148' in result['summary'] and '€ 0,075' in result['summary'], result['summary']
        assert result['calls'][0][1] == 'set_store' and result['calls'][0][2]['settings']['heat_sources']['enabled']
        assert not errors, errors
        print('PASS: chart and heat sources')
        browser.close()
finally:
    server.shutdown()
