"""Run with Playwright + Chromium; boost buttons/modal and climate boiler settings in the card."""
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
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{server.server_port}')
        source = (ROOT / 'thermostat-pro-timeline.js').read_text(encoding='utf-8')
        page.add_script_tag(content=source.replace('import.meta.url', '"http://localhost/card.js"'))
        result = page.evaluate('''async () => {
          const card = document.createElement('thermostat-timeline-card');
          card._loadStore = async () => {};
          card._hydrateUiNamesFromBackend = async () => {};
          card._debouncedSaveStore = () => {};
          card._startStoreWatchTimer = () => {};
          card.setConfig({entities:['climate.living','climate.bed'], auto_apply:false, storage_enabled:true});
          document.body.replaceChildren(card);
          const calls = [];
          card.hass = {states:{
              'climate.living':{state:'heat',attributes:{temperature:21,current_temperature:20}},
              'climate.bed':{state:'heat',attributes:{temperature:22,current_temperature:19}}},
            locale:{language:'nl'}, language:'nl', user:{is_admin:true}, config:{unit_system:{temperature:'°C'}},
            callService: async (d, s, data) => { calls.push([d, s, data]); }};
          await new Promise(r => setTimeout(r, 0));
          for (const e of ['climate.living','climate.bed'])
            card._schedules[e] = {defaultTemp:18,blocks:[{id:'b',startMin:0,endMin:1440,temp:20}]};
          card._config.room_pause = {'climate.bed': {indef:false, until_ms: Date.now() + 3600e3, source:'boost', target_c: 22}};
          card._render();
          const btns = [...card.shadowRoot.querySelectorAll('.row-boost-btn')];
          const out = {buttons: btns.length, active: btns.filter(b => b.classList.contains('active')).length};
          out.pausedShown = card.shadowRoot.querySelectorAll('.row-pause-btn.paused').length;
          // open room boost for the bedroom (second row)
          btns[1].click();
          const sr = card.shadowRoot;
          out.open = sr.querySelector('.overlay-boost').classList.contains('open');
          out.pauseOpen = sr.querySelector('.overlay-pause').classList.contains('open');
          out.status = sr.querySelector('.boost-active-text').textContent;
          out.stopVisible = sr.querySelector('.boost-active').style.display !== 'none';
          out.deltaOptions = sr.querySelectorAll('.boost-deltas .chip').length;
          out.preview = sr.querySelector('.boost-preview').textContent;
          sr.querySelectorAll('.boost-deltas .chip')[3].click();   // +2
          sr.querySelectorAll('.boost-durations .chip')[2].click(); // 1 uur
          out.startLabel = sr.querySelector('.boost-start').textContent;
          await card._startBoost(card._boostTargetEid);
          // global
          card._openBoost(null);
          out.globalStatus = sr.querySelector('.boost-active-text').textContent;
          out.globalTitle = sr.querySelector('.boost-title').textContent;
          await card._stopBoost(null);
          out.calls = calls;
          out.dom = [ttBoilerDom('climate'), ttBoilerDom('input_boolean'), ttBoilerDom('x')];
          return out;
        }''')
        print(result)
        assert result['buttons'] == 2 and result['active'] == 1 and result['pausedShown'] == 0
        assert result['open'] and not result['pauseOpen']
        assert 'Boost naar 22' in result['status'] and result['stopVisible']
        assert result['deltaOptions'] == 6 and result['startLabel'] == 'Boost vervangen'
        assert '20 °C' in result['preview'] and '21 °C' in result['preview'], result['preview']
        assert result['globalTitle'] == 'Boost alle ruimtes'
        assert result['calls'][0] == ['thermostat_timeline', 'boost', {'delta': 2, 'duration_minutes': 60, 'entity_id': ['climate.bed'], 'instance_id': 'default'}], result['calls']
        assert result['calls'][1] == ['thermostat_timeline', 'cancel_boost', {'instance_id': 'default'}]
        assert '1 ruimte' in result['globalStatus']
        assert result['dom'] == ['climate', 'input_boolean', 'switch']
        assert not errors, errors
        print('PASS: boost buttons, modal and service calls')
        browser.close()
finally:
    server.shutdown()
