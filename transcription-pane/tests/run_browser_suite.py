"""Run only the NAMED actual-markup suites (run_browser_checks.py's fixtures, flags and pass rule), never all of SUITES.

Each suite gets its own disposable Chrome profile under the shared scratch root and the same ephemeral loopback fixture server;
no production pane, host or credentials.
Usage: python3 transcription-pane/tests/run_browser_suite.py --receipt-dir ../oc-scratch/opencode-companion/receipts/a3-browser tool-inspector phone-viewer
"""
import argparse
import functools
import html
import http.server
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from run_browser_checks import SUITES, CDP_SUITES, DEVICE_SUITES, FixtureFiles, check_nexus_md
from browser_scratch import chrome_profile, receipt_dir, sweep_profiles


# @beacon[
#   id=opc-pane-tests@run_browser_suite.main,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Runs named actual-markup suites with external receipts and a finally-cleaned profile per suite; sweeps day-old crash leftovers.,
# ]
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suites', nargs='+', choices=SUITES)
    parser.add_argument('--receipt-dir', type=Path, required=True)
    parser.add_argument('--chrome', default='/mnt/c/Program Files/Google/Chrome/Application/chrome.exe')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    receipt = receipt_dir(args.receipt_dir)
    sweep_profiles()
    if {'glimpse-view', 'glimpse-lightbox', 'glimpse-lightbox-sheet', 'glimpse-lightbox-desktop', 'glimpse-lightbox-live',
        'glimpse-lightbox-compare'} & set(args.suites):
        check_nexus_md()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(FixtureFiles, directory=str(repo)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(2)  # WSL -> Windows loopback forwarding startup
    results = []
    try:
        for suite in args.suites:
            with chrome_profile('chrome-pane-check-') as profile:
                if suite in DEVICE_SUITES:
                    windows = lambda p: str(p).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/')
                    process = subprocess.run(['/mnt/c/Program Files/nodejs/node.exe',
                        windows(repo / 'transcription-pane/tests' / 'run_phone_device_browser.cjs'),
                        f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/{suite}.html',
                        windows(receipt), windows(profile), windows(args.chrome), suite], capture_output=True, timeout=150)
                    (receipt / (suite + '.stderr')).write_bytes(process.stderr)
                    checks_file = receipt / (suite + '.json')
                    checks = json.loads(checks_file.read_text()) if checks_file.is_file() else {}
                    ok = process.returncode == 0 and checks.get('failed') == 0 and (checks.get('passed') or 0) > 0
                    results.append(dict(suite=suite, exit=process.returncode, passed=ok,
                                        checks=checks.get('passed'), failures=checks.get('failed'), profile=str(profile)))
                    print(json.dumps(results[-1]), flush=True)
                    (receipt / 'browser.json').write_text(json.dumps(results, indent=2) + '\n')
                    continue
                if suite in CDP_SUITES:
                    windows = lambda p: str(p).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/')
                    process = subprocess.run(['/mnt/c/Program Files/nodejs/node.exe',
                        windows(repo / 'transcription-pane/tests' / 'run_tap_browser.cjs'),
                        f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/{suite}.html',
                        windows(receipt), windows(profile), windows(args.chrome), suite], capture_output=True, timeout=45)
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
                process = subprocess.run([args.chrome, '--headless=new', '--disable-gpu', '--no-first-run',
                    '--no-default-browser-check', '--virtual-time-budget=16000', '--window-size=1400,1000',
                    '--user-data-dir=' + str(profile).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/'), '--dump-dom',
                    f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/{suite}.html'],
                    capture_output=True, timeout=45)
                (receipt / (suite + '.dom')).write_bytes(process.stdout)
                (receipt / (suite + '.stderr')).write_bytes(process.stderr)
                dom = process.stdout.decode('utf-8', errors='replace')
                match = re.search(r'<pre id="results">(.*?)</pre>', dom, re.S)
                checks = json.loads(html.unescape(match[1])) if match else {}
                if isinstance(checks, list):
                    checks = dict(passed=sum(c.get('passed') is True for c in checks),
                                  failed=sum(c.get('passed') is not True for c in checks))
                ok = (process.returncode == 0 and 'data-test-passed="true"' in dom and
                      'data-test-passed="false"' not in dom and checks.get('failed') == 0)
                results.append(dict(suite=suite, exit=process.returncode, passed=ok, dataTestPassed='data-test-passed="true"' in dom,
                                    checks=checks.get('passed'), failures=checks.get('failed'), profile=str(profile)))
                print(json.dumps(results[-1]), flush=True)
                (receipt / 'browser.json').write_text(json.dumps(results, indent=2) + '\n')
    finally:
        server.shutdown()
        server.server_close()
    return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
