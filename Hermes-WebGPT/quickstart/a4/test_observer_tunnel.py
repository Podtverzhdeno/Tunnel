"""A4 dedicated tunnel connection security tests. No network mutations."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from quickstart.a4.observer_tunnel import (
    ALIAS, TunnelConnectError, connect, decode_aliases, materialize_header,
    validate_input,
)


class TunnelOnboardingTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state=Path(tmp.name)
        self.cli=self.state/"fake-tunnel-client.exe"
        self.cli.write_bytes(b"fixture")
        self.token="A"*60
        self.id="tunnel_observer2026test0001"
        self.key="fake_key_never_show"
        self.calls=[]

    def safe_connect(self, **kwargs):
        def audit(port, token):
            self.assertEqual(port, 18766)
            self.assertEqual(token, self.token)
            return {"ok":True, "read_only":True, "tool_count":6}
        def execute(command, **kw):
            self.calls.append((command, kw))
            self.assertEqual(command[0], str(self.cli))
            self.assertEqual(command[command.index("--alias")+1], ALIAS)
            self.assertNotIn(self.key, " ".join(command))
            self.assertNotIn(self.token, " ".join(command))
            self.assertEqual(kw["env"]["CONTROL_PLANE_API_KEY"], self.key)
            self.assertEqual(kw["env"]["MCP_EXTRA_HEADERS"],
                             kw["env"]["MCP_DISCOVERY_EXTRA_HEADERS"])
            header = kw["env"]["MCP_EXTRA_HEADERS"]
            self.assertEqual(header,
                             "Authorization: env:HERMES_A4_OBSERVER_AUTHORIZATION")
            self.assertEqual(kw["env"]["HERMES_A4_OBSERVER_AUTHORIZATION"],
                             "Bearer " + self.token)
            self.assertNotIn(self.token, header)
            self.assertFalse((self.state/"a4-observer-authorization-header").exists())
            return SimpleNamespace(returncode=0)
        def status(cli, *, env):
            self.assertEqual(env["CONTROL_PLANE_API_KEY"], self.key)
            return {"process_running":True,"healthy":True,"ready":True}
        with patch("quickstart.a4.observer_tunnel.apply_windows_file_acl"):
            return connect(state=self.state,tunnel_id=self.id,bearer=self.token,
                           api_key=self.key,cli=self.cli,audit=audit,
                           aliases=lambda _ :frozenset(),execute=execute,
                           verify_status=status,**kwargs)

    def test_connect_with_distinct_alias_and_redacted_receipt(self):
        result=self.safe_connect()
        self.assertTrue(result["command_accepted"])
        self.assertFalse(result["remote_e2e_verified"])
        self.assertEqual(len(self.calls),1)
        r=self.state/"a4-observer-tunnel-attempt.json"
        data=r.read_text(encoding="utf-8")
        self.assertNotIn(self.token,data)
        self.assertNotIn(self.key,data)
        self.assertNotIn(self.id,data)
        self.assertEqual(json.loads(data)["state"],"local_runtime_healthy_remote_e2e_unverified")

    def test_connect_status_unhealthy_is_not_reported_as_success(self):
        def ok_execute(*args, **kwargs):
            self.calls.append((args,kwargs))
            return SimpleNamespace(returncode=0)
        with patch("quickstart.a4.observer_tunnel.apply_windows_file_acl"):
            with self.assertRaisesRegex(TunnelConnectError,"result_unknown"):
                connect(state=self.state,tunnel_id=self.id,bearer=self.token,
                        api_key=self.key,cli=self.cli,
                        audit=lambda p,t:{"ok":True},
                        aliases=lambda _:frozenset(),execute=ok_execute,
                        verify_status=lambda cli,env:{
                            "process_running":True,"healthy":False,"ready":True})
        self.assertEqual(len(self.calls),1)
        receipt=json.loads((self.state/"a4-observer-tunnel-attempt.json").read_text())
        self.assertEqual(receipt["state"],"attempted")

    def test_actual_status_parser_rejects_fake_true_and_wrong_alias(self):
        from quickstart.a4.observer_tunnel import runtime_status
        class R:
            def __init__(self,data):
                self.returncode=0
                self.stdout=json.dumps(data).encode("utf8")
        for payload in ({"alias": ALIAS,"process_running":"true","healthy":True,"ready":True},
                        {"alias":"local-workspace","process_running":True,"healthy":True,"ready":True},
                        {"alias":ALIAS,"process_running":True,"healthy":True},
                        {"alias":ALIAS,"process_running":True,"healthy":True,"ready":False}):
            with patch("quickstart.a4.observer_tunnel.subprocess.run",return_value=R(payload)):
                with self.assertRaises(TunnelConnectError):
                    runtime_status(self.cli)
        with patch("quickstart.a4.observer_tunnel.subprocess.run",
                   return_value=R({"alias": ALIAS,"process_running":True,
                                   "healthy":True,"ready":True})):
            self.assertTrue(runtime_status(self.cli)["ready"])

    def test_second_request_is_not_replayed(self):
        self.safe_connect()
        with self.assertRaisesRegex(TunnelConnectError,"existing_attempt"):
            self.safe_connect()
        self.assertEqual(len(self.calls),1)

    def test_ambiguous_exit_code_does_not_retry(self):
        def bad_execute(*args, **kwargs):
            self.calls.append((args,kwargs))
            return SimpleNamespace(returncode=23)
        with patch("quickstart.a4.observer_tunnel.apply_windows_file_acl"):
            with self.assertRaisesRegex(TunnelConnectError,"result_unknown"):
                connect(state=self.state,tunnel_id=self.id,bearer=self.token,
                        api_key=self.key,cli=self.cli,
                        audit=lambda p,t:{"ok":True},
                        aliases=lambda _:frozenset(),execute=bad_execute)
        self.assertEqual(len(self.calls),1)
        receipt=json.loads((self.state/"a4-observer-tunnel-attempt.json").read_text())
        self.assertEqual(receipt["state"],"attempted")

    def test_existing_remote_alias_fails_closed_without_header_creation(self):
        with self.assertRaisesRegex(TunnelConnectError,"already_exists"):
            connect(state=self.state,tunnel_id=self.id,bearer=self.token,
                    api_key=self.key,cli=self.cli,
                    audit=lambda p,t:{"ok":True},
                    aliases=lambda _:frozenset({ALIAS}),
                    execute=lambda *a,**kw: self.fail("must never connect"))
        self.assertFalse((self.state/"a4-observer-authorization-header").exists())

    def test_inventory_error_does_not_connect(self):
        def fail(_):
            raise TunnelConnectError("tunnel_alias_inventory_failed")
        with self.assertRaises(TunnelConnectError):
            connect(state=self.state,tunnel_id=self.id,bearer=self.token,
                    api_key=self.key,cli=self.cli,
                    audit=lambda p,t:{"ok":True},
                    aliases=fail,
                    execute=lambda *a,**kw:self.fail("not reached"))

    def test_decode_alias_schema_and_reject_unknown_shapes(self):
        self.assertEqual(decode_aliases({"aliases":["one",{"alias":"two"},{"name":"three"}]}),
                         frozenset({"one","two","three"}))
        for bad in (None,[],{"alias":[]},{"aliases":[{"url":"secret"}]},
                    {"aliases":[123]},{"aliases":None}):
            with self.assertRaises(TunnelConnectError):
                decode_aliases(bad)

    def test_invalid_tunnel_id_and_keys_rejected_before_effects(self):
        for x in ("local-workspace", "tunnel_", "tunnel_bad id", "../tunnel_a",
                  "tunnel_"+"a"*150):
            with self.assertRaises(TunnelConnectError):
                validate_input(x,self.token,self.key)
        with self.assertRaises(TunnelConnectError):
            validate_input(self.id,"short",self.key)
        with self.assertRaises(TunnelConnectError):
            validate_input(self.id,self.token,"line\nbreak")

    def test_header_mismatch_refused_and_not_overwritten(self):
        h=self.state/"a4-observer-authorization-header"
        h.write_text("Bearer other-test",encoding="utf-8")
        with self.assertRaisesRegex(TunnelConnectError,"header_conflict"):
            materialize_header(self.state,self.token)
        self.assertEqual(h.read_text(encoding="utf-8"),"Bearer other-test")

    @unittest.skipUnless(__import__("os").name == "nt",
                         "Windows ACL validation requires icacls")
    def test_real_windows_acl_on_synthetic_header(self):
        # No real key; the file and ACL are created only inside a temp dir.
        header=materialize_header(self.state,self.token)
        self.assertTrue(header.is_file())
        self.assertEqual(header.read_bytes(),("Bearer "+self.token+"\n").encode("ascii"))
        result=subprocess.run(["icacls.exe",str(header)],
                              capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0)
        self.assertNotIn(b"Bearer "+self.token.encode(),result.stdout)
        # Repeated preparation must reuse the same bytes, not rotate silently.
        self.assertEqual(materialize_header(self.state,self.token),header)

    def test_header_access_control_is_applied_before_secret_write(self):
        # Simulate ACL failure: must not leave a readable plaintext secret.
        with patch("quickstart.a4.observer_tunnel.apply_windows_file_acl",
                   side_effect=TunnelConnectError("acl_denied")):
            with self.assertRaises(TunnelConnectError):
                materialize_header(self.state,self.token)
        self.assertFalse((self.state/"a4-observer-authorization-header").exists())


if __name__=="__main__":
    unittest.main()
