"""Root-search guard: is one Bash, Grep or Glob call a whole-tree search started at a root?

The contract (which paths are roots, which commands walk, the cd tracking and the
refuse-only-when-certain rule) is docs/root-search-guard.md; scripts/fixtures/root_search_vectors.json
holds the shared test vectors, which the held OpenCode port (branch feat/root-search-guard-2) must pass
before it ships. `judge` is pure apart from two probes `Env` makes injectable: `.git` lookups and a
folder listing, made only for a search's starting folder (is it a repository, or a folder of
repositories?) and for a refusal's message. Any doubt returns None, which the caller reads as allow.
"""
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import posixpath
import re

ROOTS_CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'claude-headless-root-search.json'
ROOTS_ENV = 'ROOT_SEARCH_GUARD_ROOTS'
"""The setting: extra root folders (each with every ancestor), `{"extra_roots": [...]}` in ROOTS_CONFIG beside
the host's other settings, or the environment variable (os.pathsep-separated; set, even empty, it wins). Empty
by default (docs/root-search-guard.md)."""
REPOSITORY_PROBES = 3  # a folder of repositories: one of its first three non-hidden subfolders holds .git
NOT_LISTED = {'tmp', 'node_modules', '__pycache__'}
LISTED_MAX = 20
PIECE_MAX = 200
MAX_DEPTH = 6  # nested bash -c / $(…) levels judged; deeper is doubt


class Doubt(Exception):
    """The command cannot be read with certainty: allow it."""


@dataclass
class Refusal:
    tool: str          # Bash | Grep | Glob
    piece: str         # the refused command piece (Bash) or a short form of the tool call
    command: str       # grep -r | find | rg | Glob | ...
    walk: str          # whole-tree | ignore-aware
    root: str
    root_kind: str     # fixed | repository
    message: str = ''

    def record(self):
        return asdict(self)


def ancestors(path):
    out = [path]
    while path != '/':
        path = posixpath.dirname(path)
        out.append(path)
    return out


def default_listdir(path):
    with os.scandir(path) as entries:
        return [entry.name for entry in entries if entry.is_dir()]


class Env:
    """Where roots are. Tests inject `is_repo` and `listdir`; the host uses the real filesystem and the setting."""

    def __init__(self, home=None, extra_roots=(), is_repo=None, listdir=None):
        self.home = normalize(home or os.path.expanduser('~'))
        roots = (normalize(self.home + root[1:] if root == '~' or root.startswith('~/') else root)
                 for root in extra_roots if isinstance(root, str) and root)
        self.extra_roots = [root for root in roots if root.startswith('/')]  # a relative entry names nothing
        self.fixed = set(ancestors(self.home))
        for root in self.extra_roots + ['/mnt/' + letter for letter in 'abcdefghijklmnopqrstuvwxyz']:
            self.fixed |= set(ancestors(root))
        self.is_repo = is_repo or (lambda path: os.path.lexists(posixpath.join(path, '.git')))
        self.listdir = listdir or default_listdir

    def root_kind(self, path):
        """'fixed' (a configured root, $HOME, a drive root, or an ancestor of one), 'repository' (a git top-level),
        'repositories' (a folder of repositories), or None. Called only for a search's starting folder."""
        if path in self.fixed:
            return 'fixed'
        try:
            if self.is_repo(path):
                return 'repository'
        except OSError:
            return None
        return 'repositories' if self.holds_repositories(path) else None

    def holds_repositories(self, path):
        """A folder of repositories: one of its first REPOSITORY_PROBES non-hidden subfolders (by name) holds a
        .git entry. One listing and at most three lookups; a false positive is accepted (the one rule that does
        not wait for certainty), any OSError is not a root."""
        try:
            names = sorted(name for name in self.listdir(path) if not name.startswith('.'))
            return any(self.is_repo(posixpath.join(path, name)) for name in names[:REPOSITORY_PROBES])
        except OSError:
            return False


def normalize(path):
    path = re.sub(r'^([A-Za-z]):[\\/]', lambda m: '/mnt/' + m.group(1).lower() + '/', path)
    if path.startswith('/mnt/') and '\\' in path:
        path = path.replace('\\', '/')
    path = posixpath.normpath(path)
    return '/' + path.lstrip('/') if path.startswith('/') else path


# ---------------------------------------------------------------- lexer

@dataclass
class Word:
    value: str = ''
    raw_start: int = 0
    raw_end: int = 0
    dynamic: bool = False      # $VAR, $(…), backticks: the value is not known before running
    glob: bool = False         # unquoted * ? [
    tilde: bool = False        # unquoted leading ~
    quoted: bool = False
    nested: list = field(default_factory=list)  # command texts inside $(…), backticks, <(…)


@dataclass
class Op:
    text: str
    start: int


