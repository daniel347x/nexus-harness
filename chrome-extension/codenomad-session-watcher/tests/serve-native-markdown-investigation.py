"""Read-only, isolated fixture origin; NEVER proxy CodeNomad/OpenCode APIs.

Run with --assets pointing to CodeNomad 0.20.1's public/assets directory.
This is diagnostic tooling, not shipped in the extension manifest.
"""
import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', required=True, type=Path)
    parser.add_argument('--port', type=int, default=18990)
    args = parser.parse_args()
    assets = args.assets.resolve()
    extension = Path(__file__).resolve().parents[1]
    if not (assets / 'markdown-B3IPZgpj.js').is_file():
        parser.error('Expected pinned CodeNomad 0.20.1 renderer asset markdown-B3IPZgpj.js')

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            path = path.split('?', 1)[0]
            if path.startswith('/assets/'):
                root, name = assets, path[len('/assets/'):]
            elif path.startswith('/tests/'):
                root, name = extension / 'tests', path[len('/tests/'):]
            else:
                root, name = extension, path.lstrip('/')
            # Only immediate static files. No traversal, directory listing or API forwarding.
            if not name or name != Path(name).name or not (root / name).is_file():
                return str(extension / '__fixture_not_found__')
            return str(root / name)

    print(f'Isolated fixture: http://127.0.0.1:{args.port}/tests/native-markdown-investigation.html', flush=True)
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
