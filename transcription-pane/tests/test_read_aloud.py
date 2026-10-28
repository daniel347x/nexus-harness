"""Read Aloud offline: the paragraph chunker, the settings, the ElevenLabs voice, the saved results,
the `before` cut of Refine's context readers, and the guarded `/api/read-aloud/*` routes.

Everything here is inert: a temporary state root, a temporary `elevenlabs.json`, a fake voice and a fake
HTTP opener. No real key, no network call, no ElevenLabs request, no browser.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from http.server import ThreadingHTTPServer

TESTS = pathlib.Path(__file__).resolve().parent
for entry in (TESTS.parent, TESTS):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import opencode_managers as om  # noqa: E402
import read_aloud as ra  # noqa: E402
import server  # noqa: E402
from claude_live import ClaudeFacade  # noqa: E402
from claude_reader import ClaudeReader, ReaderError  # noqa: E402
from opencode_conversation import row_ref  # noqa: E402
from test_claude_live import InertHost  # noqa: E402
from test_claude_reader import SESSION, record, uid  # noqa: E402
from test_opencode_managers import SID, Proto, assistant_message, manager_session, text_part, user_message  # noqa: E402

SEPARATOR = "\n\n---\n\n"
KEY = {"backend": "claude", "conversation": "conv-1", "message": "msg-1", "part": "text"}
SOURCE_HASH = hashlib.sha256(b"the message that was rewritten").hexdigest()
# Three paragraphs, each over half the 300-char chunk size: three chunks, never merged.
PARAGRAPHS = ["A" * 150, "B" * 150, "C" * 150]
TEXT = "\n\n".join(PARAGRAPHS)
PAYLOAD = b"ID3\x03\x00\x00\x00" + b"mp3" * 16000


def sentences(count, width=80):
    """`count` ordinary space-separated sentences, each about `width` characters and ending in a full stop."""
    out = []
    for number in range(count):
        body = "Sentence %02d speaks about the Manager's work and keeps going" % number
        out.append((body + " " + "x" * max(0, width - len(body) - 2)).strip() + ".")
    return " ".join(out)


class FakeConfig:
    """A settings stand-in: `load` answers a fixed dict or raises, and every load is counted."""

    def __init__(self, data=None, error=None):
        self.data, self.error, self.loads = data, error, 0

    def load(self):
        self.loads += 1
        if self.error is not None:
            raise self.error
        return dict(self.data)

    def public(self):
        if self.error is not None:
            return {"configured": False, "error": str(self.error)}
        data = self.data
        return {"configured": True, "voiceId": data["voice_id"], "voiceName": data.get("voice_name"),
                "modelId": data["model_id"], "playbackRate": data["playback_rate"],
                "chunkChars": data["chunk_chars"]}


class FakeVoice:
    """One inert synthesis stand-in: records each call, optionally blocks on `gate`."""

    def __init__(self, payload=PAYLOAD, gate=None, started=None, error=None):
        self.payload, self.gate, self.started, self.error = payload, gate, started, error
        self.calls = []

    def synthesize(self, config, voice, text, previous_text="", next_text=""):
        self.calls.append({"config": config, "voice": voice, "text": text,
                           "previous": previous_text, "next": next_text})
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            self.gate.wait(10)
        if self.error is not None:
            raise self.error
        return self.payload, len(text)


class Clock:
    """A monotonic stand-in for time.time, so `usedAt` ordering is exact."""

    def __init__(self, start=1_800_000_000.0):
        self.now, self.lock = start, threading.Lock()

    def __call__(self):
        with self.lock:
            self.now += 1
            return self.now


def fixed_config(**overrides):
    data = {**ra.DEFAULTS, "api_key": "offline-fixture-not-a-key", "voice_id": "voice-fixture", "chunk_chars": 300}
    data.update(overrides)
    return FakeConfig(data)


class ChunkTests(unittest.TestCase):
    """split_chunks: the TypingMind widget's elevenBuildChunks, paragraph by paragraph."""

    def test_consecutive_paragraphs_merge_while_they_fit_under_the_limit(self):
        self.assertEqual(ra.split_chunks("alpha\n\nbeta\n\ngamma", 12), ["alpha\n\nbeta", "gamma"])
        self.assertEqual(ra.split_chunks("alpha\n\nbeta\n\ngamma", 100), ["alpha\n\nbeta\n\ngamma"])
        # A paragraph exactly at the limit stays whole and takes no neighbour.
        self.assertEqual(ra.split_chunks("a" * 10 + "\n\nb", 10), ["a" * 10, "b"])

    def test_blank_lines_with_spaces_or_tabs_still_separate_paragraphs(self):
        self.assertEqual(ra.split_chunks("one\n  \t\ntwo", 100), ["one\n\ntwo"])
        self.assertEqual(ra.split_chunks("a\n\t\nb", 100), ["a\n\nb"])
        # A run of blank lines collapses into one paragraph break; the merged chunk keeps one "\n\n".
        self.assertEqual(ra.split_chunks("x" * 8 + "\n\n\n\n" + "y" * 3, 100), ["x" * 8 + "\n\nyyy"])

    def test_an_over_long_paragraph_is_cut_at_a_sentence_end(self):
        chunks = ra.split_chunks("One two three. Four five six.", 20)
        self.assertEqual(chunks, ["One two three.", "Four five six."])
        self.assertTrue(all(0 < len(chunk) <= 20 for chunk in chunks))

    def test_an_over_long_paragraph_without_a_sentence_end_is_cut_at_a_space(self):
        chunks = ra.split_chunks("aaaa bbbb cccc dddd eeee", 20)
        self.assertEqual(chunks, ["aaaa bbbb cccc dddd", "eeee"])
        self.assertTrue(all(0 < len(chunk) <= 20 for chunk in chunks))
        # A sentence end before half the window is ignored in favour of the last space.
        self.assertEqual(ra.split_chunks("Hi. " + "z" * 40, 30), ["Hi.", "z" * 30, "z" * 10])

    def test_a_long_paragraph_flushes_the_pending_chunk_and_keeps_the_next_short_one(self):
        self.assertEqual(ra.split_chunks("s" * 25 + "\n\nshort", 20), ["s" * 20, "s" * 5, "short"])

    def test_whitespace_only_text_has_no_chunks(self):
        for text in ("", " ", " \n \t\n \n", "\n\n"):
            with self.subTest(text=text):
                self.assertEqual(ra.split_chunks(text, 100), [])

    def test_every_chunk_stays_inside_the_limit_for_a_mixed_document(self):
        text = "\n\n".join(["word " * 40, "Q. " + "y" * 300, "short", "tail " * 50])
        chunks = ra.split_chunks(text, 64)
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= 64 for chunk in chunks), [len(chunk) for chunk in chunks])


class QuickStartTests(unittest.TestCase):
    """quick_start: a long opening is cut short so the first audio arrives sooner (the measured ~322-char part)."""

    def test_a_short_opening_is_left_alone(self):
        for chunks in (["short", "later"], ["z" * 525, "t"], ["z" * 600, "t"]):
            with self.subTest(lengths=[len(chunk) for chunk in chunks]):
                self.assertEqual(ra.quick_start(list(chunks), 350), chunks)
        self.assertEqual(ra.quick_start([], 350), [])

    def test_a_long_opening_is_cut_at_its_last_sentence_end_inside_the_first_part(self):
        head = sentences(10)
        chunks = ra.quick_start([head, "tail"], 350)
        self.assertEqual(len(chunks), 3)
        self.assertLessEqual(len(chunks[0]), 350)
        self.assertTrue(chunks[0].endswith("."))
        self.assertEqual(chunks[0] + " " + chunks[1], head)  # only the cut's space goes
        self.assertEqual(chunks[2], "tail")                  # the later parts keep their order

    def test_a_paragraph_break_is_a_legitimate_cut(self):
        head = "a" * 200 + "\n\n" + "b" * 400
        self.assertEqual(ra.quick_start([head, "tail"], 350), ["a" * 200, "b" * 400, "tail"])

    def test_an_unbreakable_or_too_late_opening_is_left_alone(self):
        # "Hi." is inside the first 40% of the window: cutting there would make a stub, not a part.
        for head in ("Hi. " + "y" * 600, "z" * 900):
            with self.subTest(head=head[:12]):
                self.assertEqual(ra.quick_start([head, "tail"], 350), [head, "tail"])

    def test_zero_disables_the_short_part(self):
        for first in (0, None):
            with self.subTest(first=first):
                self.assertEqual(ra.quick_start(["z" * 900, "tail"], first), ["z" * 900, "tail"])

    def test_the_measured_opening_of_a_long_reply_is_about_three_hundred_characters(self):
        head = sentences(9)  # ~730 characters: one chunk under the default 1000, far over 1.5 x 350
        self.assertGreater(len(head), 350 * 1.5)
        chunks = ra.quick_start(ra.split_chunks(head, 1000), 350)
        self.assertEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks[0]), 350)
        self.assertGreaterEqual(len(chunks[0]), 350 * 0.4)
        self.assertEqual(chunks[0] + " " + chunks[1], head)


