"""View's local files: GET|HEAD /files/…, POST /api/files/meta and POST /api/files/list (live gallery folders and globs)
through the shared Handler guard, on scratch files."""
from email.utils import formatdate
import http.client
import json
import os
import pathlib
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from http.server import ThreadingHTTPServer
from urllib.parse import quote

from browser_scratch import scratch_dir

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import server

ORIGIN = "https://pane.example.ts.net"
AUTHORITY = "pane.example.ts.net"
LOGIN = "dan@example.test"  # Fixture only, never Dan's configuration.
BAKEOFF = pathlib.Path("/mnt/e/__daniel347x/__Dan_Root/__repos/workflowy-nexus-mcp-tools-wt-ui-opus/docs/ui-bakeoff")


def png(width, height):
    """A real, minimal PNG (one grey row repeated) of the given size."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def jpeg(width, height, orientation=None):
    """A JPEG's header segments only (SOI, an optional EXIF orientation, DQT, SOF0), enough for its size."""
    out = b"\xff\xd8"
    if orientation is not None:
        tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1) + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0) + b"\x00\x00\x00\x00"
        body = b"Exif\x00\x00" + tiff
        out += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    out += b"\xff\xdb" + struct.pack(">H", 67) + b"\x00" + b"\x01" * 64
    out += b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return out + b"\xff\xd9"


def webp(kind, width, height):
    if kind == "VP8X":
        body = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    elif kind == "VP8L":
        body = b"\x2f" + ((width - 1) | (height - 1) << 14).to_bytes(4, "little") + b"\x00" * 5
    else:
        body = b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", width, height) + b"\x00" * 4
    chunk = kind.encode() + struct.pack("<I", len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


class FilesHarness(unittest.TestCase):
    """Scratch files and a real Handler on an ephemeral loopback port."""
    def setUp(self):
        # External scratch on the Windows-accessible drive, so E:/… names the same file.
        self.temp = tempfile.TemporaryDirectory(dir=scratch_dir('fixtures/files-routes'))
        self.dir = pathlib.Path(self.temp.name)
        self.shot = self.dir / "My Shots" / "card dark.png"
        self.shot.parent.mkdir()
        self.shot.write_bytes(png(30, 20))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, route, method="GET", data=None, headers=(), host=None):
        raw = json.dumps(data).encode() if data is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.putrequest(method, route, skip_host=True)
        connection.putheader("Host", host or f"127.0.0.1:{self.port}")
        for name, value in headers:
            connection.putheader(name, value)
        if raw is not None:
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(raw)))
        connection.endheaders(raw)
        response = connection.getresponse()
        body = response.read()
        result = response.status, dict(response.getheaders()), body
        connection.close()
        return result

    def url(self, path):
        return "/files" + quote(str(path))

    def meta(self, paths, **kwargs):
        code, headers, body = self.request("/api/files/meta", "POST", {"paths": paths}, **kwargs)
        return code, json.loads(body)


