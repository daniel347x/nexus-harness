#!/usr/bin/env python3
"""Does a remote MCP request that stays silent for SILENCE seconds survive in this OpenCode binary?

Bun's fetch gives up on a request silent for about six minutes (Bun 1.4.2: 360-380 s) unless it passes
`timeout: false`, which plugins/mcp-fetch-timeout.ts adds to MCP traffic. Run with 600 after an OpenCode upgrade
that touches the MCP transport: A must end `connected`, B `failed` ("The operation timed out."). ~12 minutes.

Two throwaway `opencode serve` instances (own homes, own passwords), each with ONE remote MCP server: a tiny
Streamable-HTTP MCP server written here that answers `initialize` at once and then stays completely silent on
`tools/list` for SILENCE seconds before answering. Instance A has our mcp-fetch-timeout plugin installed (plus a
probe line that logs every request the wrapper handles); instance B has no plugin. The dummy server logs, per
request, when the client hung up (if it did) and whether its late reply could be written.

No model, no live service touched, nothing installed. Run from WSL: python3 scripts/oc_mcp_silence_test.py [SILENCE]
"""
import base64, json, os, secrets, shutil, socket, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SILENCE = int(sys.argv[1]) if len(sys.argv) > 1 else 330
BINARY = Path.home() / '.opencode/bin/opencode'
REPO = Path('/mnt/e/__daniel347x/__Dan_Root/__repos/opencode-companion')
T0 = time.monotonic()


def log(msg):
    print(f'[{time.monotonic() - T0:7.1f}s] {msg}', flush=True)