class ListeningBlockTests(unittest.TestCase):
    def test_paragraphs_are_independent_even_when_they_would_fit_together(self):
        self.assertEqual(ra.listening_chunks("First point.\n\nSecond point.\n\nThird point.", 1000, 350),
                         ["First point.", "Second point.", "Third point."])

    def test_headings_stay_with_prose_even_without_blank_lines_and_never_share_a_previous_block(self):
        text = "Opening.\n## The plan\n\nThe first paragraph.\n\nAnother paragraph.\n### Tests\nA final paragraph."
        self.assertEqual(ra.listening_chunks(text, 1000, 250),
                         ["Opening.", "## The plan\n\nThe first paragraph.", "Another paragraph.", "### Tests\n\nA final paragraph."])
        self.assertEqual(ra.listening_chunks("## Plan\n\n### Now\n\nRead this.", 1000, 250),
                         ["## Plan\n\n### Now\n\nRead this."])

    def test_long_paragraphs_keep_every_word_with_a_short_opening_and_no_orphan_heading(self):
        text = "## A title\n\n" + sentences(24)
        chunks = ra.listening_chunks(text, 1000, 250)
        self.assertTrue(chunks[0].startswith("## A title\n\nSentence"))
        self.assertLessEqual(len(chunks[0]), 250)
        self.assertTrue(all(len(chunk) <= ra.BLOCK_CHARS for chunk in chunks))
        self.assertEqual(" ".join(" ".join(chunks).split()), " ".join(text.split()))
        self.assertTrue(all(len(c) <= 300 for c in ra.listening_chunks(text, 300, 200)))

    def test_paragraph_separation_does_not_undo_the_old_fast_first_sentence_cut(self):
        opening = sentences(3)
        text = opening + "\n\n" + sentences(6)
        legacy = ra.quick_start(ra.split_chunks(text, 1000), 250)
        blocks = ra.listening_chunks(text, 1000, 250)
        self.assertTrue(legacy[0].startswith(blocks[0]))
        self.assertLessEqual(len(blocks[0]), len(legacy[0]))
        headed = ra.listening_chunks("## Playback\n\n" + text, 1000, 250)
        self.assertEqual(headed[0], "## Playback\n\n" + blocks[0])

    def test_a_heading_only_pass_is_still_a_spoken_label_and_empty_input_has_no_blocks(self):
        self.assertEqual(ra.listening_chunks("## A title", 1000, 250), ["## A title"])
        self.assertEqual(ra.speech_text("## A title"), "A title.")
        self.assertEqual(ra.listening_chunks(" \n\n ", 1000, 250), [])

    def test_voice_has_plain_titles_once_and_no_heading_or_inline_markdown(self):
        text = "## **The plan** ##\n\nRead the `new` [player](https://example.test) &amp; _listen_.\n\n### Next?\n\n> Not **markup**."
        spoken = ra.speech_text(text)
        self.assertEqual(spoken, "The plan.\n\nRead the new player & listen.\n\nNext?\n\nNot markup.")
        self.assertNotRegex(spoken, r"[#*`_]|heading|https://")
        self.assertEqual(ra.speech_text("## &#35; **Plan**\n\n&lt;b&gt;Ready&lt;/b&gt;"), "Plan.\n\nReady")


class KeyTests(unittest.TestCase):
    def test_a_key_is_backend_conversation_message_and_part(self):
        self.assertEqual(ra.check_key(dict(KEY)), KEY)
        self.assertEqual(ra.check_key({**KEY, "part": "thinking:12"})["part"], "thinking:12")
        self.assertEqual(ra.check_key({**KEY, "backend": "opencode"})["backend"], "opencode")
        self.assertIsNot(ra.check_key(dict(KEY)), KEY)  # a normalized copy, never the caller's dict

    def test_a_bad_key_is_a_schema_error(self):
        bad = [None, [], {k: KEY[k] for k in ("backend", "conversation", "message")},
               {**KEY, "extra": "x"}, {**KEY, "backend": "chatgpt"}, {**KEY, "part": "text2"},
               {**KEY, "part": "prose"}, {**KEY, "conversation": ""}, {**KEY, "message": "a/b"},
               {**KEY, "message": 7}, {**KEY, "part": "thinking:"}]
        for key in bad:
            with self.subTest(key=key):
                with self.assertRaises(ra.ReadAloudError) as error:
                    ra.check_key(key)
                self.assertEqual((error.exception.code, error.exception.status), (400, "schema"))

    def test_item_id_is_a_stable_32_hex_digest_of_the_four_fields(self):
        ident = ra.item_id(dict(KEY))
        self.assertRegex(ident, r"^[0-9a-f]{32}$")
        self.assertEqual(ident, ra.item_id(dict(KEY)))
        self.assertNotEqual(ident, ra.item_id({**KEY, "part": "thinking:0"}))
        self.assertNotEqual(ident, ra.item_id({**KEY, "message": "msg-2"}))
        self.assertNotEqual(ident, ra.item_id({**KEY, "backend": "opencode"}))
        self.assertEqual(len({ra.item_id({**KEY, "part": part}) for part in ("text", "thinking:0", "thinking:1")}), 3)


