"""Opt-in exploratory run. Uses a throwaway store and port; no hosted API calls.

Run with a local non-loopback address: python -B docs/evals/isolated_app.py 192.168.x.x
The second client is an independent Chromium context on this host, not another device.
"""
from pathlib import Path
import base64
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import traceback
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
REPORT = Path(__file__).with_name('isolated_app_results.json')
LAN_IP = sys.argv[1]
results = []

def check(name, action):
    try:
        detail = action()
        results.append({'check': name, 'ok': True, 'detail': detail})
    except Exception:
        try:
            Path('exploration_failure_'+str(len(results))+'.log').write_text(page.locator('body').inner_text(),encoding='utf-8')
        except Exception:
            pass
        results.append({'check': name, 'ok': False, 'detail': traceback.format_exc()})
    print(name, results[-1]['ok'], flush=True)
    REPORT.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')

def wait_js(page, expression, arg=None):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if page.evaluate(expression, arg=arg):
                return
        except Exception as exc:
            if 'Execution context was destroyed' not in str(exc):
                raise
        page.wait_for_timeout(25)
    raise AssertionError('Condition timed out: ' + expression)

# No local .env, keys or production state; configuration is complete before webui imports.
for key in list(os.environ):
    if key.startswith(('LAPTOP_AGENT_', 'OPENAI_', 'OPENROUTER_', 'GOOGLE_', 'MICROSOFT_',
                       'SMTP_', 'IMAP_', 'JOBRIGHT_', 'OBSIDIAN_', 'SEARCH_', 'BRAVE_', 'SERPER_', 'SERPAPI_')):
        os.environ.pop(key, None)