CONTROL = re.compile(r'&&|\|\||\|&|;;&|;;|;&|;|\||&|\(|\)')
REDIRECT = re.compile(r'(\d*)(<<<|<<-|<<|&>>|&>|>>|>&|<&|>\||<>|<|>)')
META = set(' \t\r\n;&|<>()')


def close_paren(src, i):
    """Index just past the `)` closing a `$(` or `<(` whose body starts at i."""
    depth = 1
    n = len(src)
    while i < n:
        c = src[i]
        if c == '\\':
            i += 2
            continue
        if c == "'":
            j = src.find("'", i + 1)
            if j < 0:
                raise Doubt('unterminated quote')
            i = j + 1
            continue
        if c == '"':
            i = skip_double(src, i + 1)
            continue
        if c == '`':
            i = close_backtick(src, i + 1)
            continue
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise Doubt('unbalanced $(')


def skip_double(src, i):
    n = len(src)
    while i < n:
        c = src[i]
        if c == '\\':
            i += 2
            continue
        if c == '"':
            return i + 1
        if src.startswith('$(', i):
            i = close_paren(src, i + 2)
            continue
        if c == '`':
            i = close_backtick(src, i + 1)
            continue
        i += 1
    raise Doubt('unterminated double quote')


def close_backtick(src, i):
    n = len(src)
    while i < n:
        if src[i] == '\\':
            i += 2
            continue
        if src[i] == '`':
            return i + 1
        i += 1
    raise Doubt('unterminated backtick')


def close_brace(src, i):
    j = src.find('}', i)
    if j < 0:
        raise Doubt('unterminated ${')
    return j + 1


def dollar(src, i, word, chars):
    """Read one `$…` at i (inside or outside double quotes); a literal `$` goes to `chars`. Returns the
    index after it."""
    if src.startswith('$((', i):
        word.dynamic = True
        return close_paren(src, i + 2)
    if src.startswith('$(', i):
        end = close_paren(src, i + 2)
        word.nested.append(src[i + 2:end - 1])
        word.dynamic = True
        return end
    if src.startswith('${', i):
        word.dynamic = True
        return close_brace(src, i + 2)
    nxt = src[i + 1:i + 2]
    if nxt and (nxt.isalnum() or nxt in '_@*#?$!-'):
        word.dynamic = True
        j = i + 1
        if nxt.isalpha() or nxt == '_':
            while j < len(src) and (src[j].isalnum() or src[j] == '_'):
                j += 1
            return j
        return j + 1
    chars.append('$')
    return i + 1


def read_word(src, i):
    word = Word(raw_start=i)
    chars = []  # joined once at the end: appending to word.value per character is quadratic on long words
    n = len(src)
    if src[i] == '~':
        word.tilde = True
    while i < n:
        c = src[i]
        if c in META:
            break
        if c == '\\':
            if src[i + 1:i + 2] == '\n':
                i += 2
                continue
            chars.append(src[i + 1:i + 2])
            word.quoted = True
            i += 2
            continue
        if c == "'":
            j = src.find("'", i + 1)
            if j < 0:
                raise Doubt('unterminated quote')
            chars.append(src[i + 1:j])
            word.quoted = True
            i = j + 1
            continue
        if src.startswith("$'", i):
            j = i + 2
            while j < n and src[j] != "'":
                j += 2 if src[j] == '\\' else 1
            if j >= n:
                raise Doubt('unterminated $quote')
            chars.append(src[i + 2:j])
            word.quoted = True
            i = j + 1
            continue
        if c == '"':
            word.quoted = True
            i += 1
            while True:
                if i >= n:
                    raise Doubt('unterminated double quote')
                c = src[i]
                if c == '"':
                    i += 1
                    break
                if c == '\\' and src[i + 1:i + 2] in ('$', '`', '"', '\\', '\n'):
                    chars.append(src[i + 1] if src[i + 1] != '\n' else '')
                    i += 2
                elif c == '$':
                    i = dollar(src, i, word, chars)
                elif c == '`':
                    end = close_backtick(src, i + 1)
                    word.nested.append(src[i + 1:end - 1])
                    word.dynamic = True
                    i = end
                else:
                    chars.append(c)
                    i += 1
            continue
        if c == '$':
            i = dollar(src, i, word, chars)
            continue
        if c == '`':
            end = close_backtick(src, i + 1)
            word.nested.append(src[i + 1:end - 1])
            word.dynamic = True
            i = end
            continue
        if c in '*?[':
            word.glob = True
        chars.append(c)
        i += 1
    word.value = ''.join(chars)
    word.raw_end = i
    return word, i


def skip_heredocs(src, i, pending):
    for delimiter, strip_tabs in pending:
        while i < len(src):
            end = src.find('\n', i)
            line = src[i:] if end < 0 else src[i:end]
            i = len(src) if end < 0 else end + 1
            if (line.lstrip('\t') if strip_tabs else line) == delimiter:
                break
    return i