class ConfigTests(unittest.TestCase):
    """ReadAloudConfig over a really written elevenlabs.json."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="ra-config-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.path = self.root / "elevenlabs.json"

    def write(self, **values):
        raw = {"api_key": "offline-fixture-not-a-key", **values}
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        return raw

    def test_a_missing_file_is_a_plain_words_not_configured(self):
        config = ra.ReadAloudConfig(self.path)
        with self.assertRaises(ra.ReadAloudError) as error:
            config.load()
        self.assertEqual((error.exception.code, error.exception.status), (503, "not-configured"))
        self.assertIn(str(self.path), str(error.exception))
        public = config.public()
        self.assertIs(public["configured"], False)
        self.assertIn("not set up", public["error"])

    def test_public_never_carries_the_key(self):
        raw = self.write(voice_id="voice-a", voice_name="A Voice")
        public = ra.ReadAloudConfig(self.path).public()
        self.assertEqual(set(public), {"configured", "voiceId", "voiceName", "modelId", "playbackRate", "chunkChars"})
        self.assertEqual((public["configured"], public["voiceId"], public["voiceName"]), (True, "voice-a", "A Voice"))
        serialized = json.dumps(public)
        self.assertNotIn(raw["api_key"], serialized)
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("apiKey", serialized)

    def test_a_changed_file_is_read_again(self):
        self.write(voice_id="voice-a", chunk_chars=300)
        config = ra.ReadAloudConfig(self.path)
        self.assertEqual(config.load()["voice_id"], "voice-a")
        self.assertEqual(config.load()["voice_id"], "voice-a")  # same stamp: served from memory
        self.write(voice_id="voice-b-is-longer", chunk_chars=400)
        self.assertEqual(config.load()["voice_id"], "voice-b-is-longer")
        self.assertEqual(config.public()["chunkChars"], 400)

    def test_defaults_fill_in_and_the_numbers_are_clamped(self):
        self.write(chunk_chars=5, continuity_chars=99_999)
        data = ra.ReadAloudConfig(self.path).load()
        self.assertEqual((data["chunk_chars"], data["continuity_chars"]), (300, 2000))
        self.write(chunk_chars=10**6, continuity_chars=-5)
        data = ra.ReadAloudConfig(self.path).load()
        self.assertEqual((data["chunk_chars"], data["continuity_chars"]), (9500, 0))
        self.assertEqual(data["model_id"], ra.DEFAULTS["model_id"])
        self.assertEqual(data["voice_settings"], ra.DEFAULTS["voice_settings"])
        self.assertEqual(data["output_format"], ra.DEFAULTS["output_format"])
        self.assertEqual(data["first_chunk_chars"], ra.DEFAULTS["first_chunk_chars"])

    def test_an_unusable_file_is_not_configured_and_says_why(self):
        cases = {"no key": ({"voice_id": "v"}, "api_key"),
                 "blank key": ({"api_key": "   "}, "api_key"),
                 "bad format": ({"api_key": "k", "output_format": "wav_44100"}, "output_format")}
        for name, (values, needle) in cases.items():
            with self.subTest(name=name):
                self.path.write_text(json.dumps(values), encoding="utf-8")
                with self.assertRaises(ra.ReadAloudError) as error:
                    ra.ReadAloudConfig(self.path).load()
                self.assertEqual((error.exception.code, error.exception.status), (503, "not-configured"))
                self.assertIn(needle, str(error.exception))
        for text in ("{not json", "[1, 2]"):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ra.ReadAloudError) as error:
                    ra.ReadAloudConfig(self.path).load()
                self.assertIn("unreadable or incomplete", str(error.exception))

    def test_the_first_part_is_capped_by_the_chunk_size_and_never_negative(self):
        self.write(chunk_chars=1000)
        self.assertEqual(ra.ReadAloudConfig(self.path).load()["first_chunk_chars"], 350)  # the default
        self.write(chunk_chars=300, first_chunk_chars=5000)
        self.assertEqual(ra.ReadAloudConfig(self.path).load()["first_chunk_chars"], 300)
        self.write(chunk_chars=1000, first_chunk_chars=-40)
        self.assertEqual(ra.ReadAloudConfig(self.path).load()["first_chunk_chars"], 0)
        self.write(chunk_chars=1000, first_chunk_chars="abc")
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ReadAloudConfig(self.path).load()
        self.assertEqual((error.exception.code, error.exception.status), (503, "not-configured"))

    def test_a_voice_settings_override_is_merged_over_the_defaults(self):
        self.write(voice_settings={"stability": 0.9})
        data = ra.ReadAloudConfig(self.path).load()
        self.assertEqual(data["voice_settings"], {**ra.DEFAULTS["voice_settings"], "stability": 0.9})


class Reply:
    """The opener's response: a context manager with headers, like urlopen's HTTPResponse."""

    def __init__(self, data, headers=None):
        self.data, self.headers = data, dict(headers or {})

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, detail=None):
    payload = {"detail": detail} if detail is not None else {"detail": {"message": "fixture detail"}}
    return urllib.error.HTTPError("https://api.elevenlabs.io/v1/text-to-speech/x", code, "fixture", {},
                                  io.BytesIO(json.dumps(payload).encode()))


class VoiceTests(unittest.TestCase):
    """ElevenLabsVoice.synthesize against a fake opener: request shape, retries, plain-words failures."""

    def setUp(self):
        self.config = {"api_key": "offline-fixture-not-a-key"}
        self.voice = ra.voice_of({**ra.DEFAULTS, "voice_id": "voice-fixture"})

    def opener(self, *replies):
        calls, queue = [], list(replies)

        def open_(request, timeout=None):
            calls.append(request)
            reply = queue.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return reply
        return open_, calls

    def test_a_success_answers_the_audio_and_the_character_cost(self):
        opener, calls = self.opener(Reply(b"mp3-bytes", {"character-cost": "37"}))
        voice = ra.ElevenLabsVoice(opener, lambda delay: self.fail("a success never sleeps"))
        audio, cost = voice.synthesize(self.config, self.voice, "hello there", "before", "after")
        self.assertEqual((audio, cost), (b"mp3-bytes", 37))
        self.assertEqual(len(calls), 1)
        request = calls[0]
        self.assertEqual(request.full_url, ra.ENDPOINT + "voice-fixture?output_format=mp3_44100_128")
        self.assertEqual(request.get_method(), "POST")
        headers = {name.lower(): value for name, value in request.headers.items()}
        self.assertEqual(headers["xi-api-key"], "offline-fixture-not-a-key")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "audio/mpeg")
        body = json.loads(request.data)
        self.assertEqual(body["text"], "hello there")
        self.assertEqual(body["model_id"], ra.DEFAULTS["model_id"])
        self.assertEqual(body["voice_settings"], ra.DEFAULTS["voice_settings"])
        self.assertEqual((body["previous_text"], body["next_text"]), ("before", "after"))

    def test_empty_neighbours_are_not_sent_and_a_missing_cost_stays_none(self):
        opener, calls = self.opener(Reply(b"mp3-bytes"))
        self.assertEqual(ra.ElevenLabsVoice(opener, lambda _: None).synthesize(self.config, self.voice, "solo"),
                         (b"mp3-bytes", None))
        body = json.loads(calls[0].data)
        self.assertNotIn("previous_text", body)
        self.assertNotIn("next_text", body)

    def test_a_401_fails_fast_with_tts_key_and_no_retry(self):
        opener, calls = self.opener(http_error(401))
        sleeps = []
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ElevenLabsVoice(opener, sleeps.append).synthesize(self.config, self.voice, "x")
        self.assertEqual((error.exception.code, error.exception.status), (502, "tts-key"))
        self.assertIn("api_key", str(error.exception))
        self.assertEqual((len(calls), sleeps), (1, []))

    def test_a_404_and_a_422_are_tts_voice(self):
        for code in (404, 422):
            with self.subTest(code=code):
                opener, calls = self.opener(http_error(code, {"message": "voice not found"}))
                sleeps = []
                with self.assertRaises(ra.ReadAloudError) as error:
                    ra.ElevenLabsVoice(opener, sleeps.append).synthesize(self.config, self.voice, "x")
                self.assertEqual((error.exception.code, error.exception.status), (502, "tts-voice"))
                self.assertIn("voice not found", str(error.exception))
                self.assertEqual((len(calls), sleeps), (1, []))

    def test_another_4xx_is_tts_refused_with_its_detail(self):
        opener, calls = self.opener(http_error(400, "bad payload"))
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ElevenLabsVoice(opener, lambda _: self.fail("a 4xx never retries")).synthesize(
                self.config, self.voice, "x")
        self.assertEqual((error.exception.code, error.exception.status), (502, "tts-refused"))
        self.assertIn("400", str(error.exception))
        self.assertIn("bad payload", str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_a_quota_429_is_not_retried(self):
        opener, calls = self.opener(http_error(429, {"message": "Your quota is exhausted"}))
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ElevenLabsVoice(opener, lambda _: self.fail("a quota 429 never retries")).synthesize(
                self.config, self.voice, "x")
        self.assertEqual((error.exception.code, error.exception.status), (502, "tts-quota"))
        self.assertIn("exceeded", str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_a_429_then_a_200_is_retried_after_the_widgets_first_delay(self):
        opener, calls = self.opener(http_error(429, {"message": "slow down"}), Reply(b"ok", {"character-cost": "3"}))
        sleeps = []
        self.assertEqual(ra.ElevenLabsVoice(opener, sleeps.append).synthesize(self.config, self.voice, "x"),
                         (b"ok", 3))
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(len(calls), 2)

    def test_a_network_failure_is_retried(self):
        opener, calls = self.opener(urllib.error.URLError("connection reset"), Reply(b"ok"))
        sleeps = []
        self.assertEqual(ra.ElevenLabsVoice(opener, sleeps.append).synthesize(self.config, self.voice, "x"),
                         (b"ok", None))
        self.assertEqual((sleeps, len(calls)), ([0.5], 2))

    def test_six_server_errors_end_in_tts_unavailable_after_the_full_backoff(self):
        opener, calls = self.opener(*[http_error(500) for _ in range(6)])
        sleeps = []
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ElevenLabsVoice(opener, sleeps.append).synthesize(self.config, self.voice, "x")
        self.assertEqual((error.exception.code, error.exception.status), (502, "tts-unavailable"))
        self.assertIn("HTTP 500", str(error.exception))
        self.assertEqual(sleeps, list(ra.RETRY_DELAYS))
        self.assertEqual(len(calls), 6)

    def test_empty_audio_is_tts_empty(self):
        opener, calls = self.opener(Reply(b""))
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ElevenLabsVoice(opener, lambda _: self.fail("empty audio never retries")).synthesize(
                self.config, self.voice, "x")
        self.assertEqual((error.exception.code, error.exception.status), (502, "tts-empty"))
        self.assertEqual(len(calls), 1)


class StoreTests(unittest.TestCase):
    """ReadAloudStore over a temporary state root with a fake config and a fake voice."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="ra-store-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.root = pathlib.Path(self.temp) / "state"
        self.voice = FakeVoice()
        self.clock = Clock()

    def store(self, config=None, voice=None):
        return ra.ReadAloudStore(self.root, config=config or fixed_config(), voice=voice or self.voice,
                                 clock=self.clock)

    def test_save_then_get_answers_the_same_item(self):
        store = self.store()
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT, {"provider": "anthropic"})
        self.assertIs(saved["found"], True)
        self.assertRegex(saved["id"], r"^[0-9a-f]{32}$")
        self.assertEqual([chunk["text"] for chunk in saved["item"]["chunks"]], PARAGRAPHS)
        self.assertEqual([chunk["chars"] for chunk in saved["item"]["chunks"]], [150, 150, 150])
        self.assertEqual([chunk["cached"] for chunk in saved["item"]["chunks"]], [False, False, False])
        self.assertEqual(saved["item"]["text"], TEXT)
        self.assertEqual(saved["item"]["meta"], {"provider": "anthropic"})
        self.assertEqual(saved["item"]["voice"]["voiceId"], "voice-fixture")
        self.assertIsNone(saved["item"]["characterCost"])
        self.assertIs(saved["config"]["configured"], True)
        self.assertTrue((self.root / "items" / (saved["id"] + ".json")).is_file())
        self.assertNotIn("offline-fixture-not-a-key", json.dumps(saved))  # the key never reaches the page
        again = store.get(dict(KEY))
        self.assertIs(again["found"], True)
        self.assertEqual((again["id"], again["item"]["chunks"]), (saved["id"], saved["item"]["chunks"]))

    def test_save_cuts_a_long_opening_into_a_short_first_part(self):
        store = self.store(config=fixed_config(chunk_chars=1000, first_chunk_chars=350))
        head = sentences(9)
        saved = store.save(dict(KEY), SOURCE_HASH, head)
        chunks = saved["item"]["chunks"]
        self.assertGreaterEqual(len(chunks), 2)
        self.assertLessEqual(chunks[0]["chars"], 350)
        self.assertEqual(" ".join(c["text"] for c in chunks), head)
        self.assertEqual(chunks[0]["index"], 0)
        self.assertEqual(chunks[1]["index"], 1)

    def test_an_unknown_key_is_an_honest_miss_with_the_public_config(self):
        reply = self.store().get(dict(KEY))
        self.assertIs(reply["found"], False)
        self.assertRegex(reply["id"], r"^[0-9a-f]{32}$")
        self.assertIs(reply["config"]["configured"], True)
        self.assertNotIn("item", reply)

    def test_audio_is_synthesized_once_then_served_from_the_cache(self):
        store = self.store()
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        data, cached, seconds = store.audio(saved["id"], 0)
        self.assertEqual((data, cached), (PAYLOAD, False))
        self.assertEqual(seconds, ra.mp3_seconds(len(PAYLOAD), "mp3_44100_128"))
        call = self.voice.calls[0]
        self.assertEqual(call["text"], PARAGRAPHS[0])
        # The neighbours are all the text before/after, bounded by the continuity ceiling.
        self.assertEqual((call["previous"], call["next"]), ("", PARAGRAPHS[1] + "\n\n" + PARAGRAPHS[2]))
        self.assertEqual(call["voice"]["voice_id"], "voice-fixture")
        self.assertEqual(call["config"]["api_key"], "offline-fixture-not-a-key")
        again = store.audio(saved["id"], 0)
        self.assertEqual(again, (PAYLOAD, True, seconds))
        self.assertEqual(len(self.voice.calls), 1)  # the cache, not a second synthesis
        chunk = store.get(dict(KEY))["item"]["chunks"][0]
        self.assertEqual((chunk["cached"], chunk["bytes"], chunk["seconds"]), (True, len(PAYLOAD), seconds))
        self.assertEqual(store.get(dict(KEY))["item"]["characterCost"], len(PARAGRAPHS[0]))

    def test_the_neighbouring_text_is_passed_across_the_seams(self):
        store = self.store()
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        store.audio(saved["id"], 2)
        self.assertEqual(self.voice.calls[0]["previous"], PARAGRAPHS[0] + "\n\n" + PARAGRAPHS[1])
        self.assertEqual(self.voice.calls[0]["next"], "")  # the last chunk has nothing after it
        store.audio(saved["id"], 1)
        self.assertEqual(self.voice.calls[1]["previous"], PARAGRAPHS[0])
        self.assertEqual(self.voice.calls[1]["next"], PARAGRAPHS[2])

    def test_a_long_rewrite_is_saved_pass_by_pass_and_its_parts_never_move(self):
        store = self.store()
        first = store.save(dict(KEY), SOURCE_HASH, PARAGRAPHS[0], {"passes": 3}, run="run-0001", segment=0, done=False)
        self.assertIs(first["item"]["complete"], False)
        self.assertEqual([chunk["text"] for chunk in first["item"]["chunks"]], [PARAGRAPHS[0]])
        store.audio(first["id"], 0)  # the voice starts on the first pass
        second = store.save(dict(KEY), SOURCE_HASH, PARAGRAPHS[1] + "\n\n" + PARAGRAPHS[2], {"passes": 3},
                            run="run-0001", segment=1, done=False)
        self.assertEqual([chunk["text"] for chunk in second["item"]["chunks"]], PARAGRAPHS)
        self.assertIs(second["item"]["chunks"][0]["cached"], True)  # the first pass's audio stays
        self.assertEqual(second["item"]["createdAt"], first["item"]["createdAt"])
        self.assertIs(store.get(dict(KEY))["item"]["complete"], False)
        last = store.save(dict(KEY), SOURCE_HASH, "D" * 150, {"passes": 3, "cost": 0.1}, run="run-0001", segment=2, done=True)
        self.assertIs(last["item"]["complete"], True)
        self.assertEqual(last["item"]["text"], TEXT + "\n\n" + "D" * 150)
        self.assertEqual(last["item"]["meta"], {"passes": 3, "cost": 0.1})
        self.assertEqual([chunk["text"] for chunk in last["item"]["chunks"]], PARAGRAPHS + ["D" * 150])
        store.audio(last["id"], 3)  # a later pass's part hears the earlier passes as its neighbours
        self.assertTrue(self.voice.calls[-1]["previous"].endswith("C" * 150))
        self.assertEqual(len(self.voice.calls), 2)  # the first part was synthesized once, at the start
        self.assertIs(store.save(dict(KEY), SOURCE_HASH, TEXT)["item"]["complete"], True)  # one whole save: complete

    def test_each_pass_opens_with_a_short_part_of_its_own(self):
        store = self.store(config=fixed_config(chunk_chars=1000, first_chunk_chars=350))
        store.save(dict(KEY), SOURCE_HASH, "Short opening pass.", run="run-0002", segment=0, done=False)
        later = store.save(dict(KEY), SOURCE_HASH, sentences(9), run="run-0002", segment=1, done=True)
        chunks = later["item"]["chunks"]
        self.assertEqual(chunks[0]["text"], "Short opening pass.")
        self.assertLessEqual(chunks[1]["chars"], 350)
        self.assertEqual(" ".join(c["text"] for c in chunks[1:]), sentences(9))

    def test_new_display_headings_and_speech_are_separate_in_every_synthesis_context(self):
        store = self.store(config=fixed_config(chunk_chars=1000))
        saved = store.save(dict(KEY), SOURCE_HASH, "## The plan\n\nFirst point.\n\n### Testing\n\nSecond point.")
        self.assertEqual([c["text"] for c in saved["item"]["chunks"]],
                         ["## The plan\n\nFirst point.", "### Testing\n\nSecond point."])
        store.audio(saved["id"], 1)
        call = self.voice.calls[0]
        self.assertEqual(call["text"], "Testing.\n\nSecond point.")
        self.assertEqual(call["previous"], "The plan.\n\nFirst point.")
        self.assertEqual(call["next"], "")
        self.assertEqual(saved["item"]["chunks"][1]["chars"], len(call["text"]))
        self.assertNotIn("#", call["text"] + call["previous"])

    def test_legacy_items_keep_their_exact_parts_and_cached_mp3s_without_migration(self):
        store = self.store()
        saved = store.save(dict(KEY), SOURCE_HASH, "First.\n\nSecond.")
        legacy = store._read(saved["id"])
        legacy.pop("speech")
        legacy["chunks"] = ["First.\n\nSecond."]  # the previous server merged these paragraphs
        store._write(legacy)
        directory = store._audio_dir(saved["id"])
        directory.mkdir(parents=True)
        (directory / "0.mp3").write_bytes(PAYLOAD)
        before = store.get(dict(KEY))["item"]
        self.assertEqual([c["text"] for c in before["chunks"]], legacy["chunks"])
        self.assertEqual(store.audio(saved["id"], 0)[0:2], (PAYLOAD, True))
        self.assertEqual(self.voice.calls, [])
        self.assertNotIn("speech", store._read(saved["id"]))
        (directory / "0.mp3").unlink()
        store.audio(saved["id"], 0)
        self.assertEqual(self.voice.calls[0]["text"], legacy["chunks"][0])

    def test_a_requested_far_block_synthesizes_while_an_earlier_prefetch_is_still_in_flight(self):
        gate, started = threading.Event(), threading.Event()
        parent = self

        class IndependentVoice(FakeVoice):
            def synthesize(self, config, voice, text, previous_text="", next_text=""):
                if text == "One.":
                    started.set()
                    parent.assertTrue(gate.wait(5))
                return super().synthesize(config, voice, text, previous_text, next_text)

        voice = IndependentVoice()
        store = self.store(voice=voice)
        saved = store.save(dict(KEY), SOURCE_HASH, "One.\n\nTwo.\n\nThree.\n\nFour.")
        earlier = threading.Thread(target=store.audio, args=(saved["id"], 0))
        earlier.start()
        try:
            self.assertTrue(started.wait(2))
            store.audio(saved["id"], 3)
            self.assertEqual([c["text"] for c in voice.calls], ["Four."])
            self.assertTrue(earlier.is_alive())
            self.assertFalse((store._audio_dir(saved["id"]) / "1.mp3").exists())
            self.assertFalse((store._audio_dir(saved["id"]) / "2.mp3").exists())
        finally:
            gate.set()
            earlier.join(5)

    def test_a_pass_of_another_run_out_of_order_or_after_the_end_is_refused_as_replaced(self):
        store = self.store()
        store.save(dict(KEY), SOURCE_HASH, PARAGRAPHS[0], run="run-aaaa1", segment=0, done=False)
        for run, segment, source in (("run-bbbb2", 1, SOURCE_HASH), ("run-aaaa1", 2, SOURCE_HASH), ("run-aaaa1", 1, "0" * 64)):
            with self.subTest(run=run, segment=segment, source=source[:4]):
                with self.assertRaises(ra.ReadAloudError) as caught:
                    store.save(dict(KEY), source, PARAGRAPHS[1], run=run, segment=segment, done=False)
                self.assertEqual((caught.exception.code, caught.exception.status), (409, "replaced"))
        # Another tab's (or Regenerate's) new run replaces the reading; the old run's next pass is refused.
        store.save(dict(KEY), SOURCE_HASH, PARAGRAPHS[2], run="run-cccc3", segment=0, done=True)
        for run in ("run-aaaa1", "run-cccc3"):  # and a complete reading takes no more passes
            with self.subTest(run=run):
                with self.assertRaises(ra.ReadAloudError) as caught:
                    store.save(dict(KEY), SOURCE_HASH, PARAGRAPHS[1], run=run, segment=1, done=True)
                self.assertEqual(caught.exception.status, "replaced")
        self.assertEqual(store.get(dict(KEY))["item"]["text"], PARAGRAPHS[2])

    def test_the_pass_fields_are_checked(self):
        store = self.store()
        for fields in ({"run": "run-aaaa1"}, {"done": False}, {"segment": 0}, {"segment": 0, "run": "short", "done": False},
                       {"segment": -1, "run": "run-aaaa1", "done": False}, {"segment": "0", "run": "run-aaaa1", "done": False},
                       {"segment": 0, "run": "run-aaaa1", "done": "yes"}, {"segment": 1000, "run": "run-aaaa1", "done": True}):
            with self.subTest(fields=fields):
                with self.assertRaises(ra.ReadAloudError) as caught:
                    store.save(dict(KEY), SOURCE_HASH, TEXT, **fields)
                self.assertEqual((caught.exception.code, caught.exception.status), (400, "schema"))
        self.assertFalse((self.root / "items").exists())

    def test_the_continuity_ceiling_bounds_the_neighbours(self):
        store = self.store(config=fixed_config(continuity_chars=100))
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        store.audio(saved["id"], 1)
        self.assertEqual(self.voice.calls[0]["previous"], PARAGRAPHS[0][-100:])
        self.assertEqual(self.voice.calls[0]["next"], PARAGRAPHS[2][:100])

    def test_zero_continuity_sends_no_neighbours(self):
        store = self.store(config=fixed_config(continuity_chars=0))
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        store.audio(saved["id"], 1)
        self.assertEqual((self.voice.calls[0]["previous"], self.voice.calls[0]["next"]), ("", ""))

    def test_two_threads_asking_for_the_same_chunk_pay_for_one_synthesis(self):
        gate, started = threading.Event(), threading.Event()
        voice = FakeVoice(gate=gate, started=started)
        store = self.store(voice=voice)
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        results, failures = {}, []

        def fetch():
            try:
                results[threading.current_thread().name] = store.audio(saved["id"], 1)
            except BaseException as exc:  # noqa: BLE001 - recorded for the assertion below
                failures.append(exc)

        player = threading.Thread(target=fetch, name="player")
        prefetch = threading.Thread(target=fetch, name="prefetch")
        player.start()
        self.assertTrue(started.wait(5), "the first synthesis never started")
        prefetch.start()
        time.sleep(0.2)  # let the prefetch reach the same chunk's flight lock
        gate.set()
        player.join(10)
        prefetch.join(10)
        self.assertEqual(failures, [])
        self.assertEqual(len(voice.calls), 1)
        self.assertEqual(set(results), {"player", "prefetch"})
        self.assertEqual([entry[0] for entry in results.values()], [PAYLOAD, PAYLOAD])
        self.assertEqual(sorted(entry[1] for entry in results.values()), [False, True])

    def test_resaving_replaces_the_old_audio(self):
        store = self.store()
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        store.audio(saved["id"], 0)
        audio_dir = self.root / "audio" / saved["id"]
        self.assertTrue((audio_dir / "0.mp3").is_file())
        replaced = store.save(dict(KEY), SOURCE_HASH, "Only one new paragraph.")
        self.assertEqual(replaced["id"], saved["id"])
        self.assertFalse((audio_dir / "0.mp3").exists())
        self.assertEqual([chunk["cached"] for chunk in replaced["item"]["chunks"]], [False])
        self.assertEqual(store.audio(saved["id"], 0)[1], False)
        self.assertEqual(len(self.voice.calls), 2)

    def test_a_result_saves_before_elevenlabs_is_set_up_and_audio_then_explains_it(self):
        config = ra.ReadAloudConfig(self.root / "absent-elevenlabs.json")
        store = self.store(config=config)
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        self.assertIsNone(saved["item"]["voice"])
        self.assertEqual(len(saved["item"]["chunks"]), 3)  # each paragraph is a tap part, even before setup
        self.assertIs(saved["config"]["configured"], False)
        self.assertIs(store.get(dict(KEY))["found"], True)
        with self.assertRaises(ra.ReadAloudError) as error:
            store.audio(saved["id"], 0)
        self.assertEqual((error.exception.code, error.exception.status), (503, "not-configured"))
        self.assertEqual(self.voice.calls, [])

    def test_the_first_synthesis_fixes_the_voice_of_an_item_saved_before_setup(self):
        config = ra.ReadAloudConfig(self.root / "elevenlabs.json")
        store = self.store(config=config)
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        self.assertIsNone(saved["item"]["voice"])
        (self.root / "elevenlabs.json").write_text(
            json.dumps({"api_key": "offline-fixture-not-a-key", "voice_id": "voice-later"}), encoding="utf-8")
        store.audio(saved["id"], 0)
        self.assertEqual(self.voice.calls[0]["voice"]["voice_id"], "voice-later")
        self.assertEqual(store.get(dict(KEY))["item"]["voice"]["voiceId"], "voice-later")

    def test_a_tiny_audio_ceiling_drops_the_least_recently_used_audio_and_keeps_the_newest(self):
        voice = FakeVoice(payload=b"m" * (700 * 1024))
        store = self.store(config=fixed_config(cache_max_mb=1), voice=voice)
        older = store.save({**KEY, "message": "msg-old"}, SOURCE_HASH, "one short paragraph")
        newer = store.save({**KEY, "message": "msg-new"}, SOURCE_HASH, "one short paragraph")
        store.audio(older["id"], 0)
        store.audio(newer["id"], 0)  # 1.4 MiB of audio over a 1 MiB ceiling
        self.assertFalse((self.root / "audio" / older["id"]).exists(), "the least recently used audio stays")
        self.assertTrue((self.root / "audio" / newer["id"] / "0.mp3").is_file())
        self.assertIs(store.get({**KEY, "message": "msg-old"})["found"], True)  # only its audio went

    def test_audio_rejects_a_bad_id_and_a_bad_chunk_number(self):
        store = self.store()
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        for ident, index in (("../../etc/passwd", 0), ("0" * 31, 0), ("0" * 33, 0), (saved["id"], -1),
                             (saved["id"], 1.0), (saved["id"], 3)):
            with self.subTest(ident=ident, index=index):
                with self.assertRaises(ra.ReadAloudError) as error:
                    store.audio(ident, index)
                self.assertEqual((error.exception.code, error.exception.status), (400, "schema"))
        with self.assertRaises(ra.ReadAloudError) as error:
            store.audio("f" * 32, 0)
        self.assertEqual((error.exception.code, error.exception.status), (404, "missing"))
        self.assertEqual(self.voice.calls, [])

    def test_save_rejects_a_bad_key_hash_and_empty_text(self):
        store = self.store()
        cases = [({**KEY, "backend": "chatgpt"}, SOURCE_HASH, TEXT),
                 (dict(KEY), "not-a-hash", TEXT),
                 (dict(KEY), SOURCE_HASH, "  \n\t "),
                 (dict(KEY), SOURCE_HASH, TEXT, {"blob": "q" * (17 * 1024)})]
        for args in cases:
            with self.subTest(args=args[1:]):
                with self.assertRaises(ra.ReadAloudError) as error:
                    store.save(*args)
                self.assertEqual((error.exception.code, error.exception.status), (400, "schema"))
        with self.assertRaises(ra.ReadAloudError) as error:
            store.save(dict(KEY), SOURCE_HASH, "x" * (ra.TEXT_LIMIT + 1))
        self.assertEqual((error.exception.code, error.exception.status), (413, "too-large"))
        self.assertFalse((self.root / "items").exists(), "a refused save writes no item")


