"""Capture PNG screenshots of the Claude-mode right pane from the offline visual fixture.

Serves the repository on an ephemeral loopback port (fixture files only; no pane server,
host or credentials) and drives a disposable headless Chrome profile under the shared scratch root.
Usage: python3 transcription-pane/tests/capture_pane_shots.py --out ../oc-scratch/opencode-companion/receipts/pane-round2/shots
"""
import argparse
import functools
import http.server
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from browser_scratch import chrome_profile, receipt_dir, sweep_profiles

SHOTS = ('collapsed', 'expanded', 'sidebar', 'paused', 'activity', 'folded', 'hover', 'child',
         'program-collapsed', 'program-expanded', 'program-scrolled', 'program-home')


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


# @beacon[
#   id=opc-pane-tests@capture_pane_shots.main,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Captures offline pane receipts outside the repo with a finally-cleaned profile per shot; sweeps day-old crash leftovers.,
# ]
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--chrome', default='/mnt/c/Program Files/Google/Chrome/Application/chrome.exe')
    parser.add_argument('--size', default='1500,950')
    parser.add_argument('--scale', default='1', help='device pixels per CSS pixel (2 for a sharp crop of small text)')
    parser.add_argument('--shots', nargs='+', choices=SHOTS, default=SHOTS)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    out = receipt_dir(args.out)
    sweep_profiles()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(repo)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(2)  # WSL -> Windows loopback forwarding startup
    windows = lambda p: str(p).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/')
    try:
        for shot in args.shots:
            with chrome_profile('chrome-pane-shot-') as profile:
                target = out / ('right-pane-' + shot + '.png')
                result = subprocess.run([args.chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                         '--hide-scrollbars', '--virtual-time-budget=6000', '--window-size=' + args.size,
                                         '--force-device-scale-factor=' + args.scale, '--user-data-dir=' + windows(profile), '--screenshot=' + windows(target), '--dump-dom',
                                         f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/pane-ui-demo.html?shot={shot}'],
                                        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
                target.with_suffix('.html').write_text(result.stdout, encoding='utf-8')
                target.with_suffix('.stderr').write_text(result.stderr, encoding='utf-8')
                match = re.search(r'<script type="application/json" id="shot-report">([^<]*)</script>', result.stdout)
                report = json.loads(match[1]) if match else {'shot': shot, 'checks': [{'name': 'fixture completed', 'passed': False}]}
                report['chromeExit'] = result.returncode
                target.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                print(target, target.is_file() and target.stat().st_size, report)
                if result.returncode or not target.is_file() or any(not check['passed'] for check in report['checks']):
                    raise RuntimeError('offline screenshot/DOM checks failed: ' + shot)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