import laptop_agent.config as config
config._load_dotenv = lambda *a, **kw: None
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory(prefix='jarvis_exploration_') as temporary:
    data_dir = Path(temporary) / 'data'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    passcode = secrets.token_urlsafe(24)
    os.environ.update(LAPTOP_AGENT_DATA_DIR=str(data_dir), LAPTOP_AGENT_PORT=str(port),
                      LAPTOP_AGENT_HOST='0.0.0.0', LAPTOP_AGENT_LAN_PASSCODE=passcode,
                      LAPTOP_AGENT_LLM_PROVIDER='heuristic')
    connect = socket.socket.connect
    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ('127.0.0.1', 'localhost', '::1', LAN_IP):
            raise OSError('External services are disabled in this exploration')
        return connect(sock, address)
    socket.socket.connect = local_connect
    import laptop_agent.webui as webui
    webui.UPLOAD_DIR = Path(temporary) / 'uploads'
    from laptop_agent.tools.imagegen import ImageTool
    assert webui.PORT == port and webui._orchestrator.data_dir == data_dir
    webui.system_metrics = lambda **kw: {'cpu_percent': 12, 'ram_percent': 30, 'gpus': []}
    calls = []
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
    def image_backend(model, body):
        calls.append(body['prompt'])
        return {'artifacts': [{'base64': png}]}
    webui._orchestrator._image_tool_cache = ImageTool(None, data_dir, backend=image_backend,
        approval_gate=webui._orchestrator.context.web.approval_gate)
    server = webui._Server(('0.0.0.0', port), webui.Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    local_url = f'http://127.0.0.1:{port}'
    lan_url = f'http://{LAN_IP}:{port}'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--no-proxy-server'])
            context = browser.new_context(viewport={'width': 1440, 'height': 950}, reduced_motion='reduce')
            context.route('**/*', lambda route: route.continue_() if route.request.url.startswith((local_url, lan_url)) else route.abort())
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(local_url)
            wait_js(page, "typeof send === 'function'")
            def images():
                for index, prompt in enumerate(('image a brass compass on a desk', 'draw me a picture of a green kite')):
                    page.evaluate('(s)=>{void send(s)}', prompt)
                    wait_js(page, '(n)=>document.querySelectorAll(".msg.bot img").length===n', arg=index+1)
                    wait_js(page, 'Array.from(document.querySelectorAll(".msg.bot img")).every(i=>i.complete&&i.naturalWidth>0)')
                    wait_js(page, '!busy')
                assert len(calls) == 2, calls
                assert len(list((data_dir/'images').glob('*'))) == 2
                def unavailable(model, body):
                    raise TimeoutError('fixture timeout')
                webui._orchestrator._image_tool_cache._backend = unavailable
                page.evaluate("void send('image a red cube')")
                wait_js(page, '!busy')
                assert 'did not respond' in page.locator('.msg.bot').last.inner_text()
                assert len(list((data_dir/'images').glob('*'))) == 2
                return {'prompts_received': calls, 'displayed': 2, 'backend_timeout_visible': True,
                        'medium_risk_uses_existing_auto_approval_policy': True}
            check('image routing, file serving, display and timeout (fixture backend)', images)
            def jobs():
                page.goto(local_url+'/#/jobs')
                wait_js(page, "document.body.dataset.view==='jobs'")
                page.locator('#jobCompany').fill('<img src=x onerror=window.__xss=1> Test Co')
                page.locator('#jobRole').fill('Python developer')
                page.locator('#jobAdd').click()
                wait_js(page, "document.querySelectorAll('#jobList .jobrow').length===1")
                assert page.locator('#jobList img').count() == 0
                options = page.locator('#jobList select option').all_text_contents()
                stage = 'interview' if 'interview' in options else options[2]
                page.locator('#jobList select').select_option(stage)
                wait_js(page, '(s)=>document.querySelector("#jobList select").value===s', arg=stage)
                page.reload()
                wait_js(page, '(s)=>document.querySelector("#jobList select")?.value===s', arg=stage)
                page.locator('#tlGo').click()
                assert 'Paste both' in page.locator('#tlMsg').inner_text()
                return {'saved_stage': stage, 'persisted_after_reload': True, 'company_escaped': True}
            check('Jobs add, stage, reload and empty tailoring validation', jobs)
            def pipeline():
                page.goto(local_url+'/#/pipeline')
                wait_js(page, "document.body.dataset.view==='pipeline' && profileLoaded")
                page.locator('#rsText').fill('Test Candidate\nPython developer with three years of SQL, Python and automated testing.\ntest@example.invalid')
                page.locator('#rsSave').click()
                wait_js(page, "document.getElementById('rsStat').textContent.includes('resume set')")
                page.get_by_text('Resume contact and certifications', exact=True).click()
                page.locator('#rsContact').fill('test@example.invalid')
                page.locator('#rsProfileSave').click()
                wait_js(page, "document.getElementById('pipeMsg').textContent.includes('profile saved')")
                page.reload()
                wait_js(page, 'profileLoaded')
                assert page.locator('#rsContact').input_value() == 'test@example.invalid'
                assert page.locator('#pipeBoard .pcard').count() == 1
                for width in (390, 1440):
                    page.set_viewport_size({'width': width, 'height': 950})
                    dims = page.evaluate('({w:innerWidth,s:document.documentElement.scrollWidth})')
                    assert dims['s'] <= dims['w'], dims
                page.once('dialog', lambda dialog: dialog.accept())
                page.locator('#clearBtn').click()
                wait_js(page, "document.getElementById('pipeMsg').textContent.includes('Cleared')")
                assert page.locator('#pipeBoard .pcard').count() == 1
                page.goto(local_url+'/#/jobs')
                page.locator('#jobList .rm').click()
                wait_js(page, "document.querySelectorAll('#jobList .jobrow').length===0")
                return {'resume_and_profile_persisted': True, 'clear_leads_keeps_application': True,
                        'widths_without_document_overflow': [390,1440], 'job_removed': True}
            check('Pipeline resume/profile persistence, mobile layout, clear leads, Jobs removal', pipeline)
            def lan():
                second = browser.new_context(viewport={'width':390,'height':844})
                second.route('**/*', lambda route: route.continue_() if route.request.url.startswith(lan_url) else route.abort())
                remote = second.new_page()
                remote.on('pageerror', lambda e: errors.append(str(e)))
                r = remote.goto(lan_url)
                assert r.status == 401, r.status
                assert second.request.get(lan_url+'/api/jobs').status == 401
                remote.locator('#p').fill('wrong-passcode')
                remote.get_by_role('button').click()
                wait_js(remote, "document.body.textContent.includes('Wrong passcode')")
                remote.locator('#p').fill(passcode)
                remote.get_by_role('button').click()
                wait_js(remote, "typeof send==='function'")
                me = remote.evaluate("async()=>await (await fetch('/api/me')).json()")
                assert me['local'] is False, me
                assert second.request.get(lan_url+'/api/jobs').status == 200
                assert remote.evaluate('isSecureContext') is False
                third = browser.new_context()
                assert third.request.get(lan_url+'/api/jobs').status == 401
                third.close()
                second.close()
                return {'unpaired_api':401,'wrong_passcode_denied':True,'paired_api':200,
                        'independent_third_client_denied':True,'server_saw_non_loopback':True,
                        'physical_second_device':False,'plain_http_secure_context':False}
            check('LAN passcode through non-loopback address with independent clients', lan)
            check('page JavaScript errors', lambda: (_ for _ in ()).throw(AssertionError(errors)) if errors else [])
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        socket.socket.connect = connect
sys.exit(0 if all(row['ok'] for row in results) else 1)
