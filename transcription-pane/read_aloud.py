"""Read Aloud's server half: the ElevenLabs settings, the saved results and their audio.

A conversation turn rewritten for listening (the browser runs the rewrite with Dan's own Refine provider, the way
Refine runs) is saved here, keyed to its conversation and message, split into paragraph-sized tap blocks (each also
one synthesis part), and synthesized through ElevenLabs on demand. Each part's MP3 is cached beside its item, so
the next click on the same turn replays at once. A long turn's rewrite arrives in passes and is saved as each lands
(an incomplete item that grows), so its first parts play while the rest is rewritten. The ElevenLabs key lives only in Dan's local config
(~/.config/opencode-companion/elevenlabs.json, mode 600): it never reaches the browser, a response or a log.

Storage: ~/.local/state/opencode-companion/read-aloud/items/<id>.json (the text, its chunks, the voice it was
saved with) and audio/<id>/<n>.mp3. Bounded: least recently used items lose their audio first once the audio
passes the configured ceiling (cache_max_mb, default 512), and the oldest items go once there are MAX_ITEMS.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "opencode-companion" / "elevenlabs.json"
STATE_DIR = Path.home() / ".local" / "state" / "opencode-companion" / "read-aloud"
ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech/"
# The widget's choices (deepgram-typingmind-extension.js CONFIG and elevenFetchChunkOnce): Multilingual v2 for
# quality, the voice's API speed at its 1.2 ceiling (extra speed comes from the player's playbackRate).
DEFAULTS = {
    "voice_id": "21m00Tcm4TlvDq8ikWAM", "voice_name": None, "model_id": "eleven_multilingual_v2",
    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": 1.2},
    "chunk_chars": 1000, "playback_rate": 1.35, "output_format": "mp3_44100_128",
    # Neighbouring text sent as previous_text/next_text for an even voice across chunk seams; ElevenLabs bills
    # only `text` (its character-cost header counts it alone), so this costs nothing.
    "continuity_chars": 500, "cache_max_mb": 512,
    # A short opening part: synthesis time grows with length (about 8.6 s for 786 characters, measured), so the first
    # audio arrives sooner and the rest synthesizes while it plays. 0 keeps the widget's chunks unchanged.
    "first_chunk_chars": 350,
}
MP3_BITRATE = {"mp3_22050_32": 32000, "mp3_24000_48": 48000, "mp3_44100_32": 32000, "mp3_44100_64": 64000,
               "mp3_44100_96": 96000, "mp3_44100_128": 128000, "mp3_44100_192": 192000}
MAX_ITEMS = 3000
TEXT_LIMIT = 400_000          # one rewritten turn; the widget had no length limit either
KEY_PART = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
RUN_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")  # one multi-pass rewrite (the browser's random id); a pass appends only to its run
BLOCK_CHARS = 600  # a long paragraph becomes several thumb-sized parts, never a third seek/alignment layer
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
BACKENDS = ("claude", "opencode")
RETRY_DELAYS = (0.5, 1, 2, 4, 8)  # the widget's elevenFetchChunk backoff, transient failures only
# The settings section (ReadAloudSettings): the rewrite's own provider/model, beside elevenlabs.json.
SETTINGS_PATH = CONFIG_PATH.parent / "read-aloud.json"
API = "https://api.elevenlabs.io"
REWRITE_PROVIDERS = ("anthropic", "openrouter", "deepinfra")
REWRITE_MODEL = re.compile(r"[A-Za-z0-9_.:/@-]{1,200}")
# Refine's own thinking levels (refine-core.js THINK_LEVELS); each provider maps them as it does for Refine.
REWRITE_THINKING = ("low", "medium", "high", "xhigh", "max")
VOICE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
TTS_MODEL = re.compile(r"[a-z0-9_.-]{1,80}")
# When the account's model list cannot be read: ElevenLabs' current text-to-speech models.
FALLBACK_TTS_MODELS = [{"id": "eleven_multilingual_v2", "name": "Eleven Multilingual v2"},
                       {"id": "eleven_v3", "name": "Eleven v3"},
                       {"id": "eleven_turbo_v2_5", "name": "Eleven Turbo v2.5"},
                       {"id": "eleven_flash_v2_5", "name": "Eleven Flash v2.5"}]


class ReadAloudError(Exception):
    """An error with an HTTP code and a plain-words message for the player's status line."""

    def __init__(self, code, status, message):
        super().__init__(message)
        self.code, self.status = code, status


