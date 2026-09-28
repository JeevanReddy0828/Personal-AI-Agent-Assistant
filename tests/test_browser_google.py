"""Real browser cookie/redirect behavior, with Google transport entirely faked."""
from __future__ import annotations

import os
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlencode
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.accounts import AccountStore
from laptop_agent.sessions import SessionStore
from laptop_agent.google_oidc import GoogleFlows
from test_google_oidc import jwt


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS")=="1", "Opt-in Chromium checks")
class GoogleInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),webui.Handler)
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
        cls.url=f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright=sync_playwright().start()
        cls.browser=cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.playwright.stop()
        cls.server.shutdown();cls.server.server_close()

    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.accounts=AccountStore(Path(tmp.name)/"accounts.json",cost=(2**10,8,1))
        self.sessions=SessionStore(Path(tmp.name)/"sessions.json")
        self.owner=self.accounts.create("jeevan","dev","correct horse battery")
        self.person=self.accounts.create("family","personal","correct horse battery")
        self.nonce="";self.subject="subject-a";self.exchanges=[]
        self.google=GoogleFlows("client","secret",transport=self.exchange,wall=lambda:1000)
        self.opened=[]
        for name,value in (("ACCOUNTS",self.accounts),("SESSIONS",self.sessions),("_GOOGLE",self.google),
                           ("_SIGNIN_LIMIT",webui._SignInLimit()),("_DESKTOP_MODE",False)):
            patcher=patch.object(webui,name,value);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(webui.webbrowser,"open",side_effect=lambda url:self.opened.append(url) or True)
        patcher.start();self.addCleanup(patcher.stop)

    def exchange(self,fields):
        self.exchanges.append(fields)
        return {"id_token":jwt(self.nonce,sub=self.subject)}

    def context(self,account=None):
        context=self.browser.new_context(viewport={"width":1280,"height":1000},reduced_motion="reduce")
        self.addCleanup(context.close)
        def route(request):
            url=request.request.url
            if url.startswith(self.url+"/auth/google/launch?"):
                # Playwright routes only the first request in a redirect chain. Turn the
                # tested HTTP 303 into a navigation so the fake Google origin is intercepted.
                response=request.fetch(max_redirects=0)
                self.assertEqual(response.status,303)
                headers=dict(response.headers)
                target=headers.pop("location")
                headers.pop("content-length",None)
                headers["content-type"]="text/html"
                request.fulfill(status=200,headers=headers,body='<script nonce="'+webui._SCRIPT_NONCE+'">location.href='+json.dumps(target)+'</script>')
            elif url.startswith(self.url):request.continue_()
            elif url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"):
                q=parse_qs(urlsplit(url).query);self.nonce=q["nonce"][0]
                target=self.url+"/auth/google/callback?"+urlencode({"state":q["state"][0],"code":"fake-code"})
                request.fulfill(status=200,headers={"Content-Type":"text/html", "Cross-Origin-Opener-Policy":"same-origin"},body='<script>location.href='+json.dumps(target)+'</script>')
            else:request.abort()
        context.route("**/*",route)
        if account:
            token=self.sessions.create(account.id,"password")
            context.add_cookies([{"name":"jarvis_session","value":token,"url":self.url}])
        return context

    def page(self,context,query=""):
        page=context.new_page();errors=[]
        page.on("pageerror",lambda e:errors.append(str(e)))
        self.addCleanup(lambda:self.assertEqual(errors,[]))
        page.goto(self.url+query)
        return page

    def settings(self,page):
        page.wait_for_function("() => !!document.body.dataset.role",timeout=6000)
        page.click("#hudBtn");page.click("#acctGoogle")
        page.wait_for_function("() => !!document.getElementById('acctGoogleInfo').textContent")
        box=page.locator("#acctGoogleForm").bounding_box()
        self.assertIsNotNone(box)
        self.assertGreater(box["width"],0)
        self.assertGreater(box["height"],0)

    def test_personal_can_link_then_use_google_without_gmail_scopes(self):
        context=self.context(self.person);page=self.page(context)
        self.settings(page)
        page.fill("#acctGoogleCur","correct horse battery")
        with page.expect_event("load",timeout=12000):page.click("#acctGoogleLink")
        self.assertEqual(self.accounts.get(self.person.id).google_sub,self.subject)
        page.wait_for_function("() => document.body.dataset.role === 'personal'")
        self.settings(page)
        self.assertIn("a@example.test",page.inner_text("#acctGoogleInfo"))
        self.assertTrue(page.locator("#acctGoogleUnlink").is_visible())
        self.assertFalse(page.locator("#acctGoogleLink").is_visible())
        page.click("#acctOut")
        page.wait_for_selector("#google")
        page.wait_for_function("() => !document.getElementById('google').disabled")
        with page.expect_event("load",timeout=12000):page.click("#google")
        page.wait_for_function("() => document.body.dataset.role === 'personal'")
        self.assertEqual(len(self.exchanges),2)
        self.assertEqual(page.evaluate("() => Object.keys(localStorage).filter(k => /token|google/i.test(k))"),[])

    def test_native_browser_callback_never_gets_the_window_session(self):
        self.accounts.link_google(self.person.id,self.subject,None)
        original=self.context();page=self.page(original,"/?app=1")
        webui._DESKTOP_MODE=True
        page.wait_for_function("() => !document.getElementById('google').disabled")
        page.click("#google")
        page.wait_for_function("() => document.getElementById('googleNote').textContent.includes('Finish with Google')")
        self.assertEqual(len(self.opened),1)
        self.assertFalse(any(c['name']=='jarvis_session' for c in original.cookies()))
        external=self.context();browser=external.new_page();browser.goto(self.opened[0])
        page.wait_for_function("() => document.body.dataset.role === 'personal'",timeout=12000)
        self.assertFalse(any(c['name']=='jarvis_session' for c in external.cookies()))
        self.assertTrue(any(c['name']=='jarvis_session' for c in original.cookies()))
        self.assertNotIn("fake-code",browser.url)

    def test_unlinked_identity_has_actionable_error_and_no_session(self):
        context=self.context();page=self.page(context)
        page.wait_for_function("() => !document.getElementById('google').disabled")
        page.click("#google")
        page.wait_for_function("() => document.getElementById('googleNote').textContent.includes('not linked')",timeout=10000)
        self.assertFalse(any(c['name']=='jarvis_session' for c in context.cookies()))
        self.assertTrue(page.locator("#google").is_enabled())
        self.assertTrue(page.locator("#googleNote").is_visible())

    def test_no_configuration_and_phone_show_password_fallback(self):
        self.google.client_id=""
        page=self.page(self.context())
        page.wait_for_function("() => document.getElementById('googleNote').textContent.includes('not configured')")
        self.assertTrue(page.locator("#google").is_disabled())
        self.assertTrue(page.locator("#p").is_visible())
        with patch.object(webui.Handler,"_client_is_local",lambda _:False):
            phone=self.page(self.context())
            phone.wait_for_function("() => document.getElementById('googleNote').textContent.includes('phone')")
            self.assertTrue(phone.locator("#google").is_disabled())
            self.assertTrue(phone.locator("#p").is_visible())

    def test_native_cancel_invalidates_pending_flow(self):
        webui._DESKTOP_MODE=True
        page=self.page(self.context(),"/?app=1")
        page.wait_for_function("() => !document.getElementById('google').disabled")
        page.click("#google")
        page.wait_for_function("() => document.getElementById('googleNote').textContent.includes('Finish with Google')")
        page.click("#googleCancel")
        page.wait_for_function("() => document.getElementById('googleNote').textContent.includes('cancelled')",timeout=6000)
        self.assertEqual(len(self.google._flows),0)
        self.assertTrue(page.locator("#google").is_enabled())
