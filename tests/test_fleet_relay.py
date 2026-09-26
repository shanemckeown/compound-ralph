import contextlib
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
import fleet_codex_rpc as rpc
import relay_to_orchestrator as relay

TARGET='01a08b81-dff5-7f91-baa1-30464253da42'
MESSAGE='Verbatim\n£25 `$(touch /tmp/NOPE)` "quoted"'

class Endpoint:
    def __init__(self, handler, desktop=False, fragmented=False):
        self.handler=handler;self.desktop=desktop;self.fragmented=fragmented;self.calls=[];self.errors=[]
    def __enter__(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp',prefix='fleet-rpc-')
        folder=Path(self.temp.name)
        if self.desktop:
            folder=folder/'ipc';folder.mkdir(mode=0o700)
        self.path=str(folder/('ipc.sock' if self.desktop else 'control.sock'))
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.server.bind(self.path)
        os.chmod(self.path,0o600);self.server.listen();self.server.settimeout(2)
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start();return self
    def run(self):
        try:
            with self.server.accept()[0] as client:
                client.settimeout(2)
                if self.desktop:
                    def exact(n):
                        b=b''
                        while len(b)<n:
                            x=client.recv(n-len(b))
                            if not x:return None
                            b+=x
                        return b
                    while True:
                        header=exact(4)
                        if not header:break
                        message=json.loads(exact(struct.unpack('<I',header)[0]))
                        self.calls.append(message)
                        result=self.handler(message)
                        if result is not None:
                            data=json.dumps(result).encode();frame=struct.pack('<I',len(data))+data
                            if self.fragmented:
                                for byte in frame:client.sendall(bytes([byte]))
                            else:client.sendall(frame)
                else:
                    file=client.makefile('rb')
                    for line in file:
                        message=json.loads(line);self.calls.append(message)
                        result=self.handler(message)
                        if isinstance(result,bytes):
                            client.sendall(result);break
                        if result is not None:
                            client.sendall(json.dumps(result).encode()+b'\n')
        except Exception as exc:self.errors.append(exc)
    def __exit__(self,*args):
        self.thread.join(3);self.server.close();self.temp.cleanup()
        if self.thread.is_alive():raise AssertionError('endpoint leaked thread')


def app_handler(status='idle',identity=TARGET,turn_result=None):
    def handle(message):
        method=message['method']
        if method=='initialized':return None
        if method=='initialize':result={'userAgent':'fake'}
        elif method=='thread/read':result={'thread':{'id':identity,'name':'Fleet','status':{'type':status}}}
        elif method=='turn/start':result=turn_result if turn_result is not None else {'turn':{'id':'turn-123','status':'inProgress'}}
        else:raise AssertionError(method)
        return {'id':message['id'],'result':result}
    return handle


def record(path='/private/tmp/no/control.sock',provider='codex',**updates):
    return {'provider':provider,'session_id':TARGET,'codex_socket':path,'codex_socket_verified':True,**updates}

def role_result(status='LIVE',rec=None):
    rec=rec or record()
    return Mock(returncode=0,stdout=json.dumps({'record':rec,'status':status,'provider':rec.get('provider','claude')}))

class RelayTests(unittest.TestCase):
    def test_registry_unknown_never_routes(self):
        with patch.object(relay.subprocess,'run',return_value=role_result('UNKNOWN')) as run:
            with self.assertRaisesRegex(relay.RelayFailure,'not a stale claim'):relay.get_holder()
            self.assertEqual(run.call_count,1)
    def test_registry_stale_no_delivery(self):
        with patch.object(relay.subprocess,'run',return_value=role_result('STALE')):
            with self.assertRaises(relay.RelayFailure) as caught:relay.get_holder()
            self.assertEqual(caught.exception.exit_code,1)
    def test_registry_unknown_provider_denied(self):
        with patch.object(relay.subprocess,'run',return_value=role_result(rec=record(provider='other'))):
            with self.assertRaisesRegex(relay.RelayFailure,'invalid session/provider'):relay.get_holder()
    def test_legacy_claude_record(self):
        rec={'session_id':TARGET,'name':'reused-name'}
        with patch.object(relay.subprocess,'run',return_value=role_result(rec=rec)):
            self.assertEqual(relay.get_holder(),rec)
    def test_registry_invalid_json(self):
        with patch.object(relay.subprocess,'run',return_value=Mock(returncode=0,stdout='not-json')):
            with self.assertRaisesRegex(relay.RelayFailure,'invalid JSON'):relay.get_holder()
    def test_self_loop_no_socket(self):
        with patch.dict(os.environ,{'CODEX_THREAD_ID':TARGET}):
            with self.assertRaisesRegex(relay.RelayFailure,'already the Codex orchestrator'):relay.relay_codex(record(),MESSAGE)
    def test_desktop_no_fake_delivery(self):
        with self.assertRaisesRegex(relay.RelayFailure,'no verified external delivery transport'):
            relay.relay_codex(record('/Users/shane/.codex/ipc/ipc.sock'),MESSAGE)
    def test_unverified_socket_denied(self):
        with self.assertRaisesRegex(relay.RelayFailure,'no verified control socket'):
            relay.relay_codex(record(codex_socket_verified=False),MESSAGE)
    def test_codex_delivers_exact_input_and_id(self):
        with Endpoint(app_handler()) as endpoint:
            result=relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertIn('Accepted by Codex',result)
        methods=[x['method'] for x in endpoint.calls]
        self.assertEqual(methods,['initialize','initialized','thread/read','turn/start'])
        sent=endpoint.calls[-1]['params']
        self.assertEqual(sent['threadId'],TARGET);self.assertEqual(sent['input'],[{'type':'text','text':MESSAGE}])
        self.assertEqual(set(sent),{'threadId','input','clientUserMessageId'})
    def test_active_input_same_thread_no_resume(self):
        with Endpoint(app_handler(status='active')) as endpoint:
            relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertNotIn('thread/resume',[x['method'] for x in endpoint.calls])
        self.assertEqual(sum(x['method']=='turn/start' for x in endpoint.calls),1)
    def test_wrong_thread_never_sends(self):
        with Endpoint(app_handler(identity='wrong')) as endpoint:
            with self.assertRaisesRegex(relay.RelayFailure,'wrong thread'):relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertFalse(any(x['method']=='turn/start' for x in endpoint.calls))
    def test_unloaded_thread_never_sends(self):
        with Endpoint(app_handler(status='notLoaded')) as endpoint:
            with self.assertRaisesRegex(relay.RelayFailure,'not accepting input'):relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertFalse(any(x['method']=='turn/start' for x in endpoint.calls))
    def test_bad_ack_unknown_no_retry(self):
        with Endpoint(app_handler(turn_result={'turn':{'id':'turn1'}})) as endpoint:
            with self.assertRaises(relay.RelayFailure) as caught:relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertEqual(caught.exception.exit_code,4)
        self.assertEqual(sum(x['method']=='turn/start' for x in endpoint.calls),1)
    def test_rpc_rejection_not_success(self):
        def handle(message):
            if message['method']=='turn/start':return {'id':message['id'],'error':{'code':1,'message':'rejected'}}
            return app_handler()(message)
        with Endpoint(handle) as endpoint:
            with self.assertRaisesRegex(relay.RelayFailure,'rejected'):relay.relay_codex(record(endpoint.path),MESSAGE)
    def test_ack_loss_unknown_no_retry(self):
        client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=None)
        client.request.side_effect=[{'thread':{'id':TARGET,'status':{'type':'idle'}}},rpc.TransportError('EOF')]
        with patch.object(relay,'CodexRPC',return_value=client):
            with self.assertRaises(relay.RelayFailure) as caught:relay.relay_codex(record(),MESSAGE)
        self.assertEqual(caught.exception.exit_code,4);self.assertEqual(client.request.call_count,2)
    def test_malformed_error_after_submission_is_unknown(self):
        def handle(message):
            if message['method']=='turn/start':return {'id':message['id'],'error':None}
            return app_handler()(message)
        with Endpoint(handle) as endpoint:
            with self.assertRaises(relay.RelayFailure) as caught:relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertEqual(caught.exception.exit_code,4)
        self.assertEqual(sum(x['method']=='turn/start' for x in endpoint.calls),1)
    def test_truncated_frame_after_submission_is_unknown(self):
        def handle(message):
            if message['method']=='turn/start':return b'{"id":3,"result":'
            return app_handler()(message)
        with Endpoint(handle) as endpoint:
            with self.assertRaises(relay.RelayFailure) as caught:relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertEqual(caught.exception.exit_code,4)
        self.assertEqual(sum(x['method']=='turn/start' for x in endpoint.calls),1)
    def test_null_runtime_status_does_not_send(self):
        def handle(message):
            if message['method']=='thread/read':return {'id':message['id'],'result':{'thread':{'id':TARGET,'status':None}}}
            return app_handler()(message)
        with Endpoint(handle) as endpoint:
            with self.assertRaises(relay.RelayFailure) as caught:relay.relay_codex(record(endpoint.path),MESSAGE)
        self.assertEqual(caught.exception.exit_code,3)
        self.assertFalse(any(x['method']=='turn/start' for x in endpoint.calls))
    def test_claude_prose_not_receipt(self):
        self.assertFalse(relay.claude_receipt(json.dumps({'type':'result','subtype':'success','result':'Sent!'}),TARGET,MESSAGE))
    def test_claude_receipt_exact_target_and_message(self):
        events=[{'type':'assistant','message':{'content':[{'type':'tool_use','id':'call1','name':'SendMessage','input':{'to':TARGET,'message':MESSAGE}}]}},
                {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'call1','content':json.dumps({'success':True})}]}},
                {'type':'result','subtype':'success','is_error':False}]
        stream='\n'.join(map(json.dumps,events))
        self.assertTrue(relay.claude_receipt(stream,TARGET,MESSAGE))
        self.assertFalse(relay.claude_receipt(stream,'reused-name',MESSAGE))
        self.assertFalse(relay.claude_receipt(stream,TARGET,MESSAGE+' changed'))
    def test_claude_argv_has_no_shell(self):
        # claude_address() lists sessions first (15 Sep); give it the orchestrator, then fail the launch.
        agents=Mock(returncode=0,stdout=json.dumps([{'sessionId':TARGET,'name':'orch-tab'}]))
        with patch.object(relay.subprocess,'run',side_effect=[agents,Mock(returncode=1,stdout='')]) as run:
            with self.assertRaises(relay.RelayFailure):relay.relay_claude(record(provider='claude'),MESSAGE,('aestheticc-5d','x'))
        args,kwargs=run.call_args
        self.assertNotIn('shell',kwargs)
        self.assertIn('orch-tab',args[0][2]);self.assertIn('--output-format',args[0])
        self.assertIn('relay-for-aestheticc-5d',args[0])