def lex(src):
    """Words and control operators; redirections and their targets, comments and heredoc bodies
    are dropped."""
    tokens = []
    heredocs = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '\n':
            tokens.append(Op('\n', i))
            i += 1
            if heredocs:
                i = skip_heredocs(src, i, heredocs)
                heredocs = []
            continue
        if c in ' \t\r':
            i += 1
            continue
        if c == '\\' and src[i + 1:i + 2] == '\n':
            i += 2
            continue
        if c == '#':
            end = src.find('\n', i)
            i = n if end < 0 else end
            continue
        if src.startswith('<(', i) or src.startswith('>(', i):
            end = close_paren(src, i + 2)
            tokens.append(Word(value='', raw_start=i, raw_end=end, dynamic=True, nested=[src[i + 2:end - 1]]))
            i = end
            continue
        redirect = REDIRECT.match(src, i)
        if redirect:
            op = redirect.group(2)
            i = redirect.end()
            while i < n and src[i] in ' \t':
                i += 1
            if i >= n or src[i] in META:
                if op in ('>&', '<&') and src[i:i + 1] == '-':
                    i += 1
                    continue
                raise Doubt('redirection without a target')
            target, i = read_word(src, i)
            if op in ('<<', '<<-'):
                heredocs.append((target.value, op == '<<-'))
            for nested in target.nested:  # a target's $(…) still runs
                tokens.append(Word(value='', raw_start=target.raw_start, raw_end=target.raw_end,
                                   dynamic=True, nested=[nested]))
            continue
        control = CONTROL.match(src, i)
        if control:
            tokens.append(Op(control.group(0), i))
            i = control.end()
            continue
        word, i = read_word(src, i)
        tokens.append(word)
    return tokens


# ---------------------------------------------------------------- shell walk

KEYWORDS = {'!', '{', '}', 'then', 'do', 'else', 'elif', 'if', 'while', 'until', 'fi', 'done', 'esac'}
OPENERS = {'if', 'while', 'until', 'for', 'select', 'case'}  # a cd inside may not run, or run more than once
CLOSERS = {'fi', 'done', 'esac', '}'}
LIST_STARTS = {None, ';', '\n', '&', ';;', ';&', ';;&', '(', ')'}  # operators after which an and/or list begins
SHELLS = {'bash', 'sh', 'zsh', 'dash', 'ksh'}
GREPS = {'grep', 'egrep', 'fgrep', 'rgrep'}
ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=')


# @beacon[
#   id=opc@root_search.judge_shell,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=Walks one shell command's pieces in order, tracking the folder through cd/pushd/popd and subshells; a cd whose effect is uncertain (after ||, backgrounded with &, inside an if/case/loop or a function body) makes the folder unknown. Returns the first root-search Refusal.,
# ]
def judge_shell(src, cwd, env, depth=0):
    """The first root-search Refusal in a shell command, or None. `cwd` is the folder at its start
    (None: unknown). Raises Doubt when the command cannot be read with certainty."""
    if depth > MAX_DEPTH:
        raise Doubt('nested too deep')
    tokens = lex(src)
    subshells = []
    dirs = []
    piece = []
    piped = False
    # after: the operator before this piece; compounds: the open if/case/loop ('branch'), function body
    # ('function') and plain { … } ('brace') around it; list_cwd: the folder where this and/or list began.
    state = {'cwd': cwd, 'after': None, 'compounds': [], 'function_next': False, 'list_cwd': cwd}
    previous = None
    for token in tokens + [Op('\n', len(src))]:
        if isinstance(token, Word):
            piece.append(token)
            previous = token
            continue
        if piece:
            if state['after'] in LIST_STARTS:
                state['list_cwd'] = state['cwd']
            refusal = judge_piece(src, piece, state, dirs, piped, env, depth)
            if refusal:
                return refusal
            piece = []
            if token.text == '&' and state['cwd'] != state['list_cwd']:
                state['cwd'] = None  # a list run with & moved only its background subshell; folder unknown
        piped = token.text in ('|', '|&')
        if token.text == '(':
            subshells.append((state['cwd'], list(dirs)))
        elif token.text == ')':
            if isinstance(previous, Op) and previous.text == '(':
                state['function_next'] = True  # `name ()`: a function definition, its body the next { … }
            if subshells:
                state['cwd'], dirs = subshells.pop()
            else:
                state['cwd'] = None  # a case pattern or something unread: the folder is unknown
        state['after'] = token.text
        previous = token
    return None