class NativeContextCutTests(unittest.TestCase):
    """ClaudeReader.context(..., before=uuid): only the records before the clicked one."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.path = self.root / "project" / (SESSION + ".jsonl")
        self.path.parent.mkdir()
        self.reader = ClaudeReader([{"id": "test", "root": str(self.root), "native": False}])

    def write_rows(self, rows):
        self.path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def write(self, count=6):
        rows = []
        for number in range(1, count + 1):
            kind = "user" if number % 2 else "assistant"
            body = "turn-" + str(number) + " prose"
            rows.append(record(number, number - 1 if number > 1 else None, kind,
                               body if kind == "user" else [{"type": "text", "text": body}]))
        self.write_rows(rows)

    def page(self):
        for _ in range(30):
            sessions = self.reader.sessions()["sessions"]
            if sessions and sessions[0]["status"] in ("complete", "incomplete-tail"):
                main = next(scope for scope in sessions[0]["scopes"] if scope["label"] == "main")
                page = self.reader.events(sessionRef=sessions[0]["sessionRef"], scopeRef=main["scopeRef"])
                self.assertIsNotNone(page["targetRef"])
                return page
        self.fail("native index did not finish")

    def test_before_excludes_the_clicked_record_and_everything_newer(self):
        self.write()
        page = self.page()
        cut = self.reader.context(page["targetRef"], 1, historyMode="whole", before=uid(4))
        self.assertEqual(cut["text"], SEPARATOR.join(["[USER]\nturn-1 prose", "[ASSISTANT]\nturn-2 prose",
                                                      "[USER]\nturn-3 prose"]))
        self.assertEqual([boundary["id"] for boundary in cut["historyBoundaries"]], [uid(1), uid(2), uid(3)])
        self.assertEqual((cut["turns"], cut["users"]), (3, 2))
        for newer in ("turn-4", "turn-5", "turn-6"):
            self.assertNotIn(newer, cut["text"])

    def test_the_oldest_record_before_is_an_empty_history(self):
        self.write()
        page = self.page()
        cut = self.reader.context(page["targetRef"], 1, historyMode="whole", before=uid(1))
        self.assertEqual((cut["text"], cut["turns"], cut["historyBoundaries"]), ("", 0, []))

    def test_a_clicked_record_that_is_not_prose_still_cuts_the_history(self):
        self.write_rows([record(1, content="turn-1 prose"),
                         record(2, 1, kind="assistant", content=[{"type": "text", "text": "turn-2 prose"}]),
                         record(3, 2, content=[{"type": "tool_result", "tool_use_id": "t", "content": "TOOL"}]),
                         record(4, 3, kind="assistant", content=[{"type": "text", "text": "turn-4 prose"}])])
        page = self.page()
        cut = self.reader.context(page["targetRef"], 1, historyMode="whole", before=uid(3))
        self.assertEqual(cut["text"], SEPARATOR.join(["[USER]\nturn-1 prose", "[ASSISTANT]\nturn-2 prose"]))
        self.assertEqual([boundary["id"] for boundary in cut["historyBoundaries"]], [uid(1), uid(2)])
        self.assertNotIn("TOOL", cut["text"])
        self.assertNotIn("turn-4", cut["text"])

    def test_an_unknown_before_is_a_missing_409(self):
        self.write()
        page = self.page()
        for before in (uid(99), "not-a-uuid", ""):
            with self.subTest(before=before):
                with self.assertRaises(ReaderError) as error:
                    self.reader.context(page["targetRef"], 1, historyMode="whole", before=before)
                self.assertEqual((error.exception.code, error.exception.status), (409, "missing"))

    def test_the_cut_composes_with_the_budgeted_mode(self):
        self.write()
        page = self.page()
        cut = self.reader.context(page["targetRef"], 1, 5 * 1024, "budgeted", before=uid(6))
        self.assertEqual(cut["historyMode"], "budgeted")
        self.assertLessEqual(cut["bytes"], 5 * 1024)
        self.assertNotIn("turn-6", cut["text"])
        self.assertEqual(cut["historyBoundaries"][-1]["id"], uid(5))


class HostContextCutTests(unittest.TestCase):
    """ClaudeFacade.context(..., before=uuid): the same cut on a host-run Manager."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.host = InertHost()
        self.reader = ClaudeFacade(ClaudeReader([{"id": "fixture", "root": self.temp.name}]), self.host)
        self.number = 0

    def add_message(self, text):
        self.number += 1
        if self.number % 2:
            self.host.user(text, uuid="u" + str(self.number))
        else:
            self.host.frame("assistant", [{"type": "text", "text": text}], "a" + str(self.number),
                            message_id="p" + str(self.number))

    def page(self):
        row = self.reader.sessions()["sessions"][0]
        main = next(scope for scope in row["scopes"] if scope["label"] == "main")
        return self.reader.events(sessionRef=row["sessionRef"], scopeRef=main["scopeRef"])

    def test_before_excludes_the_clicked_row_and_everything_newer(self):
        for number in range(1, 7):
            self.add_message("host-" + str(number))
        page = self.page()
        cut = self.reader.context(page["targetRef"], 1, historyMode="whole", before="u3")
        self.assertEqual(cut["text"], SEPARATOR.join(["[USER]\nhost-1", "[ASSISTANT]\nhost-2"]))
        self.assertEqual([boundary["id"] for boundary in cut["historyBoundaries"]], ["u1", "a2"])
        for newer in ("host-3", "host-4", "host-5", "host-6"):
            self.assertNotIn(newer, cut["text"])

    def test_an_unknown_before_is_a_missing_409(self):
        self.add_message("host only")
        page = self.page()
        with self.assertRaises(ReaderError) as error:
            self.reader.context(page["targetRef"], 1, historyMode="whole", before="u404")
        self.assertEqual((error.exception.code, error.exception.status), (409, "missing"))