class ReplyToStampTests(unittest.TestCase):
    """26 Sep: relayed messages arrived from the throwaway relay process's name and socket, so the
    orchestrator could not reply. The relay now stamps the caller's real tab name and session id."""
    SID='bf02e123-5673-4902-8aef-e4b583e0bf2a'
    def test_stamp_first_line_names_real_tab(self):
        out=relay.stamp('status: done',('aestheticc-18',self.SID))
        self.assertEqual(out.splitlines()[0],f'reply-to: aestheticc-18 ({self.SID})')
        self.assertTrue(out.endswith('status: done'))
    def test_caller_identity_resolves_name_from_session_id(self):
        rows=json.dumps([{'sessionId':self.SID,'name':'aestheticc-18'},{'sessionId':'other','name':'x'}])
        env={k:v for k,v in os.environ.items() if k!='CODEX_THREAD_ID'}; env['CLAUDE_CODE_SESSION_ID']=self.SID
        with patch.dict(os.environ,env,clear=True), \
             patch.object(relay.subprocess,'run',return_value=Mock(returncode=0,stdout=rows)):
            self.assertEqual(relay.caller_identity(),('aestheticc-18',self.SID))
    def test_caller_identity_codex(self):
        env={k:v for k,v in os.environ.items() if k not in ('CLAUDE_CODE_SESSION_ID','CLAUDE_SESSION_ID')}
        env['CODEX_THREAD_ID']='thr-1'
        with patch.dict(os.environ,env,clear=True):
            self.assertEqual(relay.caller_identity(),('codex-thread','thr-1'))
    def test_codex_caller_inside_a_claude_tab_is_codex(self):
        with patch.dict(os.environ,{'CLAUDE_CODE_SESSION_ID':self.SID,'CODEX_THREAD_ID':'thr-9'},clear=False):
            self.assertEqual(relay.caller_identity(),('codex-thread','thr-9'))
    def test_codex_child_of_orchestrator_tab_can_relay(self):
        # Astra r2: a Codex child inherits the orchestrator's CLAUDE_CODE_SESSION_ID; not a self-loop.
        agents=Mock(returncode=0,stdout=json.dumps([{'sessionId':TARGET,'name':'orch-tab'}]))
        with patch.dict(os.environ,{'CLAUDE_CODE_SESSION_ID':TARGET,'CODEX_THREAD_ID':'thr-1'},clear=False), \
             patch.object(relay.subprocess,'run',side_effect=[agents,Mock(returncode=1,stdout='')]) as run:
            with self.assertRaises(relay.RelayFailure) as e:
                relay.relay_claude(record(provider='claude'),'m',('codex-thread','thr-1'))
        self.assertNotIn('already the Claude orchestrator',str(e.exception))
        self.assertEqual(run.call_count,2)
    def test_unknown_caller_is_said_plainly(self):
        self.assertTrue(relay.stamp('m',('','')).startswith('reply-to: unknown'))
    def test_main_sends_stamped_message(self):
        sent={}
        def fake(record,message,identity=('','')):
            sent['m']=message;return 'ok'
        with patch.object(relay,'get_holder',return_value={'session_id':TARGET,'provider':'claude'}), \
             patch.object(relay,'caller_identity',return_value=('aestheticc-5d',self.SID)), \
             patch.object(relay,'relay_claude',side_effect=fake):
            self.assertEqual(relay.main(['hello']),0)
        self.assertEqual(sent['m'],f'reply-to: aestheticc-5d ({self.SID})\nhello')