def enter_compounds(words, state):
    """Consume one piece's leading compound-command keywords, keeping state['compounds'] (the contexts in
    which a cd is uncertain) current; returns the rest of the piece."""
    while words and not words[0].quoted and not words[0].dynamic:
        name = words[0].value
        if name in OPENERS:
            state['compounds'].append('branch')
            if name in ('for', 'select', 'case'):
                return []  # `for x in …`, `case word in`: a word list, no command
        elif name in CLOSERS:
            if state['compounds']:
                state['compounds'].pop()
        elif name == '{':
            # A { … } right after || runs only when the left side failed, like a branch.
            kind = 'function' if state['function_next'] else 'branch' if state['after'] == '||' else 'brace'
            state['compounds'].append(kind)
            state['function_next'] = False
        elif name == 'function' and len(words) > 1:
            state['function_next'] = True
            words = words[1:]
        elif name not in ('then', 'do', 'else', 'elif', '!'):
            break
        words = words[1:]
    return words


def resolve(word, cwd, env):
    """The absolute path a literal operand names, or None when unknown."""
    if word.dynamic or word.glob or not word.value:
        return None
    value = word.value
    if word.tilde:
        if value == '~' or value.startswith('~/'):
            value = env.home + value[1:]
        else:
            return None
    value = normalize(value) if value[:1] == '/' or re.match(r'^[A-Za-z]:[\\/]', value) else value
    if not value.startswith('/'):
        if cwd is None:
            return None
        value = posixpath.join(cwd, value)
    return normalize(value)


def operand_start(word, cwd, env):
    """The folder a search operand starts at: `*` and `dir/*` stand for the folder itself; other
    globs are never roots."""
    if word.glob:
        if word.dynamic:
            return None
        value = word.value
        if value == '*':
            return cwd
        if value.endswith('/*') and not any(ch in value[:-2] for ch in '*?['):
            parent = Word(value=value[:-2] or '/', tilde=word.tilde)
            return resolve(parent, cwd, env)
        return None
    return resolve(word, cwd, env)


def strip_wrappers(words, state, env):
    """Drop leading keywords, assignments and wrapper commands; returns (words, fed_by_xargs)."""
    fed = False
    while words:
        name = words[0].value if not words[0].dynamic else None
        if name in KEYWORDS and not words[0].quoted:
            words = words[1:]
            continue
        if name and ASSIGNMENT.match(name) and not words[0].quoted:
            words = words[1:]
            continue
        base = posixpath.basename(name) if name else None
        rest = words[1:]
        if base == 'timeout':
            while rest and rest[0].value.startswith('-'):
                flag = rest[0].value
                rest = rest[2:] if flag in ('-s', '-k', '--signal', '--kill-after') else rest[1:]
            words = rest[1:]  # the duration
            continue
        if base == 'nice':
            while rest and rest[0].value.startswith('-'):
                rest = rest[2:] if rest[0].value == '-n' else rest[1:]
            words = rest
            continue
        if base in ('nohup', 'builtin', 'exec', 'command', 'stdbuf', 'sudo', 'time'):
            if base == 'command' and rest and rest[0].value in ('-v', '-V'):
                return [], fed
            while rest and rest[0].value.startswith('-'):
                takes = {'sudo': ('-u', '-g', '-h', '-p', '-C', '-D', '-r', '-t', '-U'),
                         'exec': ('-a',), 'stdbuf': ('-i', '-o', '-e')}.get(base, ())
                rest = rest[2:] if rest[0].value in takes else rest[1:]
            words = rest
            continue
        if base == 'env':
            while rest and (rest[0].value.startswith('-') or ASSIGNMENT.match(rest[0].value)):
                flag = rest[0].value
                if flag in ('-C', '--chdir') or flag.startswith('--chdir='):
                    target = rest[1] if flag in ('-C', '--chdir') and len(rest) > 1 else Word(
                        value=flag.split('=', 1)[-1])
                    state['cwd'] = None if target is None else resolve(target, state['cwd'], env)
                rest = rest[2:] if flag in ('-u', '-C', '--chdir', '--unset', '-S') else rest[1:]
            words = rest
            continue
        if base == 'xargs':
            takes = ('-I', '-n', '-P', '-d', '-E', '-L', '-s', '-a', '-e')
            while rest and rest[0].value.startswith('-'):
                rest = rest[2:] if rest[0].value in takes else rest[1:]
            words = rest
            fed = True
            continue
        return words, fed
    return words, fed