def make_handler(tag):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def _send(self, code, body=None, extra=None):
            data = json.dumps(body).encode() if body is not None else b''
            self.send_response(code)
            if body is not None:
                self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Mcp-Session-Id', 'sess-' + tag)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(405)

        def do_DELETE(self):
            self._send(200, {})

        def do_POST(self):
            n = int(self.headers.get('Content-Length') or 0)
            msg = json.loads(self.rfile.read(n) or b'{}')
            method, mid = msg.get('method'), msg.get('id')
            log(f'{tag}: <- {method}')
            if method == 'initialize':
                pv = (msg.get('params') or {}).get('protocolVersion', '2025-06-18')
                return self._send(200, {'jsonrpc': '2.0', 'id': mid, 'result': {
                    'protocolVersion': pv, 'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'silent-' + tag, 'version': '0'}}})
            if mid is None:
                return self._send(202)
            if method == 'tools/list':
                start = time.monotonic()
                hung_up = None
                self.connection.setblocking(False)
                while time.monotonic() - start < SILENCE:
                    try:
                        chunk = self.connection.recv(1, socket.MSG_PEEK)
                        if chunk == b'' and hung_up is None:
                            hung_up = time.monotonic() - start
                            log(f'{tag}: CLIENT HUNG UP after {hung_up:.1f}s of silence')
                    except BlockingIOError:
                        pass
                    except OSError as e:
                        if hung_up is None:
                            hung_up = time.monotonic() - start
                            log(f'{tag}: CLIENT CONNECTION ERROR after {hung_up:.1f}s: {e}')
                    time.sleep(0.5)
                self.connection.setblocking(True)
                try:
                    self._send(200, {'jsonrpc': '2.0', 'id': mid, 'result': {'tools': [{
                        'name': 'late_tool', 'description': 'answered after the silence',
                        'inputSchema': {'type': 'object', 'properties': {}}}]}})
                    log(f'{tag}: reply written after {time.monotonic() - start:.1f}s'
                        + (' (but the client had hung up)' if hung_up is not None else ''))
                except OSError as e:
                    log(f'{tag}: reply could NOT be written after {time.monotonic() - start:.1f}s: {e}')
                return
            return self._send(200, {'jsonrpc': '2.0', 'id': mid, 'result': {}})
    return H


def start_dummy(tag, port):
    srv = ThreadingHTTPServer(('127.0.0.1', port), make_handler(tag))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def api(port, password, path, timeout=30):
    auth = base64.b64encode(f'opencode:{password}'.encode()).decode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', headers={'Authorization': f'Basic {auth}'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    return d.get('data', d) if isinstance(d, dict) else d


def start_instance(tag, port, mcp_port, with_plugin):
    root = Path(tempfile.mkdtemp(prefix=f'mcp139-{tag}-'))
    cfg = root / 'config/opencode'
    (cfg / 'plugins').mkdir(parents=True)
    (cfg / 'opencode.json').write_text(json.dumps({
        '$schema': 'https://opencode.ai/config.json',
        'mcp': {'timeout': {'catalog': 3600000, 'execution': 3600000},
                'servers': {'silent': {'type': 'remote', 'codemode': False, 'oauth': False,
                                       'url': f'http://127.0.0.1:{mcp_port}/mcp'}}},
        'plugins': []}))
    if with_plugin:
        src = (REPO / 'plugins/mcp-fetch-timeout.ts').read_text()
        probe_log = root / 'wrapper-hits.log'
        src = src.replace('        state.hits++',
                          '        state.hits++\n'
                          f'        require("fs").appendFileSync({json.dumps(str(probe_log))}, `hit ${{key}} ${{Date.now()}}\\n`)')
        (cfg / 'plugins/mcp-fetch-timeout.ts').write_text(src)
    password = secrets.token_urlsafe(16)
    env = dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'),
               XDG_STATE_HOME=str(root / 'state'), OPENCODE_PASSWORD=password)
    env.pop('OPENCODE_SERVER_PASSWORD', None)
    out = (root / 'serve.log').open('wb')
    proc = subprocess.Popen([str(BINARY), 'serve', '--hostname', '127.0.0.1', '--port', str(port),
                             '--print-logs', '--log-level', 'info'], cwd=str(root), env=env,
                            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    log(f'{tag}: opencode serve pid {proc.pid} on {port}, home {root}, plugin={with_plugin}')
    return root, proc, password


def main():
    for p in (4098, 4099, 18901, 18902):
        if subprocess.run(['ss', '-H', '-ltn', f'sport = :{p}'], capture_output=True, text=True).stdout.strip():
            sys.exit(f'port {p} busy')
    log(f'silence = {SILENCE}s')
    start_dummy('A-plugin', 18901)
    start_dummy('B-none', 18902)
    insts = {'A-plugin': (4098, *start_instance('A-plugin', 4098, 18901, True)),
             'B-none': (4099, *start_instance('B-none', 4099, 18902, False))}
    # boot, then ask /api/mcp (a location boots lazily) so each connects
    for tag, (port, root, proc, pw) in insts.items():
        for _ in range(80):
            try:
                if api(port, pw, '/api/info', 2).get('version'):
                    break
            except Exception:
                time.sleep(0.5)
        try:
            log(f'{tag}: /api/mcp -> {json.dumps(api(port, pw, "/api/mcp", 20))[:300]}')
        except Exception as e:
            log(f'{tag}: /api/mcp error {e}')
    end = time.monotonic() + SILENCE + 90
    last = {}
    while time.monotonic() < end:
        for tag, (port, root, proc, pw) in insts.items():
            try:
                st = json.dumps(api(port, pw, '/api/mcp', 20))[:300]
            except Exception as e:
                st = f'error {e}'
            if last.get(tag) != st:
                log(f'{tag}: /api/mcp -> {st}')
                last[tag] = st
        time.sleep(10)
    for tag, (port, root, proc, pw) in insts.items():
        hits = root / 'wrapper-hits.log'
        log(f'{tag}: wrapper hits: ' + (hits.read_text().strip().replace('\n', ' | ') if hits.exists() else 'none (file absent)'))
        try:
            tools = api(port, pw, '/api/mcp', 20)
            log(f'{tag}: final /api/mcp {json.dumps(tools)[:400]}')
        except Exception as e:
            log(f'{tag}: final /api/mcp error {e}')
        text = (root / 'serve.log').read_text(errors='replace')
        mcp_lines = [l for l in text.splitlines() if 'mcp' in l.lower() or 'timed out' in l.lower() or 'timeout' in l.lower()]
        log(f'{tag}: serve.log mcp/timeout lines (last 15):\n    ' + '\n    '.join(mcp_lines[-15:]))
        os.killpg(proc.pid, 15)
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
        shutil.rmtree(root, ignore_errors=True)
    log('done')


if __name__ == '__main__':
    main()
