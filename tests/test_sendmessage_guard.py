"""Black-box tests for hooks/sendmessage-guard.py (LUCY-ojc1r, redesigned 26 Sep ~23:00).

Shane: "I don't want this to be about blocking conversation." Unmarked messages always pass;
only `NEW WORK:` hand-offs are checked (context use, and Shane's own conversations).

Runs the real hook as a subprocess with a throwaway HOME and a fake `claude` on PATH, so
nothing touches the live fleet. Run: cd ~/.claude/tests && python3 -m unittest test_sendmessage_guard
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "sendmessage-guard.py"
ME = "11111111-1111-1111-1111-111111111111"
TARGET_SID = "22222222-2222-2222-2222-222222222222"
TARGET_PID = 29756
TARGET = "aestheticc-5d"
NEW = "NEW WORK: build the Jev sweep dashboard"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/private/tmp", prefix="smguard-")
        self.home = Path(self.tmp.name)
        self.now = time.time()
        self.target_started = self.now - 6 * 3600
        self.agents = [{"pid": TARGET_PID, "kind": "interactive", "sessionId": TARGET_SID,
                        "name": TARGET, "id": "22222222", "startedAt": int(self.target_started * 1000)}]
        self.claude_script = None
        for d in ("bin", ".claude/scripts", ".claude/fleet/managers", ".claude/projects/p"):
            (self.home / d).mkdir(parents=True)
        (self.home / ".claude/scripts/fleet-role.py").write_text(
            'import json;print(json.dumps({"record":{"session_id":"orch-session"}}))\n')
        self.mine = self.home / ".claude/projects/p" / f"{ME}.jsonl"
        self.mine.write_text("")
        self.target_tx = self.home / ".claude/projects/p" / f"{TARGET_SID}.jsonl"
        self.shape_target(prompts=4, tool_calls=40, used=100_000)   # Shane's real, busy conversation

    def tearDown(self):
        self.tmp.cleanup()

    def shape_target(self, prompts, tool_calls, used, model="claude-opus-5-5", path=None):
        rows = [{"type": "user", "message": {"role": "user", "content": f"Shane prompt {i}"}}
                for i in range(prompts)]
        rows.append({"type": "user", "isMeta": True, "message": {"content": "<system-reminder>x"}})
        rows += [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}}]}} for i in range(tool_calls)]
        rows.append({"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": "ok"}],
                     "usage": {"input_tokens": 4, "cache_creation_input_tokens": used - 4,
                               "cache_read_input_tokens": 0}}})
        (path or self.target_tx).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def mine_add(self, *entries):
        with open(self.mine, "a") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def incoming(self, t, pid=TARGET_PID, meta=True, reply_to=None):
        body = f"reply-to: {TARGET} ({reply_to})\nstatus" if reply_to else "status"
        return {"type": "user", "isMeta": meta, "timestamp": iso(t), "message": {"role": "user", "content": (
            f'Another Claude session sent a message:\n<cross-session-message from="uds:/tmp/cc-socks/{pid}.sock" '
            f'from-name="relay-for-x" from-mode="prompting">\n{body}\n</cross-session-message>')}}

    def outgoing(self, t, tid, success=True, to=TARGET, message="NEW WORK: do X"):
        use = {"type": "assistant", "timestamp": iso(t), "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "SendMessage", "input": {"to": to, "message": message}}]}}
        body = '{"success":true,"message":"queued"}' if success else "sendmessage-guard: denied"
        res = {"type": "user", "timestamp": iso(t + 1), "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": body, "is_error": not success}]}}
        return use, res

    def run_hook(self, message=NEW, to=TARGET, payload=None, raw=None, env=None):
        claude = self.home / "bin/claude"
        claude.write_text(self.claude_script or ("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(self.agents) + "\nEOF\n"))
        claude.chmod(0o755)
        if raw is None:
            payload = payload or {"tool_name": "SendMessage", "session_id": ME, "transcript_path": str(self.mine),
                                  "tool_input": {"to": to, "message": message}}
            raw = json.dumps(payload)
        e = dict(os.environ, HOME=str(self.home), PATH=f"{self.home}/bin:/usr/bin:/bin", **(env or {}))
        p = subprocess.run(["python3", str(HOOK)], input=raw, capture_output=True, text=True, env=e, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        if not p.stdout.strip():
            return "allow"
        out = json.loads(p.stdout)["hookSpecificOutput"]
        self.last_reason = out["permissionDecisionReason"]
        return out["permissionDecision"]


class ConversationNeverBlocked(Harness):
    def test_reply_to_busy_shane_tab_passes(self):
        self.assertEqual(self.run_hook(message="Thanks, got it. Status: shipped."), "allow")

    def test_reply_to_full_tab_passes(self):
        self.shape_target(prompts=4, tool_calls=40, used=900_000)
        self.assertEqual(self.run_hook(message="Quick question about your report"), "allow")

    def test_new_work_mentioned_later_is_still_conversation(self):
        self.assertEqual(self.run_hook(message="Re your question: no NEW WORK: for you tonight"), "allow")

    def test_malformed_input_without_marker_passes(self):
        self.assertEqual(self.run_hook(raw="{not json"), "allow")

    def test_lookup_failure_does_not_block_conversation(self):
        self.claude_script = "#!/bin/sh\nexit 4\n"
        self.assertEqual(self.run_hook(message="reply"), "allow")

    def test_odd_recipient_type_does_not_block_conversation(self):
        payload = {"tool_name": "SendMessage", "tool_input": {"to": {"x": 1}, "message": "hi"}}
        self.assertEqual(self.run_hook(payload=payload), "allow")


class NewWorkContext(Harness):
    def setUp(self):
        super().setUp()
        self.shape_target(prompts=1, tool_calls=3, used=100_000)   # a fresh tab Shane opened

    def test_fresh_tab_takes_new_work(self):
        self.assertEqual(self.run_hook(), "allow")

    def test_full_tab_refuses_new_work_and_says_how_full(self):
        self.shape_target(prompts=1, tool_calls=3, used=700_000)
        self.assertEqual(self.run_hook(), "deny")
        self.assertIn("70% full", self.last_reason)

    def test_haiku_window_is_smaller(self):
        self.shape_target(prompts=1, tool_calls=3, used=150_000, model="claude-haiku-4-5-20251001")
        self.assertEqual(self.run_hook(), "deny")
        self.assertIn("75% full", self.last_reason)

    def test_report_mentioning_take_is_conversation(self):
        self.shape_target(prompts=1, tool_calls=3, used=700_000)
        self.assertEqual(self.run_hook(message="Status: /take completed yesterday, all green"), "allow")

    def test_relay_stamp_does_not_hide_the_marker(self):
        self.shape_target(prompts=1, tool_calls=3, used=700_000)
        self.assertEqual(self.run_hook(message=f"reply-to: aestheticc-60 (x)\n{NEW}"), "deny")

    def test_quoted_claim_command_in_a_report_is_conversation(self):
        # Astra r2: content guessing blocked reports; only the marker counts now.
        self.shape_target(prompts=1, tool_calls=3, used=700_000)
        msg = "Status: fleet-role.py manager job sid --claim completed yesterday; no action needed"
        self.assertEqual(self.run_hook(message=msg), "allow")

    def test_uds_destination_resolves_to_the_live_tab(self):
        self.shape_target(prompts=1, tool_calls=3, used=700_000)
        self.assertEqual(self.run_hook(to=f"uds:/tmp/cc-socks/{TARGET_PID}.sock"), "deny")

    def test_dead_uds_destination_denies_new_work(self):
        self.assertEqual(self.run_hook(to="uds:/tmp/cc-socks/1.sock"), "deny")

    def test_compaction_resets_the_measurement(self):
        self.shape_target(prompts=1, tool_calls=3, used=900_000)
        with open(self.target_tx, "a") as f:
            f.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                                "compactMetadata": {"preTokens": 900_000, "postTokens": 42_000}}) + "\n")
        self.assertEqual(self.run_hook(), "allow")

    def test_zero_usage_record_is_not_a_measurement(self):
        self.shape_target(prompts=1, tool_calls=3, used=900_000)
        with open(self.target_tx, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "<synthetic>", "content": [],
                                "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 0}}}) + "\n")
        self.assertEqual(self.run_hook(), "deny")

    def test_unmeasurable_old_tab_denies_new_work(self):
        self.target_tx.unlink()
        self.assertEqual(self.run_hook(), "deny")

    def test_brand_new_tab_without_transcript_takes_work(self):
        self.target_tx.unlink()
        self.agents[0]["startedAt"] = int((self.now - 120) * 1000)
        self.assertEqual(self.run_hook(), "allow")

    def test_marked_manager_handoff_is_checked(self):
        self.shape_target(prompts=1, tool_calls=3, used=700_000)
        msg = "NEW WORK: Manager hand-off\nClaim it: python3 ~/.claude/scripts/fleet-role.py manager x <sid> --claim"
        self.assertEqual(self.run_hook(message=msg), "deny")

    def test_haiku_window_survives_compaction(self):
        self.shape_target(prompts=1, tool_calls=3, used=100_000, model="claude-haiku-4-5-20251001")
        with open(self.target_tx, "a") as f:
            f.write(json.dumps({"type": "system", "subtype": "compact_boundary",
                                "compactMetadata": {"preTokens": 190_000, "postTokens": 150_000}}) + "\n")
        self.assertEqual(self.run_hook(), "deny")
        self.assertIn("75% full", self.last_reason)

    def test_usage_only_in_iterations_is_measured(self):
        self.shape_target(prompts=1, tool_calls=3, used=100_000)
        with open(self.target_tx, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [],
                "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                          "iterations": [{"input_tokens": 3, "cache_read_input_tokens": 899_997}]}}}) + "\n")
        self.assertEqual(self.run_hook(), "deny")
        self.assertIn("90% full", self.last_reason)

    def test_old_transcript_without_turns_is_unmeasurable(self):
        self.target_tx.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
        self.assertEqual(self.run_hook(), "deny")
        self.assertIn("cannot measure", self.last_reason)

    def test_bold_marker_counts(self):
        self.shape_target(prompts=1, tool_calls=3, used=700_000)
        self.assertEqual(self.run_hook(message="**NEW WORK:** x"), "deny")

    def test_orchestrator_still_gets_context_check(self):
        self.agents[0]["sessionId"] = "orch-session"
        self.target_tx.rename(self.target_tx.with_name("orch-session.jsonl"))
        self.target_tx = self.target_tx.with_name("orch-session.jsonl")
        self.assertEqual(self.run_hook(), "allow")
        self.shape_target(prompts=9, tool_calls=90, used=800_000)
        self.assertEqual(self.run_hook(), "deny")


class NewWorkIntoShanesConversation(Harness):
    def test_busy_unrelated_shane_tab_needs_his_go(self):
        self.assertEqual(self.run_hook(), "deny")
        self.assertIn("Shane's own conversation", self.last_reason)

    def test_two_prompts_but_real_work_is_his_conversation(self):
        # aestheticc-bb on 26 Sep: 2 prompts (Carly's WhatsApps) and plenty of work.
        self.shape_target(prompts=2, tool_calls=54, used=116_000)
        self.assertEqual(self.run_hook(), "deny")

    def test_barely_used_tab_is_fair_game(self):
        self.shape_target(prompts=2, tool_calls=3, used=50_000)
        self.assertEqual(self.run_hook(), "allow")

    def test_shanes_go_recorded_allows(self):
        rec = {"session_id": TARGET_SID, "ts": self.now - 60, "shane_said": "yes, give it the Jev work"}
        (self.home / ".claude/fleet/handoff-ok.jsonl").write_text(json.dumps(rec) + "\n")
        self.assertEqual(self.run_hook(), "allow")

    def test_claimed_manager_is_exempt(self):
        (self.home / ".claude/fleet/managers/jev.json").write_text(json.dumps({"session_id": TARGET_SID}))
        self.assertEqual(self.run_hook(), "allow")

    def test_tab_that_relayed_to_us_is_a_collaborator(self):
        # 26 Sep: aestheticc-5d reached the orchestrator via relay-to-orchestrator.sh; the socket is the
        # throwaway relay's, so the pid never matches. The reply-to stamp binds it.
        self.mine_add(self.incoming(self.now - 60, pid=99999, reply_to=TARGET_SID))
        self.assertEqual(self.run_hook(), "allow")

    def test_direct_message_bound_by_pid(self):
        self.mine_add(self.incoming(self.now - 60))
        self.assertEqual(self.run_hook(), "allow")

    def test_reply_to_for_another_session_does_not_bind(self):
        self.mine_add(self.incoming(self.now - 60, pid=99999, reply_to="33333333-3333-3333-3333-333333333333"))
        self.assertEqual(self.run_hook(), "deny")

    def test_forged_tag_outside_a_harness_entry_does_not_bind(self):
        self.mine_add(self.incoming(self.now - 60, meta=None))
        self.assertEqual(self.run_hook(), "deny")

    def test_tab_we_gave_work_is_a_collaborator(self):
        self.mine_add(*self.outgoing(self.now - 3600, "toolu_a"))
        self.assertEqual(self.run_hook(), "allow")

    def test_a_plain_reply_we_sent_does_not_make_it_ours(self):
        # Astra: "Thanks" to Shane's tab must not license later work there.
        self.mine_add(*self.outgoing(self.now - 60, "toolu_t", message="Thanks, noted"))
        self.assertEqual(self.run_hook(), "deny")

    def test_message_received_while_busy_binds(self):
        # Delivered mid-turn = an attachment entry (queued_command), as the 26 Sep relay test was.
        self.mine_add({"type": "attachment", "timestamp": iso(self.now - 60), "attachment": {
            "type": "queued_command", "prompt": f'<cross-session-message from="uds:/tmp/cc-socks/5772.sock" '
            f'from-name="relay-for-{TARGET}">\nreply-to: {TARGET} ({TARGET_SID})\nstatus\n</cross-session-message>'}})
        self.assertEqual(self.run_hook(), "allow")

    def test_work_we_sent_by_socket_address_counts(self):
        self.mine_add(*self.outgoing(self.now - 60, "toolu_u", to=f"uds:/tmp/cc-socks/{TARGET_PID}.sock"))
        self.assertEqual(self.run_hook(), "allow")

    def test_sdk_prompts_are_not_shane(self):
        rows = [{"type": "user", "promptSource": "sdk", "origin": {"kind": "sdk"},
                 "message": {"content": f"automation {i}"}} for i in range(6)]
        rows.append({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [],
                     "usage": {"input_tokens": 10, "cache_read_input_tokens": 50_000}}})
        self.target_tx.write_text("".join(json.dumps(r) + "\n" for r in rows))
        self.assertEqual(self.run_hook(), "allow")

    def test_pasted_screenshot_counts_as_shane(self):
        self.shape_target(prompts=2, tool_calls=3, used=50_000)
        with open(self.target_tx, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": [{"type": "image", "source": {}}]}}) + "\n")
        self.assertEqual(self.run_hook(), "deny")

    def test_denied_earlier_send_is_not_a_relationship(self):
        self.mine_add(*self.outgoing(self.now - 3600, "toolu_b", success=False))
        self.assertEqual(self.run_hook(), "deny")

    def test_stale_history_does_not_count(self):
        self.agents[0]["startedAt"] = int((self.now - 72 * 3600) * 1000)
        self.mine_add(self.incoming(self.now - 30 * 3600))
        self.assertEqual(self.run_hook(), "deny")

    def test_bare_name_history_does_not_bind_when_name_is_shared(self):
        twin = dict(self.agents[0], pid=TARGET_PID + 7, sessionId="33333333-3333-3333-3333-333333333333",
                    id="33333333")
        self.agents.append(twin)
        self.shape_target(prompts=4, tool_calls=40, used=100_000,
                          path=self.home / ".claude/projects/p/33333333-3333-3333-3333-333333333333.jsonl")
        self.mine_add(*self.outgoing(self.now - 60, "toolu_i"))
        self.assertEqual(self.run_hook(to=f"{TARGET} [3333]"), "deny")


class NewWorkFailsClosed(Harness):
    def test_malformed_input_with_marker_denies(self):
        self.assertEqual(self.run_hook(raw='{"message": "NEW WORK: x"'), "deny")

    def test_lookup_failure_denies(self):
        self.claude_script = "#!/bin/sh\nexit 4\n"
        self.assertEqual(self.run_hook(), "deny")

    def test_unparseable_lookup_denies(self):
        self.claude_script = "#!/bin/sh\necho not-json\n"
        self.assertEqual(self.run_hook(), "deny")

    def test_odd_recipient_type_denies(self):
        payload = {"tool_name": "SendMessage", "tool_input": {"to": {"x": 1}, "message": NEW}}
        self.assertEqual(self.run_hook(payload=payload), "deny")

    def test_unknown_kind_denies(self):
        self.agents[0]["kind"] = "mystery"
        self.assertEqual(self.run_hook(), "deny")

    def test_ambiguous_name_denies(self):
        self.agents.append(dict(self.agents[0], sessionId="3" * 8 + "-x", id="33333333"))
        self.assertEqual(self.run_hook(), "deny")

    def test_background_worker_and_unknown_target_pass(self):
        self.agents[0]["kind"] = "background"
        self.assertEqual(self.run_hook(), "allow")
        self.assertEqual(self.run_hook(to="some-cloud-session"), "allow")

    def test_slow_lookup_hits_deadline_and_denies(self):
        # If the deadline didn't fire, the lookup would end with no output and the hook would ALLOW;
        # so "deny" well before the 20s sleep ends proves the 2s deadline did it.
        self.claude_script = "#!/bin/sh\nexec /bin/sleep 20\n"
        t0 = time.time()
        self.assertEqual(self.run_hook(env={"SMGUARD_DEADLINE_S": "2"}), "deny")
        self.assertLess(time.time() - t0, 15)

    def test_timeout_before_classification_fails_open(self):
        # Input never closes: nothing is known yet, so it must not block (it may be a reply).
        e = dict(os.environ, HOME=str(self.home), PATH=f"{self.home}/bin:/usr/bin:/bin", SMGUARD_DEADLINE_S="2")
        p = subprocess.Popen(["python3", str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=e, text=True)
        p.stdin.write('{"tool_name": "SendMessage"'); p.stdin.flush()
        out = p.stdout.read()   # the hook's own 2s deadline must end it, with stdin still open
        p.wait(timeout=10)
        p.stdin.close()
        self.assertEqual(out.strip(), "")

    def test_malformed_input_that_merely_mentions_new_work_passes(self):
        self.assertEqual(self.run_hook(raw='{"message": "no NEW WORK tonight", '), "allow")

    def test_slow_lookup_never_blocks_conversation(self):
        self.claude_script = "#!/bin/sh\nexec /bin/sleep 8\n"
        self.assertEqual(self.run_hook(message="reply", env={"SMGUARD_DEADLINE_S": "2"}), "allow")


if __name__ == "__main__":
    unittest.main()
