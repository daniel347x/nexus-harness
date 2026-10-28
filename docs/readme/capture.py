"""Serve only the local UI and invented README transport, then capture in isolated Chrome."""
import argparse
import hashlib
import http.server
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit, unquote

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRATCH = Path('/mnt/c/tmp/companion-readme-shots')


class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == '/readme-fixture.js':
            file = HERE / 'fixture.js'
        elif path.startswith('/nexus-md/'):
            file = self.server.nexus / path.removeprefix('/nexus-md/')
        elif path in ('/tool-record-text.js', '/tool-preview-core.js'):
            file = REPO / 'chrome-extension/codenomad-session-watcher' / path[1:]
        else:
            name = {'/': 'index.html', '/sessions': 'sessions.html', '/m': 'm.html'}.get(path, path[1:])
            file = REPO / 'transcription-pane/static' / name
        if not file.is_file():
            self.send_error(404)
            return
        raw = file.read_bytes()
        label = 'nexus/' + file.relative_to(self.server.nexus).as_posix() if file.is_relative_to(self.server.nexus) else file.relative_to(REPO).as_posix()
        self.server.sources[label] = hashlib.sha256(raw).hexdigest()
        if file.suffix == '.html':
            raw = raw.replace(b'<head>', b'<head><script src="/readme-fixture.js"></script>', 1)
        elif file.name == 'sessions.js':
            raw = raw.replace(b'const history=createClaudeHistory();', b'const history=createClaudeHistory();window.readmeHistory=history;')
        elif file.name == 'm.js':
            raw += b'\nwindow.readmePhone=phoneManager;\n'
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def windows(path):
    return str(path).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shots', nargs='+', default=['desktop-hero', 'program-view', 'phone-programs', 'phone-chat'])
    parser.add_argument('--nexus', type=Path, default=REPO.parent.parent / 'workflowy-nexus-mcp-tools/workflowy-glimpse-cache')
    args = parser.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    server.nexus = args.nexus.resolve()
    server.sources = {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(2)
    try:
        subprocess.run(['/mnt/c/Program Files/nodejs/node.exe', windows(HERE / 'capture.cjs'),
                        f'http://127.0.0.1:{server.server_port}', windows(HERE), windows(SCRATCH), *args.shots],
                       check=True, timeout=240)
        manifest_path = HERE / 'capture-manifest.json'
        previous = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {'shots': {}}
        reports = {shot: json.loads((SCRATCH / (shot + '-receipt.json')).read_text()) for shot in args.shots}
        for shot, report in reports.items():
            report['sha256'] = hashlib.sha256((HERE / (shot + '.png')).read_bytes()).hexdigest()
        manifest = {'content': 'Invented system-development records; not live execution evidence.',
                    'sourceCommit': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
                    'sourceTree': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
                    'shots': {**previous['shots'], **reports}, 'servedSourceSha256': dict(sorted(server.sources.items())),
                    'captureCodeSha256': {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in ('capture.sh', 'capture.py', 'capture.cjs', 'fixture.js')}}
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    finally:
        server.shutdown()
        server.server_close()
        # Chrome is closed by CDP before this cleanup. Retry briefly for Windows locks.
        for _ in range(10):
            try:
                shutil.rmtree(SCRATCH)
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(.5)


if __name__ == '__main__':
    main()