class OpenCodeContextCutTests(unittest.TestCase):
    """OpenCodeManagers.history(..., before=row_id): only the messages before the clicked row."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="ra-om-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.proto = Proto()
        self.proto.sessions[SID] = manager_session()
        self.manager = om.OpenCodeManagers(path=pathlib.Path(self.temp) / "journal.sqlite3", client=self.proto,
                                           clock=Clock(), workspace=om.WORKSPACE)
        self.proto.history[SID] = [user_message("msg_1", "first question"),
                                   assistant_message("msg_2", text_part("first answer")),
                                   user_message("msg_3", "second question"),
                                   assistant_message("msg_4", text_part("second answer"))]

    def test_before_excludes_the_clicked_messages_and_everything_newer(self):
        text, boundaries, users = self.manager.history(SID, "whole", 51200, before=row_ref("msg_3", "m"))
        self.assertEqual(text, SEPARATOR.join(["[USER]\nfirst question", "[ASSISTANT]\nfirst answer"]))
        self.assertEqual([boundary["id"] for boundary in boundaries], ["msg_1", "msg_2"])
        self.assertEqual(users, 1)
        self.assertNotIn("second question", text)
        self.assertNotIn("second answer", text)

    def test_every_row_of_a_message_matches_not_only_its_main_slot(self):
        self.proto.history[SID][1] = assistant_message("msg_2", text_part("first answer"), text_part("more detail"))
        for message, slots, expected in (("msg_3", ("m", "state", "r", "p0"), ["msg_1", "msg_2"]),
                                         ("msg_2", ("m", "state", "r", "p0", "p1"), ["msg_1"])):
            for slot in slots:
                with self.subTest(message=message, slot=slot):
                    text, boundaries, _ = self.manager.history(SID, "whole", 51200,
                                                              before=row_ref(message, slot))
                    self.assertEqual([boundary["id"] for boundary in boundaries], expected)

    def test_the_oldest_message_before_is_an_empty_history(self):
        text, boundaries, users = self.manager.history(SID, "whole", 51200, before=row_ref("msg_1", "m"))
        self.assertEqual((text, boundaries, users), ("", [], 0))

    def test_an_unknown_before_is_a_missing_409(self):
        with self.assertRaises(ReaderError) as error:
            self.manager.history(SID, "whole", 51200, before=row_ref("msg_9", "m"))
        self.assertEqual((error.exception.code, error.exception.status), (409, "missing"))


class FakeReader:
    """The pane's injected Claude context reader: records the call, answers a fixed envelope."""

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def context(self, targetRef, targetRevision, budgetBytes=50 * 1024, historyMode="budgeted", before=None):
        self.calls.append((targetRef, targetRevision, budgetBytes, historyMode, before))
        if self.error is not None:
            raise self.error
        return dict(self.reply)


