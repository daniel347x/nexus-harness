"""Actual-markup checks with inert IO, isolated Chrome and ephemeral loopback HTTP.

HTTP supplies real same-origin postMessage semantics (unlike file://).
No production pane, native host, credentials or browser profile is used.
"""
import argparse
import functools
import html
import http.server
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

from browser_scratch import chrome_profile, receipt_dir, sweep_profiles

SUITES = ('claude-live-sessions', 'claude-manager', 'claude-sessions', 'claude-mode',
          'claude-voice', 'tool-inspector', 'claude-target-stability', 'message-markdown',
          'claude-follow', 'refine-context', 'chatgpt-accounts', 'phone-manager', 'phone-programs', 'phone-viewer', 'phone-paging', 'phone-thinking', 'phone-glare', 'opencode-conversations',
          'teams-break', 'diff-view', 'glimpse-view', 'handle-links', 'view-history', 'desktop-view', 'phone-caret', 'glimpse-lightbox', 'glimpse-lightbox-sheet', 'glimpse-lightbox-desktop',
          'glimpse-lightbox-live', 'glimpse-lightbox-compare', 'phone-composer', 'desktop-toast',
          'opencode-managers', 'phone-opencode-managers', 'phone-keyboard-reading', 'history', 'ribbon-sidebar')
# Suites that need a real-time / trusted-event browser run through Windows Node + Chrome CDP: run_tap_browser.cjs.
CDP_SUITES = ('phone-caret', 'desktop-toast')
# Suites that need REAL device metrics / touch emulation run through Windows Node + Chrome CDP: run_phone_device_browser.cjs.
DEVICE_SUITES = ('phone-composer', 'phone-keyboard-reading', 'phone-thinking')


def nexus_md_root():
    """Test-only: the NEXUS checkout whose workflowy-glimpse-cache/ the pane serves at /nexus-md/ (server.py's B2 route).

    NEXUS_MD_CHECKOUT overrides; otherwise the pane's own startup setting (pane-glimpse.json), else NEXUS main.
    """
    checkout = os.environ.get('NEXUS_MD_CHECKOUT')
    if not checkout:
        try:
            checkout = json.loads((Path.home() / '.config/opencode-companion/pane-glimpse.json').read_text())['nexus_checkout']
        except FileNotFoundError:
            checkout = '/mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools'
    return (Path(checkout) / 'workflowy-glimpse-cache').resolve()


def check_nexus_md():
    """Say loudly when the shared renderer is absent: glimpse-view then fails on its asset checks, never on a copy."""
    root = nexus_md_root()
    if not (root / 'glimpse-md-render.js').is_file():
        print(f'NEXUS renderer missing: {root}/glimpse-md-render.js; set NEXUS_MD_CHECKOUT to a checkout that has it '
              '(glimpse-view and the glimpse shots will fail)', file=sys.stderr, flush=True)
    return root


LIGHTBOX_FILES = Path(__file__).resolve().parent / 'fixtures' / 'lightbox'
LIGHTBOX_ROOT = '/files/mnt/z/lightbox-fixture/'  # the lightbox fixture's images, as the pane's /files/ route names them


class NexusMdAlias:
    """Serve /nexus-md/<file> as the exact bytes of the configured NEXUS checkout (never a checked-in copy), and the
    lightbox fixture's images at /files/mnt/z/lightbox-fixture/… (test-only: the pane's real /files/ route is server.py's)."""
    def translate_path(self, path):
        clean = urlsplit(path).path
        if clean.startswith('/nexus-md/'):
            root = nexus_md_root()
            target = (root / unquote(clean[len('/nexus-md/'):])).resolve()
            return str(target) if root in target.parents else str(root / 'not-allowed')
        if clean.startswith(LIGHTBOX_ROOT):
            target = (LIGHTBOX_FILES / unquote(clean[len(LIGHTBOX_ROOT):])).resolve()
            return str(target) if LIGHTBOX_FILES in target.parents else str(LIGHTBOX_FILES / 'not-allowed')
        return super().translate_path(path)


