/* Display only. Stored prose, Refine context and inspector snapshots stay exact. */
(() => {
  'use strict';
  const escape = value => value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let parser;
  const tags = ['p','br','h1','h2','h3','h4','h5','h6','strong','em','del','ul','ol','li',
    'blockquote','pre','code','hr','table','thead','tbody','tr','th','td','a'];

  // Game boards: a fenced block whose language names a renderer below is drawn as
  // a static board, built after sanitization from DOM nodes and text only (never
  // markup). A renderer returns null for a block it cannot read, which then stays
  // the ordinary code block. Another game is one more entry (e.g. sgf for Go).
  const boards = {fen: chessBoard};
  const glyphs = {k:'♚', q:'♛', r:'♜', b:'♝', n:'♞', p:'♟'};
  const boardCss = `
.opc-chess{margin:.5em 0;width:min(100%,22rem)}
.opc-chess-grid{display:grid;grid-template:repeat(8,minmax(0,1fr))/repeat(8,minmax(0,1fr));aspect-ratio:1;border:1px solid #6b5641;border-radius:3px;overflow:hidden;container-type:inline-size;-webkit-user-select:none;user-select:none}
.opc-chess-grid>span{position:relative;display:flex;align-items:center;justify-content:center;min-width:0;min-height:0;overflow:hidden;font:28px/1 "Segoe UI Symbol","Apple Symbols","DejaVu Sans",sans-serif;font-size:10cqw}
.opc-chess .lt{background:#f0d9b5}.opc-chess .dk{background:#b58863}
.opc-chess .lt.last{background:#cdd26a}.opc-chess .dk.last{background:#aaa23a}
.opc-chess .pw{color:#fff;text-shadow:1px 0 #000,-1px 0 #000,0 1px #000,0 -1px #000}
.opc-chess .pb{color:#111;text-shadow:0 0 1px rgba(255,255,255,.5)}
.opc-chess i{position:absolute;font:600 9px/1 system-ui,sans-serif;font-size:2.5cqw;font-style:normal;text-shadow:none}
.opc-chess i.rk{top:2px;left:2px}.opc-chess i.fl{right:2px;bottom:2px}
.opc-chess .lt i{color:#b58863}.opc-chess .dk i{color:#f0d9b5}
.opc-chess figcaption{margin-top:4px;font-size:.85em;opacity:.75}`;
  const styled = new WeakSet();

  // @beacon[
  //   id=opc-pane@chessBoard,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=A ```fen block (one FEN line, optional second line the last move as e7e5) drawn as a static 8x8 board with Unicode pieces, White at the bottom; null for anything else.,
  // ]
  function chessBoard(text, doc) {
    const lines = text.trim().split(/\s*\n\s*/);
    const [placement, side] = lines[0].split(/\s+/), ranks = placement.split('/');
    const last = lines[1] && /^([a-h][1-8])([a-h][1-8])[qrbn]?$/i.exec(lines[1]);
    if (lines.length > 2 || ranks.length !== 8 || (lines[1] && !last) || (side && !/^[wb]$/.test(side))) return null;
    const cells = [];
    for (const rank of ranks) {
      const row = [];
      for (const c of rank) {
        if (/[1-8]/.test(c)) row.push(...Array(+c).fill(''));
        else if (/[kqrbnp]/i.test(c)) row.push(c);
        else return null;
      }
      if (row.length !== 8) return null;
      cells.push(...row);
    }
    const lit = last ? [last[1].toLowerCase(), last[2].toLowerCase()] : [];
    const make = (tag, cls, content) => {
      const el = doc.createElement(tag);
      if (cls) el.className = cls;
      if (content) el.textContent = content;
      return el;
    };
    const figure = make('figure', 'opc-board opc-chess'), grid = make('div', 'opc-chess-grid');
    const toMove = side === 'w' ? 'White to move' : side === 'b' ? 'Black to move' : '';
    figure.setAttribute('role', 'img');
    figure.setAttribute('aria-label', `Chess position${toMove ? ', ' + toMove : ''}: ${lines[0]}`);
    figure.title = lines[0];
    cells.forEach((piece, i) => {
      const file = i % 8, row = Math.floor(i / 8), square = 'abcdefgh'[file] + (8 - row);
      const cell = make('span', ((file + row) % 2 ? 'dk' : 'lt') + (lit.includes(square) ? ' last' : ''));
      // U+FE0E keeps iOS from drawing the pawn as an emoji.
      if (piece) cell.append(make('b', piece === piece.toUpperCase() ? 'pw' : 'pb', glyphs[piece.toLowerCase()] + '︎'));
      if (file === 0) cell.append(make('i', 'rk', String(8 - row)));
      if (row === 7) cell.append(make('i', 'fl', 'abcdefgh'[file]));
      grid.append(cell);
    });
    figure.append(grid);
    const caption = [toMove, last ? `last move ${lit[0]}–${lit[1]}` : ''].filter(Boolean).join(' · ');
    if (caption) figure.append(make('figcaption', '', caption));
    if (!styled.has(doc)) {
      (doc.head || doc.documentElement).append(make('style', '', boardCss));
      styled.add(doc);
    }
    return figure;
  }

  // @beacon[
  //   id=opc-pane@drawBoards,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Swaps each sanitized fenced block whose language is a key of the boards map (fen today; sgf for Go would be one more renderer) for its board; unreadable blocks stay code.,
  // ]
  function drawBoards(fragment, doc) {
    for (const code of fragment.querySelectorAll('pre > code[class^="language-"]')) {
      const draw = Object.hasOwn(boards, code.className.slice(9)) && boards[code.className.slice(9)];
      const board = draw && draw(code.textContent, doc);
      if (board) code.parentNode.replaceWith(board);
    }
  }
  // @beacon[
  //   id=opc-pane@renderMessageMarkdown,
  //   slice_labels=opc-pane,
  //   kind=ast,
  //   comment=Final user and assistant prose only: local GFM, escaped HTML, allow-list sanitization and safe links, then game-board fences (```fen) drawn as boards; unchanged bodies and streaming text bypass parsing.,
  // ]
  function render(node, source, markdown) {
    const available = globalThis.marked?.Marked && globalThis.DOMPurify?.isSupported;
    const mode = markdown && available ? 'markdown' : 'text';
    if (node._messageSource === source && node.dataset.rendering === mode) return;
    if (mode === 'markdown') {
      try {
        parser ||= new marked.Marked({gfm:true, breaks:false, async:false, renderer:{
          // Never let source HTML become markup, even before sanitization.
          html: token => escape(token.text),
          // No remote image loading/tracking from a conversation.
          image: token => escape(token.text || '')
        }});
        const fragment = DOMPurify.sanitize(parser.parse(source), {
          ALLOWED_TAGS:tags, ALLOWED_ATTR:['href','title','start','align','class'],
          ALLOW_DATA_ATTR:false, ALLOW_ARIA_ATTR:false, RETURN_DOM_FRAGMENT:true
        });
        for (const link of fragment.querySelectorAll('a')) {
          const href = link.getAttribute('href');
          let safe = false;
          try {
            const url = new URL(href, node.ownerDocument.baseURI);
            safe = href !== null && ['http:','https:','mailto:'].includes(url.protocol);
          } catch (_) { /* malformed destinations become ordinary text */ }
          if (safe) { link.setAttribute('target','_blank'); link.setAttribute('rel','noopener noreferrer'); }
          else link.removeAttribute('href');
        }
        drawBoards(fragment, node.ownerDocument);
        node.replaceChildren(fragment);
      } catch (_) {
        // A library/load failure cannot lose a message or insert unsanitized HTML.
        node.textContent = source;
        node.dataset.rendering = 'text';
        node.title = 'Markdown unavailable; complete source text shown.';
        node._messageSource = source;
        return;
      }
    } else node.textContent = source;
    node.dataset.rendering = mode;
    node.title = markdown && !available ? 'Markdown unavailable; complete source text shown.' : '';
    node._messageSource = source;
  }
  globalThis.opcMessageMarkdown = Object.freeze({render});
})();