class FilesRouteTests(FilesHarness):
    def test_get_streams_exact_bytes_with_revalidation_headers(self):
        code, headers, body = self.request(self.url(self.shot))
        self.assertEqual(code, 200)
        self.assertEqual(body, self.shot.read_bytes())
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["Content-Length"], str(len(body)))
        self.assertEqual(headers["Cache-Control"], "no-cache")
        st = self.shot.stat()
        self.assertEqual(headers["ETag"], f'"{st.st_mtime_ns:x}-{st.st_size:x}"')
        self.assertEqual(headers["Last-Modified"], formatdate(st.st_mtime, usegmt=True))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        code, headers, body = self.request(self.url(self.shot), "HEAD")
        self.assertEqual((code, body, headers["Content-Length"]), (200, b"", str(st.st_size)))

    def test_streams_a_file_larger_than_one_chunk(self):
        big = self.dir / "big.bin"
        big.write_bytes(os.urandom(server.FILE_CHUNK * 2 + 123))
        code, headers, body = self.request(self.url(big))
        self.assertEqual((code, body), (200, big.read_bytes()))
        self.assertEqual(headers["Content-Type"], "application/octet-stream")

    def test_conditional_requests_answer_304_until_the_file_changes(self):
        etag = self.request(self.url(self.shot))[1]["ETag"]
        for headers in ([("If-None-Match", etag)], [("If-None-Match", 'W/' + etag + ', "other"')], [("If-None-Match", "*")],
                        [("If-Modified-Since", formatdate(self.shot.stat().st_mtime + 5, usegmt=True))]):
            with self.subTest(headers=headers):
                code, reply, body = self.request(self.url(self.shot), headers=headers)
                self.assertEqual((code, body, reply["ETag"]), (304, b"", etag))
        code = self.request(self.url(self.shot), headers=[("If-Modified-Since", formatdate(self.shot.stat().st_mtime - 60, usegmt=True))])[0]
        self.assertEqual(code, 200, "older If-Modified-Since")
        code = self.request(self.url(self.shot), headers=[("If-Modified-Since", "not a date")])[0]
        self.assertEqual(code, 200, "unreadable If-Modified-Since")
        # An agent's regenerated screenshot: same path, new bytes, a new ETag; the old one no longer matches.
        time.sleep(0.01)
        self.shot.write_bytes(png(31, 20))
        code, reply, body = self.request(self.url(self.shot), headers=[("If-None-Match", etag)])
        self.assertEqual((code, body), (200, self.shot.read_bytes()))
        self.assertNotEqual(reply["ETag"], etag)

    def test_drive_letter_and_encoded_forms_name_the_same_file(self):
        posix = str(self.shot)
        self.assertTrue(posix.startswith("/mnt/e/"), posix)
        drive = "E:/" + posix[len("/mnt/e/"):]
        for route in ("/files/" + quote(drive), "/files/" + drive.replace(" ", "%20"), "/files/e:/" + quote(posix[len("/mnt/e/"):]),
                      "/files" + posix.replace(" ", "%20")):
            with self.subTest(route=route):
                code, _, body = self.request(route)
                self.assertEqual((code, body), (200, self.shot.read_bytes()))

    def test_missing_directory_and_bad_paths_are_named_404s(self):
        missing = self.dir / "nope.png"
        for path, words in ((missing, "no such file: " + str(missing)), (self.shot.parent, "is a directory: " + str(self.shot.parent))):
            with self.subTest(path=path):
                code, headers, body = self.request(self.url(path))
                self.assertEqual(code, 404)
                self.assertEqual(json.loads(body)["error"], words)
                self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("/files/%ff%fe")[0], 400)
        self.assertEqual(self.request("/files/mnt/e/a%00b")[0], 400)
        self.assertEqual(self.request(self.url(self.shot), "POST", {})[0], 405)
        self.assertEqual(self.request(self.url(self.shot), "OPTIONS")[0], 403)
        self.assertEqual(self.request("/api/files/meta", "OPTIONS")[0], 403)

    def test_same_guard_as_m_loopback_or_the_configured_serve_origin_only(self):
        route = self.url(self.shot)
        refused = ([("Sec-Fetch-Site", "cross-site")], [("Origin", "https://workflowy.com")], [("Sec-Fetch-Site", "same-site")])
        for headers in refused:
            with self.subTest(headers=headers):
                self.assertEqual(self.request(route, headers=headers)[0], 403)
        self.assertEqual(self.request(route, headers=[("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Dest", "image")])[0], 200)
        serve = [("Tailscale-User-Login", LOGIN)]
        self.assertEqual(self.request(route, headers=serve, host=AUTHORITY)[0], 403, "remote OFF")
        config = self.dir / "pane-remote.json"
        config.write_text(json.dumps({"origin": ORIGIN, "login": LOGIN}), encoding="utf-8")
        self.httpd.remote_origin_policy = server.load_remote_policy(config)
        code, _, body = self.request(route, headers=serve + [("Sec-Fetch-Site", "same-origin")], host=AUTHORITY)
        self.assertEqual((code, body), (200, self.shot.read_bytes()))
        self.assertEqual(self.request(route, headers=[("Tailscale-User-Login", "someone@else.test")], host=AUTHORITY)[0], 403)
        self.assertEqual(self.request(route, host=AUTHORITY)[0], 403, "no Serve identity")
        code, result = self.meta([str(self.shot)], headers=serve + [("Origin", ORIGIN)], host=AUTHORITY)
        self.assertEqual((code, result["files"][0]["exists"]), (200, True))
        self.assertEqual(self.meta([str(self.shot)], headers=[("Origin", "https://evil.example")])[0], 403)

    def test_meta_reads_sizes_from_headers_for_each_format(self):
        files = {"a.png": png(1500, 950), "b.gif": b"GIF89a" + struct.pack("<HH", 320, 200) + b"\x00" * 20,
                 "c.jpg": jpeg(1200, 800), "d.jpg": jpeg(1200, 800, orientation=6), "e.jpeg": jpeg(640, 480, orientation=1),
                 "f.webp": webp("VP8X", 1920, 1080), "g.webp": webp("VP8L", 333, 222), "h.webp": webp("VP8 ", 800, 600),
                 "i.bmp": b"BM" + b"\x00" * 16 + struct.pack("<ii", 64, -48) + b"\x00" * 30, "j.svg": b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                 "k.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 4}
        for name, data in files.items():
            (self.dir / name).write_bytes(data)
        code, result = self.meta([str(self.dir / name) for name in files])
        self.assertEqual(code, 200)
        sizes = {pathlib.Path(f["path"]).name: (f.get("width"), f.get("height")) for f in result["files"]}
        self.assertEqual(sizes, {"a.png": (1500, 950), "b.gif": (320, 200), "c.jpg": (1200, 800), "d.jpg": (800, 1200),
                                 "e.jpeg": (640, 480), "f.webp": (1920, 1080), "g.webp": (333, 222), "h.webp": (800, 600),
                                 "i.bmp": (64, 48), "j.svg": (None, None), "k.png": (None, None)})
        first = result["files"][0]
        st = (self.dir / "a.png").stat()
        self.assertEqual(first, {"path": str(self.dir / "a.png"), "url": "/files" + quote(str(self.dir / "a.png")), "exists": True,
                                 "bytes": st.st_size, "mtime": st.st_mtime, "type": "image/png", "width": 1500, "height": 950})
        self.assertEqual(result["files"][9]["type"], "image/svg+xml")

    def test_meta_reports_why_a_file_cannot_be_shown_in_request_order(self):
        drive = "E:/" + str(self.shot)[len("/mnt/e/"):]
        paths = [str(self.dir / "nope.png"), str(self.shot.parent), "relative/a.png", drive, str(self.shot), str(self.shot)]
        code, result = self.meta(paths)
        self.assertEqual(code, 200)
        rows = result["files"]
        self.assertEqual([r["path"] for r in rows], paths, "each row names the path as sent")
        self.assertEqual([r["exists"] for r in rows], [False, False, False, True, True, True])
        self.assertEqual([r.get("error") for r in rows[:3]], ["no such file", "is a directory", "not an absolute path"])
        self.assertEqual(rows[3]["url"], "/files" + quote(str(self.shot)), "E:/ maps to /mnt/e/")
        self.assertEqual((rows[4]["width"], rows[4]["height"]), (30, 20))
        self.assertIn("%20", rows[4]["url"])

    def test_meta_request_shape_is_strict(self):
        bad = ({}, {"paths": "x"}, {"paths": [1]}, {"paths": [""]}, {"paths": [], "extra": 1}, {"paths": ["/x"] * (server.FILES_META_LIMIT + 1)})
        for data in bad:
            with self.subTest(data=str(data)[:60]):
                code, _, body = self.request("/api/files/meta", "POST", data)
                self.assertEqual(code, 400, body[:200])
        self.assertEqual(self.meta([])[1], {"schemaVersion": 1, "files": []})
        self.assertEqual(self.request("/api/files/meta")[0], 405)
        self.assertEqual(self.request("/api/files/meta?x=1", "POST", {"paths": []})[0], 400)
        self.assertEqual(self.request("/api/files/other", "POST", {"paths": []})[0], 404)

    @unittest.skipUnless((BAKEOFF / "card-dark.png").is_file(), "the bake-off screenshots are not on this machine")
    def test_meta_sizes_on_the_bakeoff_screenshots(self):
        astra = BAKEOFF.parent.parent.parent / "workflowy-nexus-mcp-tools-wt-ui-astra" / "docs/ui-bakeoff/dark/narrow-reader.png"
        code, result = self.meta([str(BAKEOFF / "card-dark.png"), str(astra)])
        self.assertEqual(code, 200)
        self.assertEqual((result["files"][0]["width"], result["files"][0]["height"]), (1500, 950))
        if astra.is_file():
            self.assertEqual((result["files"][1]["width"], result["files"][1]["height"]), (600, 760))
        code, headers, body = self.request(self.url(BAKEOFF / "card-dark.png"))
        self.assertEqual((code, headers["Content-Type"], body[:8]), (200, "image/png", b"\x89PNG\r\n\x1a\n"))


class FilesListTests(FilesHarness):
    """POST /api/files/list: a live gallery line's folder or glob, expanded at render time (View's answer 5)."""
    def setUp(self):
        super().setUp()
        self.live = self.shot.parent / "live"
        (self.live / "sub").mkdir(parents=True)
        for name in ("shot-10.png", "shot-2.png", "Shot-1.PNG", "b.jpg", "a.webp", "README.md", ".hidden.png", "notes.txt"):
            (self.live / name).write_bytes(png(40, 25) if name.endswith(".png") or name.endswith(".PNG") else b"\xff\xd8x" if name == "b.jpg" else b"x")
        (self.live / "sub" / "deep 1.png").write_bytes(png(12, 8))
        (self.live / "folder.png").mkdir()  # a folder named like an image is not a file
        (self.dir / "empty").mkdir()

    def listing(self, paths, **kwargs):
        code, headers, body = self.request("/api/files/list", "POST", {"paths": paths}, **kwargs)
        return code, json.loads(body)

    def names(self, row):
        return [pathlib.Path(f["path"]).relative_to(self.live).as_posix() for f in row["files"]]

    def test_a_folder_lists_its_own_images_in_natural_name_order(self):
        code, result = self.listing([str(self.live) + "/"])
        self.assertEqual(code, 200)
        row = result["lists"][0]
        self.assertEqual((row["path"], row["kind"], row["total"]), (str(self.live) + "/", "folder", 5))
        self.assertEqual(self.names(row), ["a.webp", "b.jpg", "Shot-1.PNG", "shot-2.png", "shot-10.png"],
                         "images only (no README, text, dot-file, sub-folder or folder named .png), shot-2 before shot-10, case folded")
        first = next(f for f in row["files"] if f["path"].endswith("shot-2.png"))
        self.assertEqual({k: first[k] for k in ("exists", "type", "width", "height")}, {"exists": True, "type": "image/png", "width": 40, "height": 25})
        self.assertEqual(first["url"], "/files" + quote(first["path"]))
        self.assertIn("/My%20Shots/live/", first["url"], "a space is %20 in each file's url")
        self.assertNotIn("truncated", row)
        # a folder named without its trailing slash is a folder too
        self.assertEqual(self.names(self.listing([str(self.live)])[1]["lists"][0]), self.names(row))

    def test_a_glob_lists_what_it_matches(self):
        cases = {"*.png": ["shot-2.png", "shot-10.png"], "shot-?.png": ["shot-2.png"], "*": ["a.webp", "b.jpg", "Shot-1.PNG", "shot-2.png", "shot-10.png"],
                 "**/*.png": ["shot-2.png", "shot-10.png", "sub/deep 1.png"], "*/*.png": ["sub/deep 1.png"]}
        code, result = self.listing([str(self.live / pattern) for pattern in cases])
        self.assertEqual(code, 200)
        for (pattern, expected), row in zip(cases.items(), result["lists"]):
            with self.subTest(pattern=pattern):
                self.assertEqual((row["kind"], self.names(row), row["total"]), ("glob", expected, len(expected)))
        # [ is literal (a folder name with brackets), never a character class
        odd = self.dir / "run [1]"
        odd.mkdir()
        (odd / "x.png").write_bytes(png(2, 2))
        (self.dir / "run 1").mkdir()
        (self.dir / "run 1" / "y.png").write_bytes(png(2, 2))
        row = self.listing([str(odd / "*.png")])[1]["lists"][0]
        self.assertEqual([pathlib.Path(f["path"]).name for f in row["files"]], ["x.png"])

    def test_empty_missing_and_wrong_paths_say_why_in_request_order(self):
        missing = self.dir / "nope"
        paths = [str(self.dir / "empty") + "/", str(self.dir / "empty" / "*.png"), str(missing) + "/", str(missing / "*.png"),
                 str(self.shot) + "/", str(self.shot), "relative/shots/", str(self.live) + "/"]
        code, result = self.listing(paths)
        self.assertEqual(code, 200)
        rows = result["lists"]
        self.assertEqual([r["path"] for r in rows], paths, "each row names the path as sent")
        self.assertEqual([(r.get("kind"), r["total"], r["files"]) for r in rows[:2]], [("folder", 0, []), ("glob", 0, [])], "empty is no error")
        self.assertEqual([r.get("error") for r in rows[2:7]], ["no such folder: " + str(missing) + "/", "no such folder: " + str(missing),
                                                               "not a folder: " + str(self.shot), "not a folder or a glob: " + str(self.shot),
                                                               "not an absolute path"])
        self.assertTrue(all(r["files"] == [] and r["total"] == 0 for r in rows[2:7]))
        self.assertEqual(rows[7]["total"], 5, "a bad path never spoils the next one")

    def test_drive_letter_form_lists_the_same_folder(self):
        posix = str(self.live) + "/"
        self.assertTrue(posix.startswith("/mnt/e/"), posix)
        drive = "E:/" + posix[len("/mnt/e/"):]
        code, result = self.listing([drive, drive + "*.png", "e:/" + posix[len("/mnt/e/"):]])
        self.assertEqual(code, 200)
        rows = result["lists"]
        self.assertEqual(rows[0]["path"], drive, "the row names the path as sent")
        self.assertEqual(self.names(rows[0]), ["a.webp", "b.jpg", "Shot-1.PNG", "shot-2.png", "shot-10.png"])
        self.assertTrue(rows[0]["files"][0]["path"].startswith("/mnt/e/"), "each file as a WSL path, the one View maps to /files/")
        self.assertEqual(self.names(rows[1]), ["shot-2.png", "shot-10.png"])
        self.assertEqual(self.names(rows[2]), self.names(rows[0]))

    def test_at_most_the_limit_files_a_call_the_cut_rows_say_truncated(self):
        old = server.FILES_LIST_LIMIT
        server.FILES_LIST_LIMIT = 3
        try:
            rows = self.listing([str(self.live) + "/", str(self.live / "*.png")])[1]["lists"]
        finally:
            server.FILES_LIST_LIMIT = old
        self.assertEqual([(len(r["files"]), r["total"], r.get("truncated")) for r in rows], [(3, 5, True), (0, 2, True)])

    def test_same_guard_and_a_strict_request(self):
        serve = [("Tailscale-User-Login", LOGIN)]
        self.assertEqual(self.listing([str(self.live) + "/"], headers=[("Sec-Fetch-Site", "cross-site")])[0], 403)
        self.assertEqual(self.listing([str(self.live) + "/"], headers=[("Origin", "https://workflowy.com")])[0], 403)
        self.assertEqual(self.listing([str(self.live) + "/"], headers=serve, host=AUTHORITY)[0], 403, "remote OFF")
        config = self.dir / "pane-remote.json"
        config.write_text(json.dumps({"origin": ORIGIN, "login": LOGIN}), encoding="utf-8")
        self.httpd.remote_origin_policy = server.load_remote_policy(config)
        code, result = self.listing([str(self.live) + "/"], headers=serve + [("Origin", ORIGIN)], host=AUTHORITY)
        self.assertEqual((code, result["lists"][0]["total"]), (200, 5))
        self.assertEqual(self.listing([str(self.live) + "/"], headers=[("Tailscale-User-Login", "someone@else.test")], host=AUTHORITY)[0], 403)
        for data in ({}, {"paths": "x"}, {"paths": [1]}, {"paths": [""]}, {"paths": [], "extra": 1}, {"paths": ["/x/"] * (server.FILES_LIST_PATHS + 1)}):
            with self.subTest(data=str(data)[:60]):
                self.assertEqual(self.request("/api/files/list", "POST", data)[0], 400)
        self.assertEqual(self.listing([])[1], {"schemaVersion": 1, "lists": []})
        self.assertEqual(self.request("/api/files/list")[0], 405)
        self.assertEqual(self.request("/api/files/list?x=1", "POST", {"paths": []})[0], 400)
        self.assertEqual(self.request("/api/files/list", "OPTIONS")[0], 403)

    @unittest.skipUnless((BAKEOFF / "card-dark.png").is_file(), "the bake-off screenshots are not on this machine")
    def test_the_bakeoff_folder_lists_its_screenshots(self):
        row = self.listing([str(BAKEOFF) + "/"])[1]["lists"][0]
        names = [pathlib.Path(f["path"]).name for f in row["files"]]
        self.assertEqual(len(names), len([p for p in BAKEOFF.iterdir() if p.suffix == ".png"]))
        self.assertIn("card-dark.png", names)
        self.assertTrue(all((f["width"], f["height"]) == (1500, 950) for f in row["files"] if f["path"].endswith("card-dark.png")))


class NexusLightboxAssetTests(unittest.TestCase):
    def test_lightbox_and_photoswipe_are_allowlisted_exactly(self):
        for name in ("glimpse-lightbox.js", "glimpse-lightbox.css", "photoswipe/photoswipe.umd.min.js", "photoswipe/photoswipe.css"):
            self.assertIn(name, server.NEXUS_MD_FILES)
        self.assertNotIn("photoswipe/LICENSE", server.NEXUS_MD_FILES)


if __name__ == "__main__":
    unittest.main()