def split_chunks(text, limit):
    """The widget's elevenBuildChunks: paragraph (blank-line) chunks, consecutive paragraphs merged while they fit
    under `limit`, an over-long paragraph split at its last sentence end (else space) inside each window."""
    chunks, current = [], None
    for paragraph in re.split(r"\n[ \t]*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > limit:
            if current is not None:
                chunks.append(current)
                current = None
            start = 0
            while start < len(paragraph):
                end = min(start + limit, len(paragraph))
                if end < len(paragraph):
                    window = paragraph[start:end]
                    cut = max(window.rfind(". "), window.rfind(".\n"), window.rfind("! "), window.rfind("? "))
                    if cut < limit * 0.5:
                        cut = window.rfind(" ")
                    if cut > 0:
                        end = start + cut + 1
                piece = paragraph[start:end].strip()
                if piece:
                    chunks.append(piece)
                start = end
        elif current is None:
            current = paragraph
        elif len(current) + 2 + len(paragraph) <= limit:
            current += "\n\n" + paragraph
        else:
            chunks.append(current)
            current = paragraph
    if current is not None:
        chunks.append(current)
    return chunks


def quick_start(chunks, first, *, force=False):
    """Split a long first chunk at its last sentence end (or paragraph break) within `first` characters, so playback
    can start on a short part. `force` retains this short start after paragraph separation removed the old merged
    chunk's tail; short messages and unbreakable openings stay as they are."""
    if not first or not chunks or (not force and len(chunks[0]) <= first * 1.5):
        return chunks
    head = chunks[0]
    window = head[:first]
    cut = max(window.rfind(". "), window.rfind(".\n"), window.rfind("! "), window.rfind("? "), window.rfind("\n\n"))
    if cut < first * 0.4:
        return chunks
    return [piece for piece in [head[:cut + 1].strip(), head[cut + 1:].strip()] if piece] + chunks[1:]


# @beacon[
#   id=opc-pane@listening_chunks,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=New readings use paragraph-sized tap blocks as synthesis parts: never merge paragraphs; split a long one at sentence/word boundaries up to 600 characters or chunk_chars, keep headings with the following prose, and retain the short first-audio part for each rewrite pass. Saved legacy parts are never rechunked.,
# ]
def listening_chunks(text, limit, first):
    """One tap = one exact synthesis start; heading labels stay attached to their first paragraph."""
    chunks, headings, lines = [], [], []

    def flush():
        if not lines:
            return
        paragraph = "\n".join(lines).strip()
        prefix = "\n\n".join(headings)
        room = max(1, min(limit, BLOCK_CHARS) - (len(prefix) + 2 if prefix else 0))
        pieces = split_chunks(paragraph, room)
        if not chunks:
            # Cut the prose, not the heading's blank line: a title must never become a tiny orphan part.
            opening = max(1, first - (len(prefix) + 2 if prefix else 0)) if first else 0
            pieces = quick_start(pieces, opening, force=bool(first and len(text) > first * 1.5))
        if prefix:
            pieces[0] = prefix + "\n\n" + pieces[0]
        chunks.extend(pieces)
        headings.clear()
        lines.clear()

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if HEADING.fullmatch(line):
            flush()
            headings.append(line.strip())
        elif not line.strip():
            flush()
        else:
            lines.append(line)
    flush()
    if headings:
        chunks.append("\n\n".join(headings))
    return chunks


def speech_text(text):
    """Voice a heading's title once, with a pause; formatting never goes to ElevenLabs, including its neighbours."""
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        heading = HEADING.fullmatch(line)
        if heading:
            title = heading[2].strip()
            line = title + ("" if title.endswith((".", "!", "?", ":", "…")) else ".")
        elif re.match(r"^\s*(?:`{3,}|~{3,}|[-*_]{3,}\s*$)", line):
            continue
        else:
            line = re.sub(r"^\s*(?:>\s*|[-*+]\s+)", "", line)
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"!?\[([^\]]*)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    text = re.sub(r"[`*~#]", "", text)
    text = re.sub(r"(?<!\w)_+|_+(?!\w)", "", text)
    return text.strip()


def check_key(key):
    """The item key the browser names a turn by: backend, conversation, message and part (text, or thinking:N)."""
    if (not isinstance(key, dict) or set(key) != {"backend", "conversation", "message", "part"}
            or key["backend"] not in BACKENDS
            or any(not isinstance(key[k], str) or not KEY_PART.fullmatch(key[k]) for k in ("conversation", "message", "part"))
            or not re.fullmatch(r"text|thinking:[0-9]{1,6}", key["part"])):
        raise ReadAloudError(400, "schema", "a read-aloud key needs backend, conversation, message and part (text or thinking:N)")
    return {k: key[k] for k in ("backend", "conversation", "message", "part")}


def item_id(key):
    key = check_key(key)
    return hashlib.sha256(json.dumps([key[k] for k in ("backend", "conversation", "message", "part")]).encode()).hexdigest()[:32]


# @beacon[
#   id=opc-pane@ReadAloudConfig,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Read Aloud's ElevenLabs settings from Dan's local elevenlabs.json (key, voice, model, voice settings, chunk size, playback rate, cache ceiling), re-read when the file changes; public() never carries the key.,
# ]
class ReadAloudConfig:
    def __init__(self, path=CONFIG_PATH):
        self.path, self.lock = Path(path), threading.Lock()
        self._stamp, self._data, self._error = None, None, None

    def load(self):
        """The merged settings, or raise a plain-words error naming the file when the key is missing."""
        with self.lock:
            try:
                stat = self.path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                self._stamp, self._data, self._error = None, None, "not-configured"
                raise ReadAloudError(503, "not-configured", "ElevenLabs is not set up: add your key in 🔊 Read Aloud settings "
                                     f"(⚙ here, Refine setup on the phone, ⚙ Refine settings on the desktop; it is kept in {self.path}), "
                                     "then click again") from None
            if stamp != self._stamp:
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        raise ValueError("not an object")
                    data = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS or k == "api_key"}}
                    data["voice_settings"] = {**DEFAULTS["voice_settings"], **(raw.get("voice_settings") or {})}
                    if not isinstance(data.get("api_key"), str) or not data["api_key"].strip():
                        raise ValueError("api_key missing")
                    if data["output_format"] not in MP3_BITRATE:
                        raise ValueError("output_format must be an mp3_* format")
                    data["chunk_chars"] = min(9500, max(300, int(data["chunk_chars"])))
                    data["continuity_chars"] = min(2000, max(0, int(data["continuity_chars"])))
                    data["first_chunk_chars"] = min(data["chunk_chars"], max(0, int(data["first_chunk_chars"])))
                    self._data, self._error = data, None
                except (OSError, ValueError, TypeError) as e:
                    self._data, self._error = None, f"{self.path} is unreadable or incomplete ({e})"
                self._stamp = stamp
            if self._data is None:
                raise ReadAloudError(503, "not-configured", "ElevenLabs settings: " + (self._error or "missing"))
            return dict(self._data)

    def public(self):
        try:
            data = self.load()
        except ReadAloudError as e:
            return {"configured": False, "error": str(e)}
        return {"configured": True, "voiceId": data["voice_id"], "voiceName": data.get("voice_name"),
                "modelId": data["model_id"], "playbackRate": data["playback_rate"], "chunkChars": data["chunk_chars"]}


# @beacon[
#   id=opc-pane@ElevenLabsVoice.synthesize,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=One chunk through ElevenLabs text-to-speech (the widget's request: voice, Multilingual v2, stability 0.5 / similarity 0.75 / speed 1.2) plus free previous/next text for an even voice across seams; the widget's backoff on network/429/5xx only, plain-words errors for a bad key, voice or quota.,
# ]
class ElevenLabsVoice:
    def __init__(self, opener=urllib.request.urlopen, sleep=time.sleep):
        self.opener, self.sleep = opener, sleep

    def synthesize(self, config, voice, text, previous_text="", next_text=""):
        body = {"text": text, "model_id": voice["model_id"], "voice_settings": voice["voice_settings"]}
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        url = ENDPOINT + urllib.parse.quote(voice["voice_id"], safe="") + "?output_format=" + voice["output_format"]
        last = None
        for attempt in range(len(RETRY_DELAYS) + 1):
            request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
                "xi-api-key": config["api_key"], "Content-Type": "application/json", "Accept": "audio/mpeg"})
            try:
                with self.opener(request, timeout=90) as response:
                    audio = response.read()
                    cost = response.headers.get("character-cost")
                if not audio:
                    raise ReadAloudError(502, "tts-empty", "ElevenLabs returned no audio for this part")
                return audio, int(cost) if cost and cost.isdigit() else None
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    payload = json.loads(e.read() or b"{}")
                    d = payload.get("detail") if isinstance(payload, dict) else None
                    detail = (d.get("message") or d.get("status") or "") if isinstance(d, dict) else str(d or "")
                except (ValueError, OSError):
                    pass
                if e.code == 401:
                    raise ReadAloudError(502, "tts-key", "ElevenLabs rejected the API key (401). Check api_key in "
                                         + str(CONFIG_PATH)) from None
                if e.code in (404, 422):
                    raise ReadAloudError(502, "tts-voice", f"ElevenLabs could not use that voice or model ({e.code}"
                                         + (": " + detail if detail else "") + ")") from None
                if e.code == 429 and "quota" in detail.lower():
                    raise ReadAloudError(502, "tts-quota", "ElevenLabs quota exceeded: " + detail) from None
                if e.code != 429 and not 500 <= e.code <= 599:
                    raise ReadAloudError(502, "tts-refused", f"ElevenLabs refused this part (HTTP {e.code}"
                                         + (": " + detail if detail else "") + ")") from None
                last = f"HTTP {e.code}" + (": " + detail if detail else "")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = "network: " + str(getattr(e, "reason", e))
            if attempt < len(RETRY_DELAYS):
                self.sleep(RETRY_DELAYS[attempt])
        raise ReadAloudError(502, "tts-unavailable", "ElevenLabs did not answer after retries (" + str(last) + ")")


def write_private_json(path, data):
    """Write a settings file atomically, owner-only (mode 600) from its first byte."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def mask_key(key):
    """What the browser may see of a key: its last four characters."""
    return "…" + key[-4:] if isinstance(key, str) and len(key) >= 8 else ("set" if key else None)


def key_print(key):
    """A short fingerprint that ties a cached voice list to the key that read it (never the key itself)."""
    return hashlib.sha256(("read-aloud:" + key).encode()).hexdigest()[:16]


class ElevenLabsAccount:
    """Read-only calls on Dan's ElevenLabs account, made only on an explicit settings action: the voices, the
    text-to-speech models and the subscription (the key test; it costs no characters)."""

    def __init__(self, opener=urllib.request.urlopen):
        self.opener = opener

    def _get(self, key, path):
        request = urllib.request.Request(API + path, method="GET", headers={"xi-api-key": key, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=20) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as e:
            status, detail = "", ""
            try:
                d = json.loads(e.read() or b"{}").get("detail")
                status, detail = (d.get("status") or "", d.get("message") or "") if isinstance(d, dict) else ("", str(d or ""))
            except (ValueError, OSError, AttributeError):
                pass
            if e.code == 401 and status == "missing_permissions":
                raise ReadAloudError(502, "tts-permission", "the key works but lacks a permission ElevenLabs needs for this"
                                     + (": " + detail if detail else "")) from None
            if e.code == 401:
                raise ReadAloudError(502, "tts-key", "ElevenLabs rejected the API key (401)"
                                     + (": " + detail if detail else "")) from None
            raise ReadAloudError(502, "tts-refused", f"ElevenLabs answered HTTP {e.code}" + (": " + detail if detail else "")) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ReadAloudError(502, "tts-unavailable", "ElevenLabs could not be reached (" + str(getattr(e, "reason", e)) + ")") from None
        except ValueError:
            raise ReadAloudError(502, "tts-refused", "ElevenLabs answered something that is not JSON") from None

    def voices(self, key):
        data = self._get(key, "/v1/voices")
        rows = data.get("voices") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ReadAloudError(502, "tts-refused", "ElevenLabs answered no voice list")
        out = [{"id": v["voice_id"], "name": str(v.get("name") or v["voice_id"])[:120], "category": str(v.get("category") or "")[:40]}
               for v in rows if isinstance(v, dict) and isinstance(v.get("voice_id"), str) and VOICE_ID.fullmatch(v["voice_id"])]
        return sorted(out, key=lambda v: v["name"].lower())

    def models(self, key):
        data = self._get(key, "/v1/models")
        if not isinstance(data, list):
            raise ReadAloudError(502, "tts-refused", "ElevenLabs answered no model list")
        return [{"id": m["model_id"], "name": str(m.get("name") or m["model_id"])[:120]} for m in data
                if isinstance(m, dict) and isinstance(m.get("model_id"), str) and TTS_MODEL.fullmatch(m["model_id"])
                and m.get("can_do_text_to_speech", True)]

    def subscription(self, key):
        data = self._get(key, "/v1/user/subscription")
        return data if isinstance(data, dict) else {}


# @beacon[
#   id=opc-pane@ReadAloudSettings,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=The 🔊 Read Aloud settings section's server half, shared by the phone and the desktop: the ElevenLabs key (stored only in elevenlabs.json, mode 600; the browser sees its last four characters), voice and TTS model, and the rewrite's provider/model and thinking-level overrides (read-aloud.json, independent of each other; none = Refine's). ElevenLabs is called only on test and catalog refresh; the voice/model lists are cached on disk per key.,
# ]
class ReadAloudSettings:
    def __init__(self, config=None, account=None, path=SETTINGS_PATH, catalog_path=STATE_DIR / "elevenlabs-catalog.json"):
        self.config = config or ReadAloudConfig()
        self.account = account or ElevenLabsAccount()
        self.path, self.catalog_path = Path(path), Path(catalog_path)
        self.lock = threading.Lock()

    @staticmethod
    def _json(path, strict):
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            if strict:
                raise ReadAloudError(409, "unreadable", f"{path} is not valid JSON; fix or remove it by hand first") from None
            return {}
        if not isinstance(raw, dict):
            if strict:
                raise ReadAloudError(409, "unreadable", f"{path} is not a JSON object; fix or remove it by hand first")
            return {}
        return raw

    def _key(self, raw):
        key = raw.get("api_key")
        return key.strip() if isinstance(key, str) and key.strip() else None

    def rewrite(self):
        raw = self._json(self.path, False)
        provider = raw.get("rewrite_provider") if raw.get("rewrite_provider") in REWRITE_PROVIDERS else None
        model = raw.get("rewrite_model") if provider and isinstance(raw.get("rewrite_model"), str) and REWRITE_MODEL.fullmatch(raw["rewrite_model"]) else None
        thinking = raw.get("rewrite_thinking") if raw.get("rewrite_thinking") in REWRITE_THINKING else None
        return {"provider": provider, "model": model, "thinking": thinking}

    def view(self):
        """The section's whole state: never the key, and no ElevenLabs call (the lists come from the last refresh)."""
        raw = self._json(self.config.path, False)
        key = self._key(raw)
        catalog = self._json(self.catalog_path, False)
        if not key or catalog.get("key") != key_print(key):
            catalog = {}
        public = self.config.public()
        return {"configured": public["configured"], "error": public.get("error"),
                "key": {"set": bool(key), "masked": mask_key(key)},
                "voiceId": raw.get("voice_id") if isinstance(raw.get("voice_id"), str) else DEFAULTS["voice_id"],
                "voiceName": raw.get("voice_name") if isinstance(raw.get("voice_name"), str) else None,
                "modelId": raw.get("model_id") if isinstance(raw.get("model_id"), str) else DEFAULTS["model_id"],
                "defaultModelId": DEFAULTS["model_id"], "rewrite": self.rewrite(),
                "voices": catalog.get("voices"), "models": catalog.get("models") or FALLBACK_TTS_MODELS,
                "modelsFromAccount": bool(catalog.get("models")), "catalogAt": catalog.get("at"),
                "configPath": str(self.config.path)}

    def update(self, changes):
        """Save the fields given; an unreadable elevenlabs.json is never overwritten."""
        eleven, rewrite = {}, None
        if "apiKey" in changes:
            key = changes["apiKey"]
            if not isinstance(key, str) or not re.fullmatch(r"[\x21-\x7e]{16,256}", key.strip()):
                raise ReadAloudError(400, "schema", "an ElevenLabs API key is 16 to 256 visible characters with no spaces")
            eleven["api_key"] = key.strip()
        if "voiceId" in changes:
            if not isinstance(changes["voiceId"], str) or not VOICE_ID.fullmatch(changes["voiceId"]):
                raise ReadAloudError(400, "schema", "voiceId is an ElevenLabs voice id")
            name = changes.get("voiceName")
            if name is not None and (not isinstance(name, str) or len(name) > 200):
                raise ReadAloudError(400, "schema", "voiceName is a short text")
            eleven["voice_id"], eleven["voice_name"] = changes["voiceId"], name or None
        if "modelId" in changes:
            if not isinstance(changes["modelId"], str) or not TTS_MODEL.fullmatch(changes["modelId"]):
                raise ReadAloudError(400, "schema", "modelId is an ElevenLabs model id")
            eleven["model_id"] = changes["modelId"]
        if "rewriteProvider" in changes:
            provider, model = changes["rewriteProvider"] or None, changes.get("rewriteModel") or None
            if provider is not None and provider not in REWRITE_PROVIDERS:
                raise ReadAloudError(400, "schema", "rewriteProvider is anthropic, openrouter, deepinfra or empty (Refine's)")
            if model is not None and (provider is None or not isinstance(model, str) or not REWRITE_MODEL.fullmatch(model)):
                raise ReadAloudError(400, "schema", "rewriteModel is a model id, with a rewriteProvider")
            rewrite = {"rewrite_provider": provider, "rewrite_model": model}
        if "rewriteThinking" in changes:
            # Independent of the model: None (or empty) follows Refine's thinking level.
            thinking = changes["rewriteThinking"] or None
            if thinking is not None and thinking not in REWRITE_THINKING:
                raise ReadAloudError(400, "schema", "rewriteThinking is low, medium, high, xhigh, max or empty (Refine's)")
            rewrite = {**(rewrite or {}), "rewrite_thinking": thinking}
        with self.lock:
            if eleven:
                raw = self._json(self.config.path, True)
                write_private_json(self.config.path, {**raw, **eleven})
            if rewrite is not None:
                write_private_json(self.path, {**self._json(self.path, False), **rewrite})
        return self.view()

    def _need_key(self):
        key = self._key(self._json(self.config.path, False))
        if not key:
            raise ReadAloudError(409, "not-configured", "no ElevenLabs API key yet: paste one and save it first")
        return key

    def test(self):
        """One cheap ElevenLabs call (the subscription read, no characters billed): OK with the usage, or why not."""
        key = self._need_key()
        try:
            sub = self.account.subscription(key)
        except ReadAloudError as e:
            if e.status == "tts-permission":
                return {"ok": True, "message": "ElevenLabs accepted the key (it may not read account details: " + str(e) + ")"}
            return {"ok": False, "message": str(e)}
        bits = ["ElevenLabs accepted the key"]
        if sub.get("tier"):
            bits.append("plan " + str(sub["tier"]))
        if isinstance(sub.get("character_count"), int) and isinstance(sub.get("character_limit"), int):
            bits.append(f"{sub['character_count']:,} of {sub['character_limit']:,} characters used")
        return {"ok": True, "message": " · ".join(bits)}

    def refresh(self):
        """Read the account's voices (and its TTS models, else the fixed list) once, and cache them for this key."""
        key = self._need_key()
        voices = self.account.voices(key)
        try:
            models = self.account.models(key) or None
        except ReadAloudError:
            models = None
        with self.lock:
            write_private_json(self.catalog_path, {"key": key_print(key), "at": int(time.time() * 1000),
                                                   "voices": voices, "models": models})
        return self.view()


def voice_of(config):
    """The voice a saved item keeps, so a replay sounds like its first play even if the config changes later."""
    return {k: config[k] for k in ("voice_id", "voice_name", "model_id", "voice_settings", "output_format")}


def mp3_seconds(size, output_format):
    """A constant-bitrate MP3's length from its size: what the seek bar needs before the browser loads a chunk."""
    return round(size * 8 / MP3_BITRATE.get(output_format, 128000), 2)


# @beacon[
#   id=opc-pane@ReadAloudStore,
#   slice_labels=opc-pane,
#   kind=ast,
#   comment=Saved readings keyed to conversation + message + part: new paragraph-sized tap/synthesis parts retain display headings and separate markup-free speech; legacy items keep their original parts and MP3s. Passes append stable parts while playback continues; each part synthesizes independently on demand (one in flight per part), with spoken neighbours for continuity, so a tapped part never waits for intervening ones. Voice fixed at save/first synthesis; LRU cache bounds unchanged.,
# ]
class ReadAloudStore:
    def __init__(self, root=STATE_DIR, config=None, voice=None, clock=time.time):
        self.root = Path(root)
        self.config = config or ReadAloudConfig()
        self.voice = voice or ElevenLabsVoice()
        self.clock = clock
        self.lock = threading.RLock()
        self.flights: dict = {}

    # ---- files
    def _item_path(self, ident):
        return self.root / "items" / (ident + ".json")

    def _audio_dir(self, ident):
        return self.root / "audio" / ident

    def _read(self, ident):
        try:
            data = json.loads(self._item_path(ident).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None  # an unreadable item is treated as absent; saving again replaces it
        return data if isinstance(data, dict) and data.get("id") == ident else None

    def _write(self, item):
        path = self._item_path(item["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def _view(self, item):
        """Display text and cached-part lengths (spoken characters for estimates), never a path or a key."""
        voice = item.get("voice") or {}
        fmt = voice.get("output_format", DEFAULTS["output_format"])
        chunks = []
        for index, chunk in enumerate(item["chunks"]):
            audio = self._audio_dir(item["id"]) / f"{index}.mp3"
            size = audio.stat().st_size if audio.is_file() else None
            spoken = item.get("speech", item["chunks"])[index]
            chunks.append({"index": index, "text": chunk, "chars": len(spoken), "cached": size is not None,
                           "bytes": size, "seconds": mp3_seconds(size, fmt) if size else None})
        return {"id": item["id"], "key": item["key"], "sourceHash": item["sourceHash"], "text": item["text"],
                "chunks": chunks, "meta": item.get("meta") or {}, "createdAt": item["createdAt"],
                "complete": item.get("complete", True),
                "voice": {"voiceId": voice["voice_id"], "modelId": voice["model_id"],
                          "voiceName": voice.get("voice_name")} if voice else None,
                "characterCost": sum(item.get("costs", {}).values()) if item.get("costs") else None}

    # ---- operations
    def get(self, key):
        ident = item_id(key)
        with self.lock:
            item = self._read(ident)
            if item is None:
                return {"found": False, "id": ident, "config": self.config.public()}
            item["usedAt"] = self.clock()
            self._write(item)
            return {"found": True, "id": ident, "item": self._view(item), "config": self.config.public()}

    def save(self, key, source_hash, text, meta=None, run=None, segment=None, done=True):
        """Replace this turn's saved result (its old audio goes with it) and answer the new item.

        A long message's rewrite arrives in passes, saved as each lands so its audio can start: segment 0 of a run
        starts a new, incomplete item; each later segment of the same run appends its text and its own parts (each
        pass split on its own into paragraph-sized tap/synthesis parts, with its first part short), so the parts
        already handed to the voice never move and a replay reuses every MP3; `done` on the last completes it.
        Headings remain in display text; the separately saved speech has no Markdown. Old items are read as-is."""
        key = check_key(key)
        if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            raise ReadAloudError(400, "schema", "sourceHash must be the message text's SHA-256 (64 hex)")
        if not isinstance(text, str) or not text.strip():
            raise ReadAloudError(400, "schema", "nothing to save: the rewritten text is empty")
        if len(text) > TEXT_LIMIT:
            raise ReadAloudError(413, "too-large", "the rewritten text is too long to save")
        meta = meta if isinstance(meta, dict) else {}
        if len(json.dumps(meta)) > 16 * 1024:
            raise ReadAloudError(400, "schema", "meta is too large")
        if segment is None:
            if run is not None or done is not True:
                raise ReadAloudError(400, "schema", "run and done belong to a pass (with its segment)")
        elif (not isinstance(run, str) or not RUN_ID.fullmatch(run) or type(segment) is not int or not 0 <= segment < 1000
              or type(done) is not bool):
            raise ReadAloudError(400, "schema", "a pass needs run (8-64 letters, digits, - or _), segment 0-999 and done true|false")
        try:
            config = self.config.load()
        except ReadAloudError:
            config = None  # the rewrite is kept anyway; its voice is chosen when ElevenLabs is set up
        ident = item_id(key)
        chunks = listening_chunks(text, (config or DEFAULTS)["chunk_chars"], (config or DEFAULTS)["first_chunk_chars"])
        speech = [speech_text(chunk) for chunk in chunks]
        if not chunks or not all(speech):
            raise ReadAloudError(400, "schema", "nothing to read: the rewritten text has no words")
        if segment:
            with self.lock:
                item = self._read(ident)
                if (item is None or item.get("run") != run or item.get("complete", True) or item["sourceHash"] != source_hash
                        or len(item.get("segments") or []) != segment):
                    raise ReadAloudError(409, "replaced", "this reading was restarted meanwhile (another tab or device); "
                                         "what was already rewritten stays in this player")
                if len(item["text"]) + 2 + len(text) > TEXT_LIMIT:
                    raise ReadAloudError(413, "too-large", "the rewritten text is too long to save")
                item["text"] += "\n\n" + text
                # An in-flight rewrite begun by the previous server may still append after activation.
                # Its existing speech is its original chunk text; never rechunk or invalidate cached audio.
                item["speech"] = item.get("speech", item["chunks"]).copy() + speech
                item["chunks"] += chunks
                item["segments"].append(len(chunks))
                item["complete"] = done
                item["meta"] = meta or item.get("meta") or {}
                item["usedAt"] = self.clock()
                self._write(item)
                return {"found": True, "id": ident, "item": self._view(item), "config": self.config.public()}
        now = self.clock()
        item = {"schemaVersion": 1, "id": ident, "key": key, "sourceHash": source_hash, "text": text, "chunks": chunks, "speech": speech,
                "voice": voice_of(config) if config else None,
                "continuity": (config or DEFAULTS)["continuity_chars"], "meta": meta, "createdAt": now, "usedAt": now, "costs": {}}
        if segment is not None:
            item.update(run=run, segments=[len(chunks)], complete=done)
        with self.lock:
            shutil.rmtree(self._audio_dir(ident), ignore_errors=True)
            self._write(item)
            self._prune(keep=ident)
            return {"found": True, "id": ident, "item": self._view(item), "config": self.config.public()}

    def audio(self, ident, index):
        """One requested part now, independently of earlier parts; new items voice speech, legacy items their old text."""
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident) or type(index) is not int or index < 0:
            raise ReadAloudError(400, "schema", "audio needs an item id and a chunk number")
        with self.lock:
            item = self._read(ident)
            if item is None:
                raise ReadAloudError(404, "missing", "this read-aloud result is no longer saved; regenerate it")
            if index >= len(item["chunks"]):
                raise ReadAloudError(400, "schema", "no such part")
            flight = self.flights.setdefault((ident, index), threading.Lock())
        path = self._audio_dir(ident) / f"{index}.mp3"
        with flight:  # the player's fetch and its prefetch of the same part never pay twice
            if path.is_file():
                data = path.read_bytes()
                self._touch(ident)
                return data, True, mp3_seconds(len(data), (item.get("voice") or DEFAULTS)["output_format"])
            config = self.config.load()  # no key: a plain-words "set it up" error, the saved text untouched
            voice = item.get("voice") or voice_of(config)
            chunks, keep = item.get("speech", item["chunks"]), int(item.get("continuity", 0))
            previous = "\n\n".join(chunks[:index])[-keep:] if keep and index else ""
            following = "\n\n".join(chunks[index + 1:])[:keep] if keep else ""
            data, cost = self.voice.synthesize(config, voice, chunks[index], previous, following)
            with self.lock:
                current = self._read(ident)
                if current is None or current["createdAt"] != item["createdAt"]:
                    # Regenerated or evicted while this part was synthesized: never mix voices or texts.
                    raise ReadAloudError(409, "replaced", "this result was regenerated meanwhile; play it again")
                if current.get("voice") not in (None, voice):
                    raise ReadAloudError(409, "replaced", "this result's voice changed meanwhile; play it again")
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
                current["voice"] = voice  # the first synthesis fixes the voice of an item saved before setup
                current.setdefault("costs", {})[str(index)] = cost if cost is not None else len(chunks[index])
                current["usedAt"] = self.clock()
                self._write(current)
                self._prune(keep=ident)
            return data, False, mp3_seconds(len(data), voice["output_format"])

    def _touch(self, ident):
        with self.lock:
            item = self._read(ident)
            if item is not None:
                item["usedAt"] = self.clock()
                self._write(item)

    def _prune(self, keep=None):
        """Least recently used items lose their audio past the ceiling; the oldest items go past MAX_ITEMS."""
        try:
            limit = int(self.config.load().get("cache_max_mb", DEFAULTS["cache_max_mb"])) * 1024 * 1024
        except ReadAloudError:
            limit = DEFAULTS["cache_max_mb"] * 1024 * 1024
        items = []
        for path in (self.root / "items").glob("*.json"):
            item = self._read(path.stem)
            if item is not None:
                audio = self._audio_dir(item["id"])
                size = sum(f.stat().st_size for f in audio.glob("*.mp3")) if audio.is_dir() else 0
                items.append((item.get("usedAt", 0), item["id"], size))
        items.sort()
        while len(items) > MAX_ITEMS:
            _, ident, _ = items.pop(0)
            if ident != keep:
                shutil.rmtree(self._audio_dir(ident), ignore_errors=True)
                self._item_path(ident).unlink(missing_ok=True)
        total = sum(size for _, _, size in items)
        for _, ident, size in items:
            if total <= limit:
                break
            if ident == keep or not size:
                continue
            shutil.rmtree(self._audio_dir(ident), ignore_errors=True)
            total -= size
