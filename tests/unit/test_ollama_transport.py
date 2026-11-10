"""Real local HTTP fixtures, never the user's Ollama or GPU."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import unittest
from unittest.mock import patch
import urllib.error

import rag


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.direct=[];self.proxy=[];outer=self
        class Proxy(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.proxy.append(self.path);self.send_response(200);self.end_headers()
                self.wfile.write(b'{"message":{"content":"WRONG PROXY"}}')
            do_POST=do_GET
            def log_message(self,*args):pass
        self.proxy_server=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
        class Ollama(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.direct.append(self.path)
                self.rfile.read(int(self.headers.get('Content-Length','0')))
                if outer.redirect:
                    self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{outer.proxy_server.server_port}/unexpected');self.end_headers();return
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                self.wfile.write(json.dumps({'message':{'content':'OK'},'eval_count':1,'eval_duration':1000000,'done':True}).encode())
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Ollama)
        self.redirect=False
        self.threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in [self.server,self.proxy_server]]
        for thread in self.threads:thread.start()
        self.env={'RAG_OLLAMA_URL':f'http://127.0.0.1:{self.server.server_port}',
            'HTTP_PROXY':f'http://127.0.0.1:{self.proxy_server.server_port}','http_proxy':f'http://127.0.0.1:{self.proxy_server.server_port}',
            'NO_PROXY':'','no_proxy':''}

    def tearDown(self):
        for server in [self.server,self.proxy_server]:server.shutdown();server.server_close()
        for thread in self.threads:thread.join(timeout=2)

    def test_prompts_go_directly_to_selected_endpoint_despite_proxy_environment(self):
        with patch.dict(os.environ,self.env):
            answer,_=rag.generate_ollama([{'role':'user','content':'Public test fixture only.'}],'fake-local')
        self.assertEqual(answer,'OK');self.assertEqual(self.direct,['/api/chat']);self.assertEqual(self.proxy,[])

    def test_redirects_are_rejected_before_contacting_the_other_endpoint(self):
        self.redirect=True
        with patch.dict(os.environ,self.env),self.assertRaises(urllib.error.URLError):
            rag.generate_ollama([{'role':'user','content':'Public test fixture only.'}],'fake-local')
        self.assertEqual(self.proxy,[])