class FakeManagers:
    """The pane's injected OpenCode manager owner: records `history` and answers its tuple."""

    def __init__(self, reply=("", [], 0), error=None):
        self.reply, self.error, self.calls = reply, error, []

    def history(self, sid, historyMode="whole", budgetBytes=51200, before=None):
        self.calls.append((sid, historyMode, budgetBytes, before))
        if self.error is not None:
            raise self.error
        return self.reply


class FakeAccount:
    """ElevenLabsAccount stand-in: canned voices/models/subscription, every call counted by name."""

    def __init__(self, voices=None, models=None, subscription=None, error=None, models_error=None):
        self.voices_, self.models_, self.sub, self.error, self.models_error = voices, models, subscription, error, models_error
        self.calls = []

    def voices(self, key):
        self.calls.append(("voices", key))
        if self.error:
            raise self.error
        return self.voices_

    def models(self, key):
        self.calls.append(("models", key))
        if self.models_error:
            raise self.models_error
        return self.models_

    def subscription(self, key):
        self.calls.append(("subscription", key))
        if self.error:
            raise self.error
        return self.sub or {}


SECRET = "sk_fixture0123456789abcdefWXYZ"


class SettingsTests(unittest.TestCase):
    """ReadAloudSettings over temp files: the write-only key, voice/model choices, the rewrite override, the catalog."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="ra-settings-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.config = ra.ReadAloudConfig(self.root / "elevenlabs.json")
        self.account = FakeAccount(voices=[{"id": "v-b", "name": "Bella", "category": "premade"},
                                           {"id": "v-a", "name": "Adam", "category": "cloned"}],
                                   models=[{"id": "eleven_v3", "name": "Eleven v3"}],
                                   subscription={"tier": "creator", "character_count": 1200, "character_limit": 100000})
        self.settings = ra.ReadAloudSettings(self.config, self.account, self.root / "read-aloud.json", self.root / "catalog.json")

    def test_defaults_are_todays_choices_and_nothing_calls_elevenlabs(self):
        view = self.settings.view()
        self.assertEqual((view["voiceId"], view["modelId"], view["rewrite"]),
                         (ra.DEFAULTS["voice_id"], "eleven_multilingual_v2", {"provider": None, "model": None, "thinking": None}))
        self.assertEqual(view["key"], {"set": False, "masked": None})
        self.assertIsNone(view["voices"])
        self.assertEqual(view["models"], ra.FALLBACK_TTS_MODELS)
        self.assertEqual(self.account.calls, [])

    def test_the_key_is_saved_owner_only_and_only_its_last_four_characters_come_back(self):
        view = self.settings.update({"apiKey": "  " + SECRET + " "})
        self.assertEqual(view["key"], {"set": True, "masked": "…WXYZ"})
        self.assertNotIn(SECRET, json.dumps(view))
        self.assertEqual(json.loads((self.root / "elevenlabs.json").read_text())["api_key"], SECRET)
        self.assertEqual((self.root / "elevenlabs.json").stat().st_mode & 0o777, 0o600)
        self.assertIs(view["configured"], True)
        self.assertEqual(self.config.load()["api_key"], SECRET)

    def test_saving_keeps_the_hand_written_settings_and_refuses_an_unreadable_file(self):
        (self.root / "elevenlabs.json").write_text(json.dumps({"api_key": SECRET, "chunk_chars": 800, "voice_id": "old"}))
        self.settings.update({"voiceId": "v-a", "voiceName": "Adam", "modelId": "eleven_v3"})
        raw = json.loads((self.root / "elevenlabs.json").read_text())
        self.assertEqual((raw["api_key"], raw["chunk_chars"], raw["voice_id"], raw["voice_name"], raw["model_id"]),
                         (SECRET, 800, "v-a", "Adam", "eleven_v3"))
        (self.root / "elevenlabs.json").write_text("{not json")
        with self.assertRaises(ra.ReadAloudError) as error:
            self.settings.update({"voiceId": "v-b"})
        self.assertEqual(error.exception.status, "unreadable")
        self.assertEqual((self.root / "elevenlabs.json").read_text(), "{not json")

    def test_bad_values_are_schema_errors(self):
        for changes in ({"apiKey": "short"}, {"apiKey": "has a space in it 1234567"}, {"voiceId": "../x"},
                        {"modelId": "Eleven V3!"}, {"rewriteProvider": "openai"}, {"rewriteProvider": "", "rewriteModel": "m"},
                        {"rewriteThinking": "huge"}, {"rewriteThinking": 3}):
            with self.subTest(changes=changes), self.assertRaises(ra.ReadAloudError) as error:
                self.settings.update(changes)
            self.assertEqual(error.exception.status, "schema")

    def test_the_rewrite_override_is_saved_and_cleared(self):
        view = self.settings.update({"rewriteProvider": "openrouter", "rewriteModel": "anthropic/claude-opus-5.5"})
        self.assertEqual(view["rewrite"], {"provider": "openrouter", "model": "anthropic/claude-opus-5.5", "thinking": None})
        view = self.settings.update({"rewriteProvider": ""})
        self.assertEqual(view["rewrite"], {"provider": None, "model": None, "thinking": None})
        self.assertFalse((self.root / "elevenlabs.json").exists())  # the override never touches the ElevenLabs file

    def test_the_rewrite_thinking_level_is_its_own_setting(self):
        # Chosen alone (the model stays Refine's), kept across a model change, cleared back to Refine's.
        view = self.settings.update({"rewriteThinking": "low"})
        self.assertEqual(view["rewrite"], {"provider": None, "model": None, "thinking": "low"})
        view = self.settings.update({"rewriteProvider": "anthropic", "rewriteModel": "claude-sonnet-5-5"})
        self.assertEqual(view["rewrite"], {"provider": "anthropic", "model": "claude-sonnet-5-5", "thinking": "low"})
        view = self.settings.update({"rewriteProvider": ""})
        self.assertEqual(view["rewrite"]["thinking"], "low")
        view = self.settings.update({"rewriteThinking": "max"})
        self.assertEqual(view["rewrite"]["thinking"], "max")
        view = self.settings.update({"rewriteThinking": ""})
        self.assertEqual(view["rewrite"], {"provider": None, "model": None, "thinking": None})
        self.assertIsNone(json.loads(self.settings.path.read_text())["rewrite_thinking"])

    def test_refresh_reads_the_account_once_and_caches_the_lists_for_that_key(self):
        with self.assertRaises(ra.ReadAloudError):
            self.settings.refresh()  # no key yet
        self.settings.update({"apiKey": SECRET})
        view = self.settings.refresh()
        self.assertEqual([v["name"] for v in view["voices"]], ["Bella", "Adam"])  # as the account answered
        self.assertEqual((view["models"], view["modelsFromAccount"]), ([{"id": "eleven_v3", "name": "Eleven v3"}], True))
        self.assertEqual([c[0] for c in self.account.calls], ["voices", "models"])
        self.assertNotIn(SECRET, (self.root / "catalog.json").read_text())
        self.assertEqual(self.settings.view()["voices"][0]["id"], "v-b")  # cached: no new call
        self.assertEqual(len(self.account.calls), 2)
        self.settings.update({"apiKey": SECRET + "2"})
        self.assertIsNone(self.settings.view()["voices"])  # a replaced key drops the old account's lists

    def test_an_unreadable_model_list_falls_back_to_the_fixed_one(self):
        self.account.models_error = ra.ReadAloudError(502, "tts-permission", "no models_read")
        self.settings.update({"apiKey": SECRET})
        view = self.settings.refresh()
        self.assertEqual((view["models"], view["modelsFromAccount"]), (ra.FALLBACK_TTS_MODELS, False))

    def test_the_key_test_reports_ok_with_usage_or_the_error(self):
        self.settings.update({"apiKey": SECRET})
        result = self.settings.test()
        self.assertEqual(result, {"ok": True, "message": "ElevenLabs accepted the key · plan creator · 1,200 of 100,000 characters used"})
        self.account.error = ra.ReadAloudError(502, "tts-key", "ElevenLabs rejected the API key (401)")
        self.assertEqual(self.settings.test(), {"ok": False, "message": "ElevenLabs rejected the API key (401)"})
        self.account.error = ra.ReadAloudError(502, "tts-permission", "the key works but lacks user_read")
        self.assertIs(self.settings.test()["ok"], True)


class AccountTests(unittest.TestCase):
    """ElevenLabsAccount's request shape and answers against a fake opener."""

    def test_voices_are_read_with_the_key_header_and_bad_rows_dropped(self):
        calls = []

        def opener(request, timeout=None):
            calls.append(request)
            return Reply(json.dumps({"voices": [{"voice_id": "v1", "name": "Rachel", "category": "premade"},
                                                {"voice_id": "../bad", "name": "x"}, {"name": "no id"}]}).encode())
        voices = ra.ElevenLabsAccount(opener).voices(SECRET)
        self.assertEqual(voices, [{"id": "v1", "name": "Rachel", "category": "premade"}])
        self.assertEqual((calls[0].full_url, calls[0].get_method(), calls[0].get_header("Xi-api-key")),
                         ("https://api.elevenlabs.io/v1/voices", "GET", SECRET))

    def test_models_keep_text_to_speech_only(self):
        reply = Reply(json.dumps([{"model_id": "eleven_v3", "name": "v3", "can_do_text_to_speech": True},
                                  {"model_id": "eleven_english_sts_v2", "can_do_text_to_speech": False}]).encode())
        self.assertEqual(ra.ElevenLabsAccount(lambda r, timeout=None: reply).models(SECRET), [{"id": "eleven_v3", "name": "v3"}])

    def test_a_401_is_a_rejected_key_unless_it_is_a_missing_permission(self):
        def opener_for(detail):
            def opener(request, timeout=None):
                raise http_error(401, detail)
            return opener
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ElevenLabsAccount(opener_for({"status": "invalid_api_key", "message": "bad"})).subscription(SECRET)
        self.assertEqual(error.exception.status, "tts-key")
        with self.assertRaises(ra.ReadAloudError) as error:
            ra.ElevenLabsAccount(opener_for({"status": "missing_permissions", "message": "user_read"})).subscription(SECRET)
        self.assertEqual(error.exception.status, "tts-permission")