def judge_piece(src, words, state, dirs, piped, env, depth):
    for word in words:
        for nested in word.nested:
            refusal = judge_shell(nested, state['cwd'], env, depth + 1)
            if refusal:
                return refusal
    words = enter_compounds(words, state)
    outer_cwd = state['cwd']
    words, fed = strip_wrappers(words, state, env)
    inner_cwd = state['cwd']
    state['cwd'] = outer_cwd  # env -C only moves the wrapped command
    if not words or words[0].dynamic:
        return None
    name = posixpath.basename(words[0].value)
    args = words[1:]
    if name in ('cd', 'pushd', 'popd'):
        if name == 'popd':
            state['cwd'] = dirs.pop() if dirs and not args else None
        else:
            operands = [a for a in args if not (a.value.startswith('-') and len(a.value) > 1 and not a.quoted)]
            if name == 'pushd':
                dirs.append(state['cwd'])
            if not operands:
                state['cwd'] = env.home if name == 'cd' else None
            elif operands[0].value == '-' or operands[0].value.startswith('+'):
                state['cwd'] = None
            else:
                state['cwd'] = resolve(operands[0], state['cwd'], env)
        # Every cd is assumed to succeed, but one that may not run at all leaves the folder unknown (a
        # backgrounded one: judge_shell). Only removes refusals (docs/root-search-guard.md).
        if state['after'] == '||' or any(kind != 'brace' for kind in state['compounds']):
            state['cwd'] = None
        return None
    if name in SHELLS:
        script = shell_command_string(args)
        if script is None:
            return None
        return judge_shell(script.value, inner_cwd, env, depth + 1) if not script.dynamic else None
    if name == 'eval':
        if any(a.dynamic for a in args):
            return None
        return judge_shell(' '.join(a.value for a in args), inner_cwd, env, depth + 1)
    parsed = SEARCHES.get(name)
    if parsed is None or any(a.value in ('--version', '--help') for a in args):
        return None
    # rg, ag and ack with no path operand read a pipe; GNU grep -r with none searches the current folder
    # even then. Under xargs, any of them gets its operands from the input.
    found = parsed(args, fed if name in GREPS else piped or fed)
    if found is None:
        return None
    command, walk, operands = found
    starts = [operand_start(word, inner_cwd, env) for word in operands] if operands else [inner_cwd]
    piece = src[words[0].raw_start:words[-1].raw_end] if words else ''
    for start in starts:
        if start is None:
            continue
        kind = env.root_kind(start)
        if kind and (walk == 'whole-tree' or kind != 'repository'):
            return Refusal('Bash', piece, command, walk, start, kind)
    return None


def shell_command_string(args):
    """The command string of `bash -c '…'` (None when the shell runs a script or stdin)."""
    i = 0
    has_c = False
    while i < len(args):
        value = args[i].value
        if value in ('-o', '+o', '-O', '+O', '--rcfile', '--init-file'):
            i += 2
            continue
        if value.startswith('--'):
            i += 1
            continue
        if re.match(r'^[-+][A-Za-z]+$', value):
            has_c = has_c or (value[0] == '-' and 'c' in value)
            i += 1
            continue
        break
    return args[i] if has_c and i < len(args) else None


# ---------------------------------------------------------------- the search commands

def options(args, short_takes, long_takes):
    """Yield (kind, name, value_word) over GNU-style arguments: kind 'short' | 'long' | 'operand'."""
    i = 0
    only_operands = False
    while i < len(args):
        word = args[i]
        value = word.value
        i += 1
        if only_operands or word.dynamic and not value.startswith('-') or value in ('-', '') or not value.startswith('-'):
            yield 'operand', None, word
            continue
        if value == '--':
            only_operands = True
            continue
        if value.startswith('--'):
            name, eq, inline = value[2:].partition('=')
            if eq:
                yield 'long', name, Word(value=inline)
            elif name in long_takes and i < len(args):
                yield 'long', name, args[i]
                i += 1
            else:
                yield 'long', name, None
            continue
        cluster = value[1:]
        for k, ch in enumerate(cluster):
            if ch in short_takes:
                rest = cluster[k + 1:]
                if rest:
                    yield 'short', ch, Word(value=rest)
                elif i < len(args):
                    yield 'short', ch, args[i]
                    i += 1
                else:
                    yield 'short', ch, None
                break
            yield 'short', ch, None


def depth_at_most(word, limit=2):
    if word is None or word.dynamic:
        return True  # unknown: allow
    try:
        return int(word.value) <= limit
    except ValueError:
        return True


def parse_grep(args, fed, always_recursive=False):
    recursive = always_recursive
    pattern_given = False
    positional = []
    for kind, name, value in options(args, set('efmABCdD'),
            {'regexp', 'file', 'max-count', 'label', 'after-context', 'before-context', 'context',
             'binary-files', 'devices', 'directories', 'exclude', 'exclude-from', 'exclude-dir',
             'include', 'group-separator'}):
        if kind == 'operand':
            positional.append(value)
        elif name in ('r', 'R', 'recursive', 'dereference-recursive'):
            recursive = True
        elif name in ('d', 'directories') and value is not None and value.value == 'recurse':
            recursive = True
        elif name in ('e', 'f', 'regexp', 'file'):
            pattern_given = True
    if not recursive:
        return None
    operands = positional if pattern_given else positional[1:]
    if not operands and fed:
        return None
    return ('grep -r', 'whole-tree', operands)