class FixtureFiles(NexusMdAlias, http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/sessions'):
            self.send_response(204)  # Initial lazy navigation; fixture replaces it.
            self.end_headers()
            return
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass


# @beacon[
#   id=opc-pane-tests@run_browser_checks.main,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Runs actual-markup suites on ephemeral HTTP with external receipts and a finally-cleaned Chrome profile per suite; sweeps day-old crash leftovers.,
# ]
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt-dir', type=Path, required=True)
    parser.add_argument('--chrome', default='/mnt/c/Program Files/Google/Chrome/Application/chrome.exe')
    parser.add_argument('--real-time-markdown', action='store_true',
                        help='Run Markdown via Windows Node/Chrome CDP without virtual time for real timing receipts')
    parser.add_argument('--suite', action='append', choices=SUITES,
                        help='Run only this suite (repeatable); default: every suite')
    args = parser.parse_args()
    suites = tuple(args.suite or SUITES)
    repo = Path(__file__).resolve().parents[2]
    receipt = receipt_dir(args.receipt_dir)
    sweep_profiles()
    check_nexus_md()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(FixtureFiles, directory=str(repo)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(2)  # Fixture-only: WSL's Windows loopback port forwarding startup.
    results = []
    try:
        for suite in suites:
            with chrome_profile('chrome-pane-check-') as profile:
                if suite in DEVICE_SUITES:
                    windows = lambda p: str(p).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/')
                    process = subprocess.run(['/mnt/c/Program Files/nodejs/node.exe',
                        windows(repo / 'transcription-pane/tests' / 'run_phone_device_browser.cjs'),
                        f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/{suite}.html',
                        windows(receipt), windows(profile), windows(args.chrome), suite], capture_output=True, timeout=150)
                    (receipt / (suite + '.stderr')).write_bytes(process.stderr)
                    checks_file = receipt / (suite + '.json')
                    _raw = checks_file.read_text().strip() if checks_file.is_file() else ''
                    try: checks = json.loads(_raw) if _raw else {}
                    except json.JSONDecodeError: checks = {}
                    ok = process.returncode == 0 and checks.get('failed') == 0 and (checks.get('passed') or 0) > 0
                    results.append(dict(suite=suite, exit=process.returncode, passed=ok,
                                        checks=checks.get('passed'), failures=checks.get('failed'), profile=str(profile)))
                    print(json.dumps(results[-1]), flush=True)
                    (receipt / 'browser.json').write_text(json.dumps(results, indent=2) + '\n')
                    continue
                if suite == 'message-markdown' and args.real_time_markdown or suite in CDP_SUITES:
                    windows = lambda p: str(p).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/')
                    driver = 'run_tap_browser.cjs' if suite in CDP_SUITES else 'run_markdown_browser.cjs'
                    process = subprocess.run(['/mnt/c/Program Files/nodejs/node.exe',
                        windows(repo / 'transcription-pane/tests' / driver),
                        f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/{suite}.html',
                        windows(receipt), windows(profile), windows(args.chrome), suite], capture_output=True, timeout=45)
                    (receipt / (suite + '.stderr')).write_bytes(process.stderr)
                    checks_file = receipt / (suite + '.json')
                    _raw = checks_file.read_text().strip() if checks_file.is_file() else ''
                    try: checks = json.loads(_raw) if _raw else {}
                    except json.JSONDecodeError: checks = {}
                    ok = process.returncode == 0 and checks.get('failed') == 0 and (checks.get('passed') or 0) > 0
                    results.append(dict(suite=suite, exit=process.returncode, passed=ok,
                                        checks=checks.get('passed'), failures=checks.get('failed'),
                                        timings=checks.get('timings'), profile=str(profile)))
                    print(json.dumps(results[-1]), flush=True)
                    (receipt / 'browser.json').write_text(json.dumps(results, indent=2) + '\n')
                    continue
                process = subprocess.run([args.chrome, '--headless=new', '--disable-gpu', '--no-first-run',
                    '--no-default-browser-check', '--virtual-time-budget=16000', '--window-size=1400,1000',
                    '--user-data-dir=' + str(profile).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/'), '--dump-dom',
                    f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/{suite}.html'],
                    capture_output=True, timeout=45)
                (receipt / (suite + '.dom')).write_bytes(process.stdout)
                (receipt / (suite + '.stderr')).write_bytes(process.stderr)
                dom = process.stdout.decode('utf-8', errors='replace')
                match = re.search(r'<pre id="results">(.*?)</pre>', dom, re.S)
                try: checks = json.loads(html.unescape(match[1])) if match else {}
                except json.JSONDecodeError: checks = {}
                if isinstance(checks, list):
                    checks = dict(passed=sum(c.get('passed') is True for c in checks),
                                  failed=sum(c.get('passed') is not True for c in checks))
                ok = (process.returncode == 0 and 'data-test-passed="true"' in dom and
                      'data-test-passed="false"' not in dom and checks.get('failed') == 0)
                results.append(dict(suite=suite, exit=process.returncode, passed=ok,
                                    checks=checks.get('passed'), failures=checks.get('failed'), profile=str(profile)))
                print(json.dumps(results[-1]), flush=True)
                (receipt / 'browser.json').write_text(json.dumps(results, indent=2) + '\n')
    finally:
        server.shutdown()
        server.server_close()
    return 0 if len(results) == len(suites) and all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