class ProbeTests(unittest.TestCase):
    def test_invalid_inputs_unknown(self):
        for target,path in [(None,'x'),(42,'x'),('valid',42)]:
            self.assertEqual(rpc.probe_thread(target,path)['status'],'UNKNOWN')
    def test_desktop_malformed_result_unknown(self):
        def handle(m):
            return {'type':'response','requestId':m['requestId'],'resultType':'success','method':m['method'],'result':None}
        with patch.object(rpc,'_check_desktop_build'),Endpoint(handle,desktop=True) as endpoint:
            self.assertEqual(rpc.probe_thread(TARGET,endpoint.path)['status'],'UNKNOWN')
    def test_absent_socket_unknown(self):
        self.assertEqual(rpc.probe_thread(TARGET,'/private/tmp/missing-fleet.sock')['status'],'UNKNOWN')
    def test_explicit_runtime_live(self):
        for state in ['active','idle','systemError']:
            with self.subTest(state=state),Endpoint(app_handler(status=state)) as endpoint:
                self.assertEqual(rpc.probe_thread(TARGET,endpoint.path)['status'],'LIVE')
            self.assertEqual([x['method'] for x in endpoint.calls],['initialize','initialized','thread/read'])
    def test_explicit_notloaded(self):
        with Endpoint(app_handler(status='notLoaded')) as endpoint:
            self.assertEqual(rpc.probe_thread(TARGET,endpoint.path)['status'],'NOT_LOADED')
    def test_wrong_id_unknown(self):
        with Endpoint(app_handler(identity='wrong')) as endpoint:
            self.assertEqual(rpc.probe_thread(TARGET,endpoint.path)['status'],'UNKNOWN')
    def test_desktop_owner_bound_to_exact_thread(self):
        def handle(m):
            if m['method']=='initialize':result={'clientId':'self'};owner='self'
            else:
                self.assertEqual(m['params'],{'hostId':'local','conversationId':TARGET})
                self.assertEqual(m['version'],1)
                result={'supportsUntrustedAppInput':True};owner='owner'
            return {'type':'response','requestId':m['requestId'],'resultType':'success','method':m['method'],'handledByClientId':owner,'result':result}
        with patch.object(rpc,'_check_desktop_build'),Endpoint(handle,desktop=True,fragmented=True) as endpoint:
            result=rpc.probe_thread(TARGET,endpoint.path)
        self.assertEqual(result['status'],'LIVE');self.assertEqual(result['owner_client_id'],'owner')
    def test_desktop_no_owner_unknown_not_stale(self):
        def handle(m):
            if m['method']=='initialize':return {'type':'response','requestId':m['requestId'],'resultType':'success','method':'initialize','result':{'clientId':'self'}}
            return {'type':'response','requestId':m['requestId'],'resultType':'error','error':'no-client-found'}
        with patch.object(rpc,'_check_desktop_build'),Endpoint(handle,desktop=True) as endpoint:
            self.assertEqual(rpc.probe_thread(TARGET,endpoint.path)['status'],'UNKNOWN')
    def test_desktop_changed_version_unknown(self):
        with patch.object(rpc,'_check_desktop_build',side_effect=rpc.TransportError('build changed')):
            self.assertEqual(rpc.probe_thread(TARGET,'/Users/shane/.codex/ipc/ipc.sock')['status'],'UNKNOWN')
    def test_world_writable_socket_refused(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            path=str(Path(folder)/'control.sock')
            sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sock.bind(path);os.chmod(path,0o666)
            try:
                with self.assertRaisesRegex(rpc.TransportError,'writable'):rpc._validate_socket(path)
            finally:sock.close()

if __name__=='__main__':unittest.main(verbosity=2)