def parse_find(args, piped):
    i = 0
    while i < len(args) and args[i].value in ('-H', '-L', '-P') or (
            i < len(args) and (args[i].value == '-D' or args[i].value.startswith('-O'))):
        i += 2 if args[i].value == '-D' else 1
    operands = []
    while i < len(args) and not args[i].value.startswith('-') and args[i].value not in ('(', '!', ','):
        operands.append(args[i])
        i += 1
    rest = args[i:]
    for k, word in enumerate(rest):
        if word.value == '-maxdepth' and depth_at_most(rest[k + 1] if k + 1 < len(rest) else None):
            return None
    return ('find', 'whole-tree', operands)


def parse_ls(args, piped):
    recursive = False
    operands = []
    for kind, name, value in options(args, set('IwT'), {'ignore', 'hide', 'width', 'tabsize', 'format', 'sort',
                                                       'time', 'block-size', 'quoting-style', 'indicator-style',
                                                       'time-style', 'color'}):
        if kind == 'operand':
            operands.append(value)
        elif name in ('R', 'recursive'):
            recursive = True
    return ('ls -R', 'whole-tree', operands) if recursive else None


def parse_tree(args, piped):
    walk = 'whole-tree'
    operands = []
    for kind, name, value in options(args, set('LPIoHT'), {'filelimit', 'charset', 'timefmt', 'sort'}):
        if kind == 'operand':
            operands.append(value)
        elif name == 'L' and depth_at_most(value):
            return None
        elif name == 'gitignore':
            walk = 'ignore-aware'
    return ('tree', walk, operands)


def parse_du(args, piped):
    operands = [value for kind, name, value in options(args, set('BdtX'),
                {'block-size', 'max-depth', 'threshold', 'exclude', 'exclude-from', 'time-style', 'files0-from'})
                if kind == 'operand']
    return ('du', 'whole-tree', operands)


def parse_ack(args, piped):
    no_pattern = False
    positional = []
    for kind, name, value in options(args, set('ABCgmt'), {'type', 'ignore-dir', 'noignore-dir', 'match', 'output',
                                                         'ignore-file', 'max-count', 'context', 'after-context',
                                                         'before-context', 'type-add', 'type-set'}):
        if kind == 'operand':
            positional.append(value)
        elif name in ('f', 'g', 'match'):
            no_pattern = True
    operands = positional if no_pattern else positional[1:]
    if not operands and piped:
        return None
    return ('ack', 'whole-tree', operands)


RG_SHORT = set('ABCdEefgjMmrtT')
RG_LONG = {'after-context', 'before-context', 'context', 'max-depth', 'maxdepth', 'encoding', 'regexp', 'file',
           'glob', 'iglob', 'threads', 'max-columns', 'max-count', 'replace', 'type', 'type-not', 'type-add',
           'type-clear', 'colors', 'context-separator', 'field-context-separator', 'field-match-separator',
           'path-separator', 'dfa-size-limit', 'regex-size-limit', 'engine', 'ignore-file', 'max-filesize', 'pre',
           'pre-glob', 'sort', 'sortr', 'hostname-bin', 'hyperlink-format', 'generate'}


def parse_rg(args, piped):
    ignore_off = False
    no_pattern = False
    positional = []
    for kind, name, value in options(args, RG_SHORT, RG_LONG):
        if kind == 'operand':
            positional.append(value)
        elif name in ('u', 'unrestricted', 'no-ignore', 'no-ignore-vcs'):
            ignore_off = True
        elif name in ('e', 'f', 'regexp', 'file', 'files'):
            no_pattern = True
        elif name in ('d', 'max-depth', 'maxdepth') and depth_at_most(value):
            return None
        elif name in ('V', 'h', 'type-list', 'pcre2-version'):
            return None  # prints its version, help or type list and exits: no search
    operands = positional if no_pattern else positional[1:]
    if not operands and piped:
        return None
    return ('rg', 'whole-tree' if ignore_off else 'ignore-aware', operands)


def parse_fd(args, piped):
    ignore_off = False
    positional = []
    search_paths = []
    for kind, name, value in options(args, set('dteESocj'),
            {'max-depth', 'type', 'extension', 'exclude', 'size', 'owner', 'color', 'threads', 'changed-within',
             'changed-before', 'max-results', 'format', 'path-separator', 'ignore-file', 'exact-depth',
             'min-depth', 'search-path', 'base-directory'}):
        if kind == 'operand':
            positional.append(value)
        elif name in ('x', 'X', 'exec', 'exec-batch'):
            break  # the rest is the command fd runs
        elif name in ('I', 'u', 'unrestricted', 'no-ignore', 'no-ignore-vcs'):
            ignore_off = True
        elif name in ('d', 'max-depth') and depth_at_most(value):
            return None
        elif name == 'search-path' and value is not None:
            search_paths.append(value)
        elif name == 'base-directory':
            return None  # moves the search root: not read
    return ('fd', 'whole-tree' if ignore_off else 'ignore-aware', positional[1:] + search_paths)