class RouteTests(unittest.TestCase):
    """`/api/read-aloud/*` over a real ephemeral loopback server with injected readers and a temp store."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="ra-route-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.root = pathlib.Path(self.temp) / "state"
        self.voice = FakeVoice()
        self.store = ra.ReadAloudStore(self.root, config=fixed_config(), voice=self.voice, clock=Clock())
        self.claude = FakeReader({"text": "[USER]\nold", "historyBoundaries": [{"id": "u1", "end": 11}],
                                  "users": 1, "exclusions": [{"uuid": "a2", "reason": "control-or-metadata"}]})
        self.managers = FakeManagers(("opencode prose", [{"id": "msg_1", "end": 14}], 1))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.read_aloud = self.store
        self.httpd.claude_reader = self.claude
        self.httpd.opencode_managers = self.managers
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.httpd.server_address[1]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()

    def request(self, method, path, body=None, headers=None):
        raw = body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        conn.request(method, path, raw, {"Content-Type": "application/json", **(headers or {})})
        response = conn.getresponse()
        data, status, reply = response.read(), response.status, dict(response.getheaders())
        conn.close()
        if "application/json" in reply.get("Content-Type", ""):
            return status, reply, json.loads(data)
        return status, reply, data

    def url(self, path, query=None):
        return path + ("?" + urllib.parse.urlencode(query) if query else "")

    def test_the_claude_context_route_answers_the_cut_with_the_default_budget(self):
        code, headers, data = self.request("GET", self.url("/api/read-aloud/context", {
            "backend": "claude", "targetRef": "target-1", "before": "u9"}))
        self.assertEqual(code, 200, data)
        self.assertEqual(self.claude.calls, [("target-1", 0, 51200, "whole", "u9")])
        self.assertEqual((data["backend"], data["source"], data["status"]), ("read-aloud", "claude", "stored"))
        self.assertEqual(data["text"], "[USER]\nold")
        self.assertEqual(data["historyBoundaries"], [{"id": "u1", "end": 11}])
        self.assertEqual((data["turns"], data["users"], data["excluded"]), (1, 1, 1))
        self.assertEqual(data["bytes"], len("[USER]\nold".encode()))
        self.assertEqual(data["historyMode"], "whole")
        self.assertIsInstance(data["readAt"], int)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_the_opencode_context_route_answers_the_cut_and_its_budget(self):
        code, _, data = self.request("GET", self.url("/api/read-aloud/context", {
            "backend": "opencode", "id": "ses_manager0001", "before": "msg_2_m",
            "historyMode": "budgeted", "budgetBytes": "8192"}))
        self.assertEqual(code, 200, data)
        self.assertEqual(self.managers.calls, [("ses_manager0001", "budgeted", 8192, "msg_2_m")])
        self.assertEqual((data["source"], data["historyMode"], data["excluded"]), ("opencode", "budgeted", 0))
        self.assertEqual(data["text"], "opencode prose")

    def test_the_context_route_refuses_an_incomplete_or_unknown_cut(self):
        cases = [{"backend": "claude", "targetRef": "t"},
                 {"backend": "opencode", "id": "ses_1"},
                 {"backend": "claude", "targetRef": "t", "before": "u1", "historyMode": "recent"},
                 {"backend": "claude", "targetRef": "t", "before": "u1", "budgetBytes": "lots"},
                 {"backend": "claude", "targetRef": "t", "before": "u1", "extra": "x"},
                 {"targetRef": "t", "before": "u1"},
                 {"backend": "chatgpt", "targetRef": "t", "before": "u1"},
                 {"backend": "claude", "before": "u1"}]
        for query in cases:
            with self.subTest(query=query):
                code, _, data = self.request("GET", self.url("/api/read-aloud/context", query))
                self.assertEqual((code, data["status"]), (400, "schema"), data)
        code, _, data = self.request("GET", "/api/read-aloud/context?backend=claude&backend=opencode&before=u1")
        self.assertEqual((code, data["status"]), (400, "schema"), data)
        self.assertEqual((self.claude.calls, self.managers.calls), ([], []))

    def test_the_context_route_reports_the_readers_missing_409(self):
        self.claude.error = ReaderError(409, "missing", "that message is not on this conversation's main path")
        code, _, data = self.request("GET", self.url("/api/read-aloud/context", {
            "backend": "claude", "targetRef": "t", "before": "u9"}))
        self.assertEqual((code, data["status"]), (409, "missing"))
        self.assertIn("main path", data["error"])
        self.managers.error = ReaderError(409, "missing", "that message is not in this OpenCode conversation")
        code, _, data = self.request("GET", self.url("/api/read-aloud/context", {
            "backend": "opencode", "id": "ses_1", "before": "msg_9_m"}))
        self.assertEqual((code, data["status"]), (409, "missing"))

    def test_the_item_route_saves_then_finds_the_same_result(self):
        payload = {"schemaVersion": 1, "key": dict(KEY), "sourceHash": SOURCE_HASH, "text": TEXT,
                   "meta": {"provider": "anthropic"}}
        code, _, data = self.request("POST", "/api/read-aloud/item", payload)
        self.assertEqual(code, 200, data)
        self.assertIs(data["found"], True)
        ident = data["id"]
        code, _, found = self.request("GET", self.url("/api/read-aloud/item", dict(KEY)))
        self.assertEqual(code, 200, found)
        self.assertEqual((found["found"], found["id"]), (True, ident))
        self.assertEqual(found["item"]["text"], TEXT)
        self.assertEqual(found["item"]["meta"], {"provider": "anthropic"})
        missing = self.request("GET", self.url("/api/read-aloud/item", {**KEY, "message": "msg-other"}))[2]
        self.assertIs(missing["found"], False)
        self.assertIs(missing["config"]["configured"], True)
        self.assertNotIn("offline-fixture-not-a-key", json.dumps(missing))

    def test_the_item_route_refuses_bad_saved_payloads(self):
        cases = [(b"{not json", "bad JSON"),
                 ({"schemaVersion": 2, "key": dict(KEY), "sourceHash": SOURCE_HASH, "text": TEXT}, "version"),
                 ({"schemaVersion": 1, "key": dict(KEY), "sourceHash": SOURCE_HASH}, "no text"),
                 ({"schemaVersion": 1, "key": dict(KEY), "sourceHash": "nope", "text": TEXT}, "hash"),
                 ({"schemaVersion": 1, "key": {**KEY, "backend": "chatgpt"}, "sourceHash": SOURCE_HASH,
                   "text": TEXT}, "backend"),
                 ({"schemaVersion": 1, "key": dict(KEY), "sourceHash": SOURCE_HASH, "text": "  "}, "empty"),
                 ({"schemaVersion": 1, "key": dict(KEY), "sourceHash": SOURCE_HASH, "text": TEXT,
                   "extra": 1}, "unknown field")]
        for body, label in cases:
            with self.subTest(label=label):
                code, _, data = self.request("POST", "/api/read-aloud/item", body)
                self.assertEqual((code, data["status"]), (400, "schema"), data)
        self.assertFalse((self.root / "items").exists())

    def test_the_item_route_takes_a_long_rewrite_pass_by_pass(self):
        base = {"schemaVersion": 1, "key": dict(KEY), "sourceHash": SOURCE_HASH}
        code, _, data = self.request("POST", "/api/read-aloud/item",
                                     {**base, "text": PARAGRAPHS[0], "run": "run-route1", "segment": 0, "done": False})
        self.assertEqual(code, 200, data)
        self.assertIs(data["item"]["complete"], False)
        self.assertIs(self.request("GET", self.url("/api/read-aloud/item", dict(KEY)))[2]["item"]["complete"], False)
        code, _, data = self.request("POST", "/api/read-aloud/item", {**base, "text": PARAGRAPHS[1], "run": "run-route1",
                                                                      "segment": 1, "done": True, "meta": {"passes": 2}})
        self.assertEqual(code, 200, data)
        self.assertEqual((data["item"]["complete"], len(data["item"]["chunks"]), data["item"]["meta"]), (True, 2, {"passes": 2}))
        code, _, data = self.request("POST", "/api/read-aloud/item",
                                     {**base, "text": PARAGRAPHS[2], "run": "run-other", "segment": 1, "done": True})
        self.assertEqual((code, data["status"]), (409, "replaced"))
        code, _, data = self.request("POST", "/api/read-aloud/item",
                                     {**base, "text": TEXT, "run": "run-route1", "segment": "1", "done": True})
        self.assertEqual((code, data["status"]), (400, "schema"))

    def test_the_item_get_needs_exactly_the_four_key_fields(self):
        for query in ({**KEY, "extra": "x"}, {k: KEY[k] for k in ("backend", "conversation", "message")},
                      {**KEY, "backend": "chatgpt"}, {**KEY, "part": "prose"}):
            with self.subTest(query=query):
                code, _, data = self.request("GET", self.url("/api/read-aloud/item", query))
                self.assertEqual((code, data["status"]), (400, "schema"), data)

    def test_the_audio_route_answers_mp3_bytes_with_the_cache_and_length_headers(self):
        saved = self.store.save(dict(KEY), SOURCE_HASH, TEXT)
        body = {"schemaVersion": 1, "id": saved["id"], "chunk": 0}
        code, headers, data = self.request("POST", "/api/read-aloud/audio", body)
        self.assertEqual(code, 200)
        self.assertEqual(data, PAYLOAD)
        self.assertEqual(headers["Content-Type"], "audio/mpeg")
        self.assertEqual(headers["Content-Length"], str(len(PAYLOAD)))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Read-Aloud-Cached"], "0")
        self.assertEqual(headers["X-Read-Aloud-Seconds"], str(ra.mp3_seconds(len(PAYLOAD), "mp3_44100_128")))
        again = self.request("POST", "/api/read-aloud/audio", body)
        self.assertEqual((again[0], again[1]["X-Read-Aloud-Cached"], again[2]), (200, "1", PAYLOAD))
        self.assertEqual(len(self.voice.calls), 1)

    def test_the_audio_route_reports_a_missing_item_a_bad_chunk_and_bad_fields(self):
        saved = self.store.save(dict(KEY), SOURCE_HASH, TEXT)
        cases = [({"schemaVersion": 1, "id": "f" * 32, "chunk": 0}, 404, "missing"),
                 ({"schemaVersion": 1, "id": saved["id"], "chunk": 3}, 400, "schema"),
                 ({"schemaVersion": 1, "id": saved["id"]}, 400, "schema"),
                 ({"schemaVersion": 1, "id": saved["id"], "chunk": 0, "extra": "x"}, 400, "schema"),
                 ({"schemaVersion": 1, "id": saved["id"], "chunk": "0"}, 400, "schema")]
        for body, expected, status in cases:
            with self.subTest(body=body):
                code, _, data = self.request("POST", "/api/read-aloud/audio", body)
                self.assertEqual((code, data["status"]), (expected, status), data)
        self.assertEqual(self.voice.calls, [])

    def test_the_audio_route_explains_a_missing_elevenlabs_setup(self):
        store = ra.ReadAloudStore(self.root, config=ra.ReadAloudConfig(pathlib.Path(self.temp) / "absent.json"),
                                  voice=self.voice, clock=Clock())
        saved = store.save(dict(KEY), SOURCE_HASH, TEXT)
        self.httpd.read_aloud = store
        code, _, data = self.request("POST", "/api/read-aloud/audio",
                                     {"schemaVersion": 1, "id": saved["id"], "chunk": 0})
        self.assertEqual((code, data["status"]), (503, "not-configured"))
        self.assertIn("ElevenLabs is not set up", data["error"])
        self.assertNotIn("offline-fixture-not-a-key", json.dumps(data))

    def test_the_settings_routes_never_answer_the_key_and_reach_elevenlabs_only_on_request(self):
        account = FakeAccount(voices=[{"id": "v-a", "name": "Adam", "category": ""}], models=None, subscription={})
        root = pathlib.Path(self.temp)
        self.httpd.read_aloud_settings = ra.ReadAloudSettings(ra.ReadAloudConfig(root / "elevenlabs.json"), account,
                                                              root / "read-aloud.json", root / "catalog.json")
        code, _, data = self.request("GET", "/api/read-aloud/settings")
        self.assertEqual((code, data["key"]["set"], data["voiceId"]), (200, False, ra.DEFAULTS["voice_id"]))
        code, _, data = self.request("POST", "/api/read-aloud/settings", {"schemaVersion": 1, "apiKey": SECRET})
        self.assertEqual((code, data["key"]["masked"]), (200, "…WXYZ"))
        self.assertNotIn(SECRET, json.dumps(data))
        code, _, data = self.request("POST", "/api/read-aloud/settings", {"schemaVersion": 1, "rewriteThinking": "xhigh"})
        self.assertEqual((code, data["rewrite"]["thinking"]), (200, "xhigh"))
        self.assertEqual(account.calls, [])
        code, _, data = self.request("POST", "/api/read-aloud/settings/catalog", {"schemaVersion": 1})
        self.assertEqual((code, [v["id"] for v in data["voices"]]), (200, ["v-a"]))
        code, _, data = self.request("POST", "/api/read-aloud/settings/test", {"schemaVersion": 1})
        self.assertEqual((code, data["ok"]), (200, True))
        self.assertEqual(self.request("POST", "/api/read-aloud/settings", {"schemaVersion": 1})[0], 400)
        self.assertEqual(self.request("POST", "/api/read-aloud/settings", {"schemaVersion": 1, "api_key": "x"})[0], 400)
        self.assertEqual(self.request("POST", "/api/read-aloud/settings/test", {"schemaVersion": 1, "x": 1})[0], 400)
        self.assertEqual(self.request("GET", "/api/read-aloud/settings?x=1")[0], 400)
        self.assertEqual(self.request("DELETE", "/api/read-aloud/settings")[0], 405)

    def test_read_aloud_routes_are_same_origin_only(self):
        saved = self.store.save(dict(KEY), SOURCE_HASH, TEXT)
        body = {"schemaVersion": 1, "id": saved["id"], "chunk": 0}
        for path in ("/api/read-aloud/audio", "/api/read-aloud/context", "/api/read-aloud/item"):
            with self.subTest(method="OPTIONS", path=path):
                code, _, data = self.request("OPTIONS", path)
                self.assertEqual((code, data["status"]), (403, "origin"), data)
        for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
                        {"Origin": "https://evil.example"}):
            with self.subTest(headers=headers):
                code, _, data = self.request("GET", self.url("/api/read-aloud/item", dict(KEY)), headers=headers)
                self.assertEqual((code, data["status"]), (403, "origin"), data)
                code, _, data = self.request("POST", "/api/read-aloud/audio", body, headers=headers)
                self.assertEqual((code, data["status"]), (403, "origin"), data)
        self.assertEqual(self.voice.calls, [])
        code = self.request("GET", self.url("/api/read-aloud/item", dict(KEY)),
                            headers={"Sec-Fetch-Site": "same-origin"})[0]
        self.assertEqual(code, 200)

    def test_a_post_carrying_a_query_string_or_a_non_json_type_is_refused(self):
        saved = self.store.save(dict(KEY), SOURCE_HASH, TEXT)
        body = {"schemaVersion": 1, "id": saved["id"], "chunk": 0}
        code, _, data = self.request("POST", "/api/read-aloud/audio?x=1", body)
        self.assertEqual((code, data["status"]), (403, "origin"), data)
        code, _, data = self.request("POST", "/api/read-aloud/audio", body,
                                     headers={"Content-Type": "text/plain"})
        self.assertEqual((code, data["status"]), (403, "origin"), data)
        self.assertEqual(self.voice.calls, [])

    def test_an_unknown_read_aloud_path_is_404_and_a_get_on_a_post_route_is_405(self):
        self.assertEqual(self.request("GET", "/api/read-aloud/other")[0], 404)
        self.assertEqual(self.request("GET", "/api/read-aloud/audio")[0], 405)
        self.assertEqual(self.request("DELETE", "/api/read-aloud/item")[0], 405)


if __name__ == "__main__":
    unittest.main()
