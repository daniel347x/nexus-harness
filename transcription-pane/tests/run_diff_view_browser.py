"""Run only the shared diff actual-DOM fixture on ephemeral HTTP, never the live pane."""
import functools
import html
import http.server
import json
from pathlib import Path
import re
import subprocess
import threading
import time

from browser_scratch import chrome_profile, scratch_dir, sweep_profiles


# @beacon[
#   id=opc-pane-tests@run_diff_view_browser.main,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Runs the shared diff DOM fixture with external receipts and a finally-cleaned profile; sweeps day-old crash leftovers.,
# ]
def main():
    repo = Path(__file__).resolve().parents[2]
    receipt = scratch_dir('receipts/diff-view-browser')
    sweep_profiles()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(repo))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(2)
    try:
        with chrome_profile('chrome-diff-') as profile:
            process = subprocess.run(['/mnt/c/Program Files/Google/Chrome/Application/chrome.exe',
                '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                '--virtual-time-budget=16000', '--window-size=1400,1000',
                '--user-data-dir=' + str(profile).replace('/mnt/e/', 'E:/').replace('/mnt/c/', 'C:/'), '--dump-dom',
                f'http://127.0.0.1:{server.server_port}/transcription-pane/tests/diff-view.html'],
                capture_output=True, timeout=45)
            (receipt / 'diff-view.dom').write_bytes(process.stdout)
            (receipt / 'diff-view.stderr').write_bytes(process.stderr)
            dom = process.stdout.decode('utf-8', errors='replace')
            match = re.search(r'<pre id="results">(.*?)</pre>', dom, re.S)
            checks = json.loads(html.unescape(match[1])) if match else {}
            ok = (process.returncode == 0 and 'data-test-passed="true"' in dom and
                  'data-test-passed="false"' not in dom and checks.get('failed') == 0)
            result = dict(exit=process.returncode, passed=ok, checks=checks, profile=str(profile))
            (receipt / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result, indent=2))
            return 0 if ok else 1
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    raise SystemExit(main())
