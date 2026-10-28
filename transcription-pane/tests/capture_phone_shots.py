"""Capture iPhone-sized PNG screenshots of the phone page /m from its offline visual fixture.

Serves the repository on an ephemeral loopback port (fixture files only; no pane server,
host or credentials) and drives a disposable headless Chrome profile under the shared scratch root.
Usage: python3 transcription-pane/tests/capture_phone_shots.py --out ../oc-scratch/opencode-companion/receipts/phone-shots
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

from run_browser_checks import NexusMdAlias, check_nexus_md
from browser_scratch import chrome_profile, receipt_dir, sweep_profiles

VIEWS = ('chat', 'chat-new', 'chat-board', 'tool', 'tool-readable', 'tool-markdown', 'tool-nowrap', 'tool-diff', 'tool-diff-large', 'thinking', 'refined', 'draft-max', 'chat-max', 'more-menu',
         'refine-view', 'refine-view-prompt', 'first', 'new-manager', 'new-manager-options', 'new-manager-started', 'context', 'programs', 'programs-max',
         'programs-collapsed', 'programs-running', 'viewer-conductor', 'viewer-worker', 'viewer-opencode', 'tune', 'programs-glance', 'glance-large',
         'glimpse', 'glimpse-compact', 'glimpse-rows', 'glimpse-source', 'glimpse-large', 'glimpse-wide', 'glimpse-docks', 'glimpse-groups',
         'glimpse-history', 'glimpse-history-sheet', 'glimpse-root')
# Any view as glare-‹view›: outdoor glare mode on (the ☀ switch tapped first), its text swept for anything left dim.
GLARE = tuple('glare-' + view for view in VIEWS)
DEFAULT = VIEWS + tuple('glare-' + view for view in ('chat', 'chat-new', 'tool', 'tool-diff', 'thinking', 'more-menu', 'context', 'programs', 'programs-glance', 'glance-large', 'viewer-worker', 'tune',
                                                    'glimpse'))
SHOTS = VIEWS + GLARE


class Quiet(NexusMdAlias, http.server.SimpleHTTPRequestHandler):  # /nexus-md/: the View shots' shared renderer
    def log_message(self, *args):
        pass


# @beacon[
#   id=opc-pane-tests@capture_phone_shots.main,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Captures offline phone receipts outside the repo with a finally-cleaned profile per shot; sweeps day-old crash leftovers.,
# ]
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--chrome', default='/mnt/c/Program Files/Google/Chrome/Application/chrome.exe')
    parser.add_argument('--size', default='390,844', help='CSS pixels; an iPhone 15 is about 393x852')
    parser.add_argument('--scale', default='2')
    parser.add_argument('--shots', nargs='+', choices=SHOTS, default=DEFAULT)
    parser.add_argument('--sweep', action='store_true', help='also count dim text in the views without glare (glare-* views always check it)')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    out = receipt_dir(args.out)
    sweep_profiles()
    if any('glimpse' in shot for shot in args.shots):
        check_nexus_md()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(repo)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(2)  # WSL -> Windows loopback forwarding startup
    windows = lambda p: str(p).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/')
    failed = []
    try:
        for shot in args.shots:
            with chrome_profile('chrome-phone-shot-') as profile:
                target = out / ('phone-' + shot + '.png')
                result = subprocess.run([args.chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                                         '--hide-scrollbars', '--virtual-time-budget=8000', '--window-size=' + args.size,
                                         '--force-device-scale-factor=' + args.scale,
                                         '--user-data-dir=' + windows(profile), '--screenshot=' + windows(target), '--dump-dom',
                                         f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/phone-ui-demo.html?shot={shot}' + ('&sweep=1' if args.sweep else '')],
                                        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
                target.with_suffix('.stderr').write_text(result.stderr, encoding='utf-8')
                match = re.search(r'<script type="application/json" id="shot-report">([^<]*)</script>', result.stdout)
                report = json.loads(match[1]) if match else {'shot': shot, 'checks': [{'name': 'fixture completed', 'passed': False}]}
                report['chromeExit'] = result.returncode
                target.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
                print(target, target.is_file() and target.stat().st_size, json.dumps(report))
                if result.returncode or not target.is_file() or any(not check['passed'] for check in report['checks']):
                    failed.append(shot)
    finally:
        server.shutdown()
        server.server_close()
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
