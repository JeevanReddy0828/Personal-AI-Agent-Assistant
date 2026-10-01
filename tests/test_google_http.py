from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit, urlencode
from unittest.mock import patch
from laptop_agent.failures import FAILURES
from laptop_agent.google_oidc import GoogleFlows
import test_webui_auth as auth_tests
from test_webui_auth import GOOD
from test_google_oidc import google_failures, jwt


class GoogleHTTPTests(unittest.TestCase):
    setUpClass = classmethod(auth_tests.SignInTests.setUpClass.__func__)
    tearDownClass = classmethod(auth_tests.SignInTests.tearDownClass.__func__)
    call = auth_tests.SignInTests.call
    sign_in = auth_tests.SignInTests.sign_in

    def setUp(self):
        auth_tests.SignInTests.setUp(self)
        self.google=GoogleFlows("client","secret",transport=self.exchange,wall=lambda:1000)
        patcher=patch.object(self.webui,"_GOOGLE",self.google)
        patcher.start();self.addCleanup(patcher.stop)
        self.sub="subject-a"
        self.query={}
        self.exchanges=[]

    def exchange(self,fields):
        self.exchanges.append(fields)
        return {"id_token":jwt(self.query["nonce"][0],sub=self.sub)}

    def start_google(self,cookie="",purpose="signin",current=GOOD,native=False):
        status,body,headers=self.call("POST","/auth/google/start",dict(purpose=purpose,current=current,native=native),cookie=cookie,token=False)
        self.assertEqual(status,200,body)
        proof=headers["Set-Cookie"].split(";",1)[0]
        self.assertIn("HttpOnly",headers["Set-Cookie"])
        self.assertIn("SameSite=Strict",headers["Set-Cookie"])
        return body,proof

    def launch(self,url):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                return None
        try:
            urllib.request.build_opener(NoRedirect).open(url,timeout=5)
        except urllib.error.HTTPError as response:
            self.assertEqual(response.code,303)
            self.query=parse_qs(urlsplit(response.headers["Location"]).query)
            self.assertIn("SameSite=Lax",response.headers["Set-Cookie"])
            return response.headers["Set-Cookie"].split(";",1)[0]
        self.fail("launch should redirect")

    def callback(self,binding,**changes):
        fields={"state":self.query["state"][0],"code":"private-code",**changes}
        return self.call("GET","/auth/google/callback?"+urlencode(fields),cookie=binding,token=False,
                         headers={"Sec-Fetch-Site":"cross-site"})

    def finish(self,body,proof,session=""):
        return self.call("POST","/auth/google/complete",{"flow":body["flow"]},cookie='; '.join(filter(None,[session,proof])),token=False)

    def owner(self):
        return self.accounts.create("jeevan","dev",GOOD)

    def test_google_first_link_then_signin_preserves_role_and_rotates_sessions(self):
        self.owner()
        personal=self.accounts.create("family","personal",GOOD)
        session=self.sign_in("family")
        other=self.sign_in("family")
        body,proof=self.start_google(session,"link")
        binding=self.launch(body["launch"])
        self.assertEqual(self.finish(body,proof,session)[0],202)
        status,page,headers=self.callback(binding)
        self.assertEqual(status,200,page)
        self.assertNotIn("jarvis_session",headers.get("Set-Cookie",""))
        self.assertNotIn("private-code",page)
        done=self.finish(body,proof,session)
        self.assertEqual(done[0],200,done[1])
        self.assertEqual(done[1]["user"]["role"],"personal")
        self.assertEqual(self.accounts.get(personal.id).google_sub,self.sub)
        self.assertIsNone(self.sessions.resolve(other.split("=",1)[1]))
        self.assertEqual(self.call("GET","/api/me",cookie=session)[0],401)
        body,proof=self.start_google()
        binding=self.launch(body["launch"])
        self.callback(binding)
        status,data,headers=self.finish(body,proof)
        self.assertEqual(status,200,data)
        self.assertEqual(data["user"],{"username":"family","role":"personal"})
        self.assertEqual(self.sessions.resolve(headers["Set-Cookie"].split(";",1)[0].split("=",1)[1]).method,"google")

    def test_google_cannot_register_or_match_on_email(self):
        self.owner()
        body,proof=self.start_google()
        self.callback(self.launch(body["launch"]))
        result=self.finish(body,proof)
        self.assertEqual(result[0],400)
        self.assertIn("not linked",result[1]["message"])
        self.assertEqual(len(self.accounts.list()),1)
        self.assertNotIn("Set-Cookie",result[2])

    def test_google_stepup_throttle_and_google_only_recovery(self):
        account=self.owner();session=self.sign_in()
        for _ in range(5):
            self.assertEqual(self.call("POST","/auth/google/start",{"purpose":"link","current":"wrong"},cookie=session,token=False)[0],403)
        self.assertEqual(self.call("POST","/auth/google/start",{"purpose":"link","current":GOOD},cookie=session,token=False)[0],429)
        self.assertFalse(self.google._flows)
        only=self.accounts.create("onlygoogle","personal")
        self.accounts.link_google(only.id,self.sub,"a@example.test")
        token=self.sessions.create(only.id,"google")
        result=self.call("POST","/auth/google/start",{"purpose":"link","current":GOOD},cookie="jarvis_session="+token,token=False)
        self.assertEqual(result[0],409)
        self.assertIn("accounts password onlygoogle",result[1]["message"])

    def test_google_pending_link_rejects_logout_disable_password_role_or_link_change(self):
        for change in ("logout","disable","password","role","link"):
            with self.subTest(change=change):
                account=self.accounts.create("person"+change,"personal",GOOD)
                session=self.sign_in(account.username)
                body,proof=self.start_google(session,"link")
                self.callback(self.launch(body["launch"]))
                if change=="logout":self.sessions.revoke(session.split("=",1)[1])
                elif change=="disable":self.accounts.set_disabled(account.id,True)
                elif change=="password":self.accounts.set_password(account.id,"another good password")
                elif change=="role":self.accounts.set_role(account.id,"dev")
                else:self.accounts.link_google(account.id,"another-subject",None)
                result=self.finish(body,proof,session)
                self.assertEqual(result[0],400,result[1])
                self.assertNotEqual(self.accounts.get(account.id).google_sub,self.sub)

    def test_google_duplicate_subject_cannot_relink_another_account(self):
        owner=self.owner();self.accounts.link_google(owner.id,self.sub,None)
        person=self.accounts.create("family","personal",GOOD);session=self.sign_in("family")
        body,proof=self.start_google(session,"link")
        self.callback(self.launch(body["launch"]))
        result=self.finish(body,proof,session)
        self.assertEqual(result[0],400)
        self.assertIn("already linked",result[1]["message"])
        self.assertIsNone(self.accounts.get(person.id).google_sub)

    def test_google_unlink_requires_password_revokes_sessions_and_prevents_pending_login(self):
        owner=self.owner();self.accounts.link_google(owner.id,self.sub,None)
        body,proof=self.start_google();self.callback(self.launch(body["launch"]))
        session=self.sign_in();other=self.sign_in()
        self.assertEqual(self.call("POST","/auth/google/unlink",{},cookie=session,token=False)[0],403)
        done=self.call("POST","/auth/google/unlink",{"current":GOOD},cookie=session,token=False)
        self.assertEqual(done[0],200,done[1])
        self.assertIsNone(self.accounts.get(owner.id).google_sub)
        self.assertIsNone(self.sessions.resolve(other.split("=",1)[1]))
        self.assertEqual(self.finish(body,proof)[0],400)
        self.assertEqual(self.call("GET","/api/me",cookie=done[2]["Set-Cookie"].split(";",1)[0])[0],200)

    def test_google_native_finishes_only_in_original_cookie_jar(self):
        owner=self.owner();self.accounts.link_google(owner.id,self.sub,None)
        with patch.object(self.webui,"_DESKTOP_MODE",True), patch.object(self.webui.webbrowser,"open",return_value=True) as opened:
            body,proof=self.start_google(native=True)
        self.assertIsNone(body["launch"])
        binding=self.launch(opened.call_args.args[0])
        self.callback(binding)
        self.assertEqual(self.finish(body,binding)[0],400)
        self.assertEqual(self.finish(body,"")[0],400)
        self.assertEqual(self.finish(body,proof)[0],200)
        self.assertEqual(self.finish(body,proof)[0],400)

    def test_google_cross_site_start_and_remote_callback_are_refused(self):
        self.owner()
        for headers in ({"Origin":"https://evil.example"},{"Sec-Fetch-Site":"cross-site"}):
            self.assertEqual(self.call("POST","/auth/google/start",{},headers=headers,token=False)[0],403)
        with patch.object(self.webui.Handler,"_client_is_local",lambda _:False):
            for method,path in (("POST","/auth/google/start"),("GET","/auth/google/status"),("GET","/auth/google/callback?state=x")):
                result=self.call(method,path,{} if method=="POST" else None,token=False)
                self.assertEqual(result[0],403)
                self.assertIn("phone",result[1]["message"])
        self.assertFalse(self.google._flows)

    def test_google_missing_binding_duplicate_query_provider_error_and_no_config(self):
        self.owner()
        body,proof=self.start_google();binding=self.launch(body["launch"])
        self.assertEqual(self.callback("")[0],400)
        self.assertFalse(self.exchanges)
        duplicate='/auth/google/callback?state='+self.query['state'][0]+'&state=other&code=c'
        self.assertEqual(self.call("GET",duplicate,cookie=binding,token=False)[0],400)
        self.callback(binding,error="access_denied")
        result=self.finish(body,proof)
        self.assertEqual(result[0],400)
        self.assertIn("cancelled",result[1]["message"])
        self.google.client_id=""
        result=self.call("POST","/auth/google/start",{},token=False)
        self.assertEqual(result[0],400)
        self.assertIn("GOOGLE_CLIENT_ID",result[1]["message"])

    def test_google_old_cancel_cannot_clear_a_new_window_proof(self):
        self.owner()
        first,old_proof=self.start_google()
        second,new_proof=self.start_google()
        result=self.call("POST","/auth/google/cancel",{"flow":first["flow"]},cookie=new_proof,token=False)
        self.assertEqual(result[0],200)
        self.assertNotIn("Set-Cookie",result[2])
        self.assertEqual(self.finish(second,new_proof)[0],202)

    def test_google_native_open_failure_leaves_no_flow(self):
        self.owner()
        with patch.object(self.webui,"_DESKTOP_MODE",True), patch.object(self.webui.webbrowser,"open",return_value=False):
            result=self.call("POST","/auth/google/start",{"native":True},token=False)
        self.assertEqual(result[0],400)
        self.assertIn("system browser",result[1]["message"])
        self.assertFalse(self.google._flows)

    def test_google_disabled_or_deleted_identity_cannot_finish_signin(self):
        for action in ("disabled","deleted"):
            account=self.accounts.create(action,"personal",GOOD)
            self.accounts.link_google(account.id,self.sub,None)
            body,proof=self.start_google();self.callback(self.launch(body["launch"]))
            if action=="disabled":
                self.accounts.set_disabled(account.id,True)
            else:self.accounts.delete(account.id)
            self.assertEqual(self.finish(body,proof)[0],400)
            if action=="disabled":self.accounts.delete(account.id)

    def test_a_session_from_older_credentials_is_signed_out_here_too(self):
        # Every other route refuses a session granted before a password change (#148); the
        # Google routes kept their own copy of "signed in", without the epoch.
        owner=self.owner();self.accounts.link_google(owner.id,self.sub,"owner@example.test")
        stale=self.sessions.create(owner.id,"password",epoch=owner.epoch)
        self.accounts.set_password(owner.id,"another good password")
        cookie="jarvis_session="+stale
        self.assertEqual(self.call("GET","/api/me",cookie=cookie)[0],401)
        status=self.call("GET","/auth/google/status",cookie=cookie,token=False)
        self.assertEqual((status[0],status[1]["linked"],status[1]["email"]),(200,False,None))
        body,proof=self.start_google(cookie)
        self.callback(self.launch(body["launch"]))
        done=self.finish(body,proof,cookie)
        self.assertEqual(done[0],200,done[1])
        self.assertIsNone(self.sessions.resolve(stale))
        self.assertEqual(self.call("GET","/api/me",cookie=done[2]["Set-Cookie"].split(";",1)[0])[0],200)

    def test_a_browser_that_will_not_open_is_recorded(self):
        self.owner();FAILURES.clear()
        with patch.object(self.webui,"_DESKTOP_MODE",True), patch.object(self.webui.webbrowser,"open",side_effect=OSError("ticket=private")):
            result=self.call("POST","/auth/google/start",{"native":True},token=False)
        self.assertEqual(result[0],400)
        recorded=google_failures()
        self.assertEqual([entry["where"] for entry in recorded],["google/browser"])
        self.assertNotIn("private",json.dumps(recorded))
        self.assertFalse(self.google._flows)