def parse_ag(args, piped):
    ignore_off = False
    no_pattern = False
    positional = []
    for kind, name, value in options(args, set('ABCGgmpW'), {'ignore', 'ignore-dir', 'depth', 'pager', 'path-to-ignore',
                                                           'file-search-regex', 'max-count', 'context'}):
        if kind == 'operand':
            positional.append(value)
        elif name in ('u', 'U', 'a', 'unrestricted', 'skip-vcs-ignores', 'all-types'):
            ignore_off = True
        elif name in ('g', 'file-search-regex'):
            no_pattern = True
        elif name == 'depth' and depth_at_most(value):
            return None
    operands = positional if no_pattern else positional[1:]
    if not operands and piped:
        return None
    return ('ag', 'whole-tree' if ignore_off else 'ignore-aware', operands)


SEARCHES = {
    'grep': parse_grep, 'egrep': parse_grep, 'fgrep': parse_grep,
    'rgrep': lambda args, fed: parse_grep(args, fed, always_recursive=True),
    'find': parse_find, 'ls': parse_ls, 'tree': parse_tree, 'du': parse_du, 'ack': parse_ack,
    'rg': parse_rg, 'fd': parse_fd, 'fdfind': parse_fd, 'ag': parse_ag,
}


# ---------------------------------------------------------------- tools and the message

def glob_start(tool_input, cwd, env):
    pattern = tool_input.get('pattern')
    path = tool_input.get('path')
    if isinstance(pattern, str) and (pattern.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', pattern)):
        parts = normalize(pattern).split('/')
        literal = []
        for part in parts:
            if any(ch in part for ch in '*?[{'):
                break
            literal.append(part)
        return normalize('/'.join(literal) or '/')
    return tool_path(path, cwd, env)


def tool_path(path, cwd, env):
    """The absolute folder a tool path names (relative to `cwd`), or None when unknown. A leading `~` or
    `~/` is the home folder (Claude Code's tools and OpenCode's FileAccess.resolvePath both expand it)."""
    if path in (None, ''):
        return normalize(cwd) if isinstance(cwd, str) and cwd.startswith('/') else None
    if not isinstance(path, str):
        return None
    if path.startswith('~'):
        if not (path == '~' or path.startswith('~/')):
            return None
        path = env.home + path[1:]
    if not (path.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', path)):
        if not (isinstance(cwd, str) and cwd.startswith('/')):
            return None
        path = posixpath.join(cwd, path)
    return normalize(path)


def message(refusal, env):
    """One short answer the model sees: the refused piece, why, and what to search instead."""
    root = refusal.root
    name = posixpath.basename(root) or '/'
    if refusal.root_kind == 'repository':
        where = f'the root of the repository {name} ({root})'
    elif root == env.home:
        where = f'the home folder {root}'
    else:
        where = f'{root}, a folder above whole repositories'
    piece = refusal.piece if len(refusal.piece) <= PIECE_MAX else refusal.piece[:PIECE_MAX - 1] + '…'
    try:
        folders = sorted(entry for entry in env.listdir(root)
                         if entry not in NOT_LISTED and not entry.startswith('.'))
    except OSError:
        folders = []
    listed = ', '.join(folders[:LISTED_MAX]) + (', …' if len(folders) > LISTED_MAX else '')
    folders_line = f' ({name} holds: {listed})' if listed else ''
    # An ignore-aware Glob runs under the host's Glob settings: CLAUDE_CODE_GLOB_NO_IGNORE=false (launch.py) with
    # CLAUDE_CODE_GLOB_TIMEOUT_SECONDS=60 (config/claude-headless-settings.json).
    glob_cost = "which runs until Claude Code's 60 s Glob limit stops it"
    cost = glob_cost if refusal.tool == 'Glob' and refusal.walk == 'ignore-aware' else 'which hangs for minutes'
    if refusal.walk == 'ignore-aware':
        what = f'`{piece}` searches every repository under {where}'
        instead = f'Give it one repository or one of its subfolders{folders_line}'
    elif refusal.tool == 'Glob':
        # A generation whose settings predate CLAUDE_CODE_GLOB_NO_IGNORE=false: its Glob walks ignored folders.
        what = (f'Glob with path {root} walks every file under {where} whatever the pattern, and '
                'ignores .gitignore (tmp/, node_modules/, .git/)')
        instead = (f'Set `path` to the subfolder you mean{folders_line}'
                   + (f", or list files with `rg --files -g '<pattern>' {root}`, which honors .gitignore"
                      if refusal.root_kind == 'repository' else ''))
    else:
        what = f'`{piece}` walks the whole tree at {where} and ignores .gitignore (tmp/, node_modules/, .git/)'
        grep_tool = 'grep tool' if refusal.tool in OPENCODE_TOOLS else 'Grep tool'
        # The Grep tool may search one repository, never a folder above repositories: offered only for one.
        instead = (f'Name subfolders instead{folders_line}'
                   + (f', or use the {grep_tool}, which honors .gitignore and may search this repository'
                      if refusal.root_kind == 'repository' else ''))
    tail = ' Nothing in this command ran.' if refusal.tool in SHELL_TOOLS else ''
    by = "OpenCode's" if refusal.tool in OPENCODE_TOOLS else "the Claude host's"
    return f"Refused by {by} root-search guard: {what}, {cost}. {instead}.{tail}"


SHELL_TOOLS = ('Bash', 'shell')
OPENCODE_TOOLS = ('shell', 'grep', 'glob')
"""OpenCode's tools: `shell` (a fresh process in `workdir` or the session folder), and `grep`/`glob`,
which run ripgrep and honor .gitignore."""


def shell_start(tool_name, tool_input, cwd, shell_cwd, env):
    """The folder a shell command starts in, or None when unknown: Claude Code's Bash starts where its
    shell stands (`shell_cwd`, as Claude Code reports it); OpenCode's shell in `workdir`, else `cwd`."""
    if tool_name == 'Bash':
        return tool_path(None, shell_cwd, env)
    return tool_path(tool_input.get('workdir'), cwd, env)


# @beacon[
#   id=opc@root_search.judge,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The root-search guard: refuses a Bash/Grep/Glob (or OpenCode shell/grep/glob) call that walks a whole tree from a repository root or above, or searches a folder above repositories (configured roots, $HOME, a folder of repositories; docs/root-search-guard.md); Glob counts as ignore-aware only when the session's settings make it honour .gitignore; chained shell is split quote-aware and walked tracking cd from the known starting folder; any doubt allows.,
# ]
def judge(tool_name, tool_input, cwd, env=None, shell_cwd=None, glob_honours_gitignore=False):
    """A Refusal when this call is a root search (docs/root-search-guard.md), else None.

    ``cwd`` is the session's working directory: what a Grep/Glob call with no path searches, and
    where OpenCode's shell starts without a ``workdir``. ``shell_cwd`` is the folder a Claude Code
    Bash command starts in, when known (None: unknown, so a relative operand before an absolute
    ``cd`` is never judged). ``glob_honours_gitignore``: the session's settings make Claude Code's Glob
    honour .gitignore (CLAUDE_CODE_GLOB_NO_IGNORE=false); otherwise Glob walks ignored folders and is a
    whole-tree walk. Never raises: any doubt or error allows the call."""
    try:
        env = env or default_env()
        if not isinstance(tool_input, dict):
            return None
        refusal = None
        if tool_name in SHELL_TOOLS:
            command = tool_input.get('command')
            if isinstance(command, str):
                refusal = judge_shell(command, shell_start(tool_name, tool_input, cwd, shell_cwd, env), env)
                if refusal:
                    refusal.tool = tool_name
        elif tool_name in ('Glob', 'Grep', 'grep', 'glob'):
            # Ignore-aware (one repository root allowed, a folder above repositories not): Grep and OpenCode's grep and
            # glob (ripgrep) always, Claude Code's Glob when the session's settings make it honour .gitignore.
            ignore_aware = tool_name != 'Glob' or glob_honours_gitignore
            start = (glob_start(tool_input, cwd, env) if tool_name == 'Glob' else
                     tool_path(tool_input.get('path'), cwd, env))
            kind = env.root_kind(start) if start else None
            if kind and (not ignore_aware or kind != 'repository'):
                pattern = tool_input.get('pattern')
                piece = f'{tool_name} {json.dumps(pattern if isinstance(pattern, str) else "", ensure_ascii=False)} at {start}'
                refusal = Refusal(tool_name, piece, tool_name, 'ignore-aware' if ignore_aware else 'whole-tree',
                                  start, kind)
        if refusal:
            refusal.message = message(refusal, env)
        return refusal
    except Exception:
        return None


_ENV = None


# @beacon[
#   id=opc@root_search.configured_roots,
#   slice_labels=opc-claude-seats,
#   kind=ast,
#   comment=The root-search guard's one setting: extra root folders from ROOT_SEARCH_GUARD_ROOTS when set, else config/claude-headless-root-search.json, else none; read once per host process by default_env.,
# ]
def configured_roots(environ=os.environ, config=ROOTS_CONFIG):
    """The setting's extra roots: the environment variable when set, else the config file, else none. An
    unreadable or malformed file gives none (fewer roots only ever means fewer refusals)."""
    if ROOTS_ENV in environ:
        return [root for root in environ[ROOTS_ENV].split(os.pathsep) if root]
    try:
        roots = json.loads(Path(config).read_text(encoding='utf-8')).get('extra_roots')
    except (OSError, ValueError, AttributeError):
        return []
    return [root for root in roots if isinstance(root, str)] if isinstance(roots, list) else []


def default_env():
    global _ENV
    if _ENV is None:
        _ENV = Env(extra_roots=configured_roots())
    return _ENV
