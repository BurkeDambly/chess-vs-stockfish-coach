#!/usr/bin/env python3
"""
Chess vs Stockfish — drag-and-drop board, optional Elo limit, and move analysis.

Loads crisp PNG piece art from ``pieces/wikipedia`` (downloaded automatically from the
public Chess.com “neo” theme CDN on first run). Falls back to drawn sprites if that fails.

After each of your moves, Stockfish scores every reply line (multi-PV) and labels the move
with familiar coaching terms (Book / Brilliant / Best / …). These labels are **heuristic**
and respect your current engine strength settings.

Requires ``python-chess``, ``pillow``, and a Stockfish binary (PATH / STOCKFISH_PATH / bundled).
"""

from __future__ import annotations

import os
import shutil
import threading
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

import chess
import chess.engine

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError as exc:
    raise SystemExit(
        "Missing Pillow. Install with: python -m pip install pillow"
    ) from exc


PROJECT_DIR = Path(__file__).resolve().parent
PIECES_DIR = PROJECT_DIR / "pieces" / "wikipedia"
PIECE_CDN = "https://images.chesscomfiles.com/chess-themes/pieces/neo/256/"

# Reasonable presets; Stockfish clamps UCI_Elo to engine-supported range on configure.
ELO_PRESETS = [800, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2400, 2600]


def ensure_piece_png_assets() -> None:
    """Populate PIECES_DIR with neo PNGs if missing (best-effort)."""
    PIECES_DIR.mkdir(parents=True, exist_ok=True)
    stems = ["wp", "wn", "wb", "wr", "wq", "wk", "bp", "bn", "bb", "br", "bq", "bk"]
    for stem in stems:
        dest = PIECES_DIR / f"{stem}.png"
        if dest.is_file() and dest.stat().st_size > 200:
            continue
        url = f"{PIECE_CDN}{stem}.png"
        try:
            with urllib.request.urlopen(url, timeout=12) as resp:
                data = resp.read()
            dest.write_bytes(data)
        except Exception:
            continue


def piece_png_path(piece: chess.Piece) -> Path:
    stem = ("w" if piece.color == chess.WHITE else "b") + piece.symbol().lower()
    return PIECES_DIR / f"{stem}.png"


def _rel_numeric(rel: chess.engine.Score) -> float:
    if rel.is_mate():
        m = rel.mate()
        assert m is not None
        return float(m) * 1e9
    cp = rel.score()
    return float(cp) if cp is not None else 0.0


def _eval_move_flip(engine: chess.engine.SimpleEngine, board_before: chess.Board, move: chess.Move) -> float:
    """Rough centipawn-equivalent from the mover's POV when multipv misses a move."""
    b = board_before.copy()
    b.push(move)
    info = engine.analyse(b, chess.engine.Limit(depth=11))
    rel = info["score"].relative
    return -_rel_numeric(rel)


def classify_human_move(
    engine: chess.engine.SimpleEngine, board_before: chess.Board, move: chess.Move
) -> dict[str, object]:
    legal = list(board_before.legal_moves)
    if move not in legal:
        return {
            "segments": [("Illegal move\n", "blunder")],
            "meta": "That move was not legal for this position.",
        }

    multipv = min(len(legal), 40)
    infos = engine.analyse(board_before, chess.engine.Limit(depth=14), multipv=multipv)
    if isinstance(infos, dict):
        infos = [infos]

    scores: dict[chess.Move, float] = {}
    for info in infos:
        pv = info.get("pv") or []
        if not pv:
            continue
        m0 = pv[0]
        scores[m0] = _rel_numeric(info["score"].relative)

    if len(scores) < len(legal):
        for mv in legal:
            if mv not in scores:
                scores[mv] = _eval_move_flip(engine, board_before, mv)

    if move not in scores:
        scores[move] = _eval_move_flip(engine, board_before, move)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_move = ranked[0][0]
    best_cp = ranked[0][1]
    played_cp = scores[move]
    cp_loss = max(0.0, best_cp - played_cp)

    rank_played = next(i + 1 for i, (m, _) in enumerate(ranked) if m == move)
    second_cp = ranked[1][1] if len(ranked) > 1 else ranked[0][1] - 999.0
    second_gap = best_cp - second_cp

    trial = board_before.copy()
    trial.push(move)
    delivered_mate = trial.is_checkmate()
    is_best = rank_played == 1

    brilliant = delivered_mate or (
        is_best and (second_gap > 160.0 or (board_before.is_capture(move) and second_gap > 90.0))
    )

    opening = board_before.fullmove_number <= 9 and board_before.ply() <= 18
    bookish = opening and rank_played <= 4 and cp_loss < 55.0

    main_tag = "good"
    main_label = "Good"

    if brilliant:
        main_tag, main_label = "brilliant", "Brilliant"
    elif bookish and is_best:
        main_tag, main_label = "book", "Book · Best"
    elif bookish and cp_loss < 18:
        main_tag, main_label = "book", "Book · Excellent"
    elif bookish and cp_loss < 45:
        main_tag, main_label = "book", "Book · Good"
    elif is_best:
        main_tag, main_label = "best", "Best move"
    elif cp_loss < 12:
        main_tag, main_label = "excellent", "Excellent"
    elif cp_loss < 30:
        main_tag, main_label = "good", "Good"
    elif cp_loss < 75:
        main_tag, main_label = "acceptable", "Acceptable"
    elif cp_loss < 160:
        main_tag, main_label = "inaccuracy", "Inaccuracy"
    elif cp_loss < 320:
        main_tag, main_label = "mistake", "Mistake"
    else:
        main_tag, main_label = "blunder", "Blunder"

    san_played = board_before.san(move)
    san_best = board_before.san(best_move)

    segments: list[tuple[str, str]] = [(main_label + "\n", main_tag)]

    meta = (
        f"Your move: {san_played}\n"
        f"Engine top choice: {san_best}\n"
        f"Rank (among legal moves in this search): {rank_played} / {len(legal)}\n"
        f"Estimated gap vs engine top line: {cp_loss:.0f} cp\n"
    )

    return {"segments": segments, "meta": meta}


def _piece_rgba(white: bool) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    if white:
        return ((248, 248, 246, 255), (34, 34, 34, 255))
    return ((48, 48, 48, 255), (210, 210, 208, 255))


def _ib(x0: float, y0: float, x1: float, y1: float) -> list[int]:
    return [int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))]


def _render_pawn(draw: ImageDraw.ImageDraw, cx: float, s: float, fill, outline, lw: int) -> None:
    draw.ellipse(_ib(cx - 0.13 * s, 0.14 * s, cx + 0.13 * s, 0.30 * s), fill=fill, outline=outline, width=lw)
    draw.ellipse(_ib(cx - 0.17 * s, 0.30 * s, cx + 0.17 * s, 0.52 * s), fill=fill, outline=outline, width=lw)
    draw.ellipse(_ib(cx - 0.21 * s, 0.50 * s, cx + 0.21 * s, 0.80 * s), fill=fill, outline=outline, width=lw)


def _render_rook(draw: ImageDraw.ImageDraw, cx: float, s: float, fill, outline, lw: int) -> None:
    r = max(2, int(s * 0.05))
    draw.rounded_rectangle(_ib(cx - 0.22 * s, 0.40 * s, cx + 0.22 * s, 0.80 * s), radius=r, fill=fill, outline=outline, width=lw)
    bw = 0.085 * s
    for off in (-0.15 * s, 0.0, 0.15 * s):
        ix = cx + off
        draw.rectangle(_ib(ix - bw / 2, 0.28 * s, ix + bw / 2, 0.44 * s), fill=fill, outline=outline, width=lw)


def _render_knight(draw: ImageDraw.ImageDraw, cx: float, s: float, fill, outline, lw: int) -> None:
    pts = [
        (cx - 0.05 * s, 0.78 * s),
        (cx - 0.24 * s, 0.66 * s),
        (cx - 0.20 * s, 0.44 * s),
        (cx - 0.02 * s, 0.26 * s),
        (cx + 0.20 * s, 0.20 * s),
        (cx + 0.16 * s, 0.36 * s),
        (cx + 0.06 * s, 0.44 * s),
        (cx + 0.12 * s, 0.62 * s),
        (cx + 0.02 * s, 0.78 * s),
    ]
    draw.polygon([(int(round(x)), int(round(y))) for x, y in pts], fill=fill, outline=outline, width=lw)


def _render_bishop(draw: ImageDraw.ImageDraw, cx: float, s: float, fill, outline, lw: int) -> None:
    draw.polygon(
        [
            (int(round(cx)), int(round(0.18 * s))),
            (int(round(cx + 0.07 * s)), int(round(0.30 * s))),
            (int(round(cx + 0.18 * s)), int(round(0.74 * s))),
            (int(round(cx - 0.18 * s)), int(round(0.74 * s))),
            (int(round(cx - 0.07 * s)), int(round(0.30 * s))),
        ],
        fill=fill,
        outline=outline,
        width=lw,
    )
    draw.line(_ib(cx, 0.12 * s, cx, 0.22 * s), fill=outline, width=max(lw + 1, 2))


def _render_queen(draw: ImageDraw.ImageDraw, cx: float, s: float, fill, outline, lw: int) -> None:
    top = 0.22 * s
    draw.polygon(
        [
            (int(round(cx - 0.20 * s)), int(round(0.44 * s))),
            (int(round(cx - 0.24 * s)), int(round(top))),
            (int(round(cx - 0.14 * s)), int(round(0.30 * s))),
            (int(round(cx - 0.06 * s)), int(round(top))),
            (int(round(cx)), int(round(0.26 * s))),
            (int(round(cx + 0.06 * s)), int(round(top))),
            (int(round(cx + 0.14 * s)), int(round(0.30 * s))),
            (int(round(cx + 0.24 * s)), int(round(top))),
            (int(round(cx + 0.20 * s)), int(round(0.44 * s))),
            (int(round(cx + 0.18 * s)), int(round(0.76 * s))),
            (int(round(cx - 0.18 * s)), int(round(0.76 * s))),
        ],
        fill=fill,
        outline=outline,
        width=lw,
    )


def _render_king(draw: ImageDraw.ImageDraw, cx: float, s: float, fill, outline, lw: int) -> None:
    r = max(2, int(s * 0.045))
    draw.rounded_rectangle(_ib(cx - 0.18 * s, 0.40 * s, cx + 0.18 * s, 0.78 * s), radius=r, fill=fill, outline=outline, width=lw)
    draw.rectangle(_ib(cx - 0.035 * s, 0.12 * s, cx + 0.035 * s, 0.34 * s), fill=outline, outline=outline)
    draw.rectangle(_ib(cx - 0.10 * s, 0.17 * s, cx + 0.10 * s, 0.24 * s), fill=outline, outline=outline)


def render_piece_rgba(size: int, piece_type: int, white: bool) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill, outline = _piece_rgba(white)
    lw = max(1, size // 36)
    s = float(size)
    cx = s / 2

    if piece_type == chess.PAWN:
        _render_pawn(draw, cx, s, fill, outline, lw)
    elif piece_type == chess.ROOK:
        _render_rook(draw, cx, s, fill, outline, lw)
    elif piece_type == chess.KNIGHT:
        _render_knight(draw, cx, s, fill, outline, lw)
    elif piece_type == chess.BISHOP:
        _render_bishop(draw, cx, s, fill, outline, lw)
    elif piece_type == chess.QUEEN:
        _render_queen(draw, cx, s, fill, outline, lw)
    elif piece_type == chess.KING:
        _render_king(draw, cx, s, fill, outline, lw)

    return img


def find_stockfish_path() -> str | None:
    env = os.environ.get("STOCKFISH_PATH", "").strip().strip('"')
    if env and os.path.isfile(env):
        return env

    bundled = (
        PROJECT_DIR
        / "stockfish-windows-x86-64-avx2"
        / "stockfish"
        / "stockfish-windows-x86-64-avx2.exe"
    )
    if bundled.is_file():
        return str(bundled)

    for cmd in ("stockfish", "stockfish.exe"):
        found = shutil.which(cmd)
        if found:
            return found
    return None


class ChessVsStockfishApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Chess vs Stockfish")
        root.minsize(980, 660)

        ensure_piece_png_assets()

        self.board = chess.Board()
        self.engine_path = find_stockfish_path()
        self.engine: chess.engine.SimpleEngine | None = None
        self._engine_lock = threading.Lock()

        self.human_color = chess.WHITE
        self.engine_thinking = False

        self.square_size = 72
        self.board_pad = 8

        self.drag_from_sq: int | None = None
        self.drag_piece: chess.Piece | None = None
        self._legal_moves_map: dict[int, list[chess.Move]] = {}
        self.drag_ghost_item: int | None = None
        self.drag_ghost_photo: ImageTk.PhotoImage | None = None
        self._piece_photo_cache: dict[tuple[bool, int, int], ImageTk.PhotoImage] = {}
        self._last_pointer_xy = (0, 0)
        self.exploration_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._try_connect_engine()

        if self.board.turn == chess.BLACK and not self.engine_thinking:
            self._schedule_engine_move()

    def _photo_cache_key(self, piece: chess.Piece) -> tuple[bool, int, int]:
        return (piece.color == chess.WHITE, piece.piece_type, self.square_size)

    def _get_piece_photo(self, piece: chess.Piece) -> ImageTk.PhotoImage:
        key = self._photo_cache_key(piece)
        if key not in self._piece_photo_cache:
            path = piece_png_path(piece)
            if path.is_file():
                pil = Image.open(path).convert("RGBA")
                pil = pil.resize((self.square_size, self.square_size), Image.Resampling.LANCZOS)
            else:
                pil = render_piece_rgba(self.square_size, piece.piece_type, piece.color == chess.WHITE)
            self._piece_photo_cache[key] = ImageTk.PhotoImage(pil)
        return self._piece_photo_cache[key]

    def _configure_analysis_tags(self) -> None:
        self.analysis_text.configure(font=("Consolas", 10))
        self.analysis_text.tag_configure("brilliant", foreground="#0d47a1", font=("Segoe UI", 16, "bold"))
        self.analysis_text.tag_configure("book", foreground="#5d4037", font=("Segoe UI", 15, "bold"))
        self.analysis_text.tag_configure("best", foreground="#2e7d32", font=("Segoe UI", 15, "bold"))
        self.analysis_text.tag_configure("excellent", foreground="#388e3c", font=("Segoe UI", 14, "bold"))
        self.analysis_text.tag_configure("good", foreground="#558b2f", font=("Segoe UI", 14, "bold"))
        self.analysis_text.tag_configure("acceptable", foreground="#6d4c41", font=("Segoe UI", 13, "bold"))
        self.analysis_text.tag_configure("inaccuracy", foreground="#f57f17", font=("Segoe UI", 13, "bold"))
        self.analysis_text.tag_configure("mistake", foreground="#ef6c00", font=("Segoe UI", 13, "bold"))
        self.analysis_text.tag_configure("blunder", foreground="#c62828", font=("Segoe UI", 14, "bold"))
        self.analysis_text.tag_configure("meta", foreground="#424242", font=("Consolas", 10))
        self.analysis_text.tag_configure("legend", foreground="#616161", font=("Segoe UI", 9))

    def _analysis_set_loading(self) -> None:
        self.analysis_text.configure(state=tk.NORMAL)
        self.analysis_text.delete("1.0", tk.END)
        self.analysis_text.insert(tk.END, "Analyzing your move…\n", "meta")
        self.analysis_text.configure(state=tk.DISABLED)

    def _analysis_clear_placeholder(self) -> None:
        self.analysis_text.configure(state=tk.NORMAL)
        self.analysis_text.delete("1.0", tk.END)
        legend = (
            "After each White move in normal mode, Stockfish runs a multi-PV search and "
            "labels your move (Book, Brilliant, Best, Good, Acceptable, Inaccuracy, "
            "Mistake, Blunder).\n\n"
            "Grey dots while dragging mark quiet destinations; rings mark captures.\n"
            "Use Undo to step back. Enable “Manual opponent” to drag Black yourself "
            "(what-if lines); turn it off and Black’s turn will ask Stockfish again.\n"
        )
        self.analysis_text.insert(tk.END, legend, "legend")
        self.analysis_text.configure(state=tk.DISABLED)

    def _analysis_show_result(self, payload: dict[str, object]) -> None:
        segments = payload.get("segments") or []
        meta = str(payload.get("meta") or "")
        self.analysis_text.configure(state=tk.NORMAL)
        self.analysis_text.delete("1.0", tk.END)
        for text, tag in segments:
            self.analysis_text.insert(tk.END, text, tag if tag else ())
        self.analysis_text.insert(tk.END, "\n")
        self.analysis_text.insert(tk.END, meta + "\n", "meta")
        self.analysis_text.configure(state=tk.DISABLED)

    def _analysis_show_error(self, err: Exception) -> None:
        self.analysis_text.configure(state=tk.NORMAL)
        self.analysis_text.delete("1.0", tk.END)
        self.analysis_text.insert(tk.END, "Analysis unavailable\n", "mistake")
        self.analysis_text.insert(tk.END, str(err) + "\n", "meta")
        self.analysis_text.configure(state=tk.DISABLED)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Bot Elo (approx.):").pack(side=tk.LEFT)
        self.elo_var = tk.StringVar(value=str(ELO_PRESETS[4]))
        self.elo_combo = ttk.Combobox(
            top,
            textvariable=self.elo_var,
            values=[str(e) for e in ELO_PRESETS],
            width=8,
            state="readonly",
        )
        self.elo_combo.pack(side=tk.LEFT, padx=(4, 12))
        self.elo_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_engine_options())

        self.status_var = tk.StringVar(value="Drag your pieces — White to move.")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.LEFT)

        btn_row = ttk.Frame(self.root, padding=(8, 0))
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="New game", command=self.new_game).pack(side=tk.LEFT, padx=(0, 8))
        self.undo_btn = ttk.Button(btn_row, text="Undo", command=self.undo_move)
        self.undo_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="Flip board", command=self.flip_board).pack(side=tk.LEFT, padx=(0, 12))
        self.explore_chk = ttk.Checkbutton(
            btn_row,
            text="Manual opponent (drag Black / what-if)",
            variable=self.exploration_var,
            command=self._on_exploration_toggle,
        )
        self.explore_chk.pack(side=tk.LEFT)

        body = ttk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        board_wrap = ttk.Frame(body)
        board_wrap.pack(side=tk.LEFT, padx=(0, 10))

        canvas_size = self.square_size * 8 + self.board_pad * 2
        self.canvas = tk.Canvas(
            board_wrap,
            width=canvas_size,
            height=canvas_size,
            highlightthickness=1,
            highlightbackground="#888",
            bg="#6d4c36",
        )
        self.canvas.pack()

        self.canvas.bind("<Button-1>", self._on_board_press)
        self.canvas.bind("<B1-Motion>", self._on_board_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_board_release)

        analysis_frame = ttk.LabelFrame(body, text="Move analysis")
        analysis_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.analysis_text = tk.Text(
            analysis_frame,
            width=42,
            height=26,
            wrap=tk.WORD,
            state=tk.DISABLED,
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        ay_scroll = ttk.Scrollbar(analysis_frame, orient=tk.VERTICAL, command=self.analysis_text.yview)
        self.analysis_text.configure(yscrollcommand=ay_scroll.set)
        self.analysis_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ay_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._configure_analysis_tags()
        self._analysis_clear_placeholder()

        self.flip_board_view = False
        self._draw_board()

        foot = ttk.Frame(self.root, padding=8)
        foot.pack(fill=tk.X)
        path_txt = self.engine_path or "(not found — set STOCKFISH_PATH or install Stockfish)"
        ttk.Label(foot, text=f"Engine: {path_txt}", font=("Segoe UI", 8), foreground="#444").pack(
            anchor=tk.W
        )

    def _square_from_canvas(self, x: float, y: float) -> int | None:
        pad = self.board_pad
        if x < pad or y < pad:
            return None
        col = int((x - pad) // self.square_size)
        row = int((y - pad) // self.square_size)
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        if self.flip_board_view:
            col = 7 - col
            row = 7 - row
        file_idx = col
        rank_idx = 7 - row
        return chess.square(file_idx, rank_idx)

    def _square_bbox_canvas(self, sq: int) -> tuple[float, float, float, float]:
        pad = self.board_pad
        file_idx = chess.square_file(sq)
        rank_idx = chess.square_rank(sq)
        display_col = file_idx
        display_row = 7 - rank_idx
        if self.flip_board_view:
            display_col = 7 - display_col
            display_row = 7 - display_row
        x0 = pad + display_col * self.square_size
        y0 = pad + display_row * self.square_size
        x1 = x0 + self.square_size
        y1 = y0 + self.square_size
        return (x0, y0, x1, y1)

    def _legal_moves_from(self, sq: int) -> dict[int, list[chess.Move]]:
        out: dict[int, list[chess.Move]] = {}
        for m in self.board.legal_moves:
            if m.from_square == sq:
                out.setdefault(m.to_square, []).append(m)
        return out

    def _try_connect_engine(self) -> None:
        if not self.engine_path:
            messagebox.showerror(
                "Stockfish not found",
                "Install Stockfish and ensure it is on PATH, or set the "
                "STOCKFISH_PATH environment variable to the executable.",
            )
            return
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            self._apply_engine_options()
        except Exception as exc:
            messagebox.showerror("Engine error", f"Could not start Stockfish:\n{exc}")

    def _apply_engine_options(self, *_args) -> None:
        if not self.engine:
            return
        try:
            elo = int(self.elo_var.get())
        except ValueError:
            elo = 1600
        try:
            self.engine.configure(
                {
                    "UCI_LimitStrength": True,
                    "UCI_Elo": elo,
                }
            )
        except chess.engine.EngineError:
            skill = max(0, min(20, (elo - 600) // 100))
            try:
                self.engine.configure({"Skill Level": skill})
            except chess.engine.EngineError:
                pass

    def flip_board(self) -> None:
        if self.drag_piece is not None:
            self._abort_drag()
        self.flip_board_view = not self.flip_board_view
        self._draw_board()

    def new_game(self) -> None:
        if self.drag_piece is not None:
            self._abort_drag()
        self.board.reset()
        self.engine_thinking = False
        self.status_var.set("Drag your pieces — White to move." if self.human_color == chess.WHITE else "Thinking…")
        self.exploration_var.set(False)
        self._analysis_clear_placeholder()
        self._draw_board()
        if self.board.turn != self.human_color:
            self._schedule_engine_move()

    def _abort_drag(self) -> None:
        if self.drag_ghost_item is not None:
            self.canvas.delete(self.drag_ghost_item)
            self.drag_ghost_item = None
        self.drag_ghost_photo = None
        self.drag_from_sq = None
        self.drag_piece = None
        self._legal_moves_map = {}

    def _draw_board(self) -> None:
        self.canvas.delete("all")
        self.drag_ghost_item = None
        pad = self.board_pad
        light = "#f0d9b5"
        dark = "#b58863"
        highlight_move = "#cdd26a"
        highlight_from = "#f6f669"

        last = self.board.peek() if self.board.move_stack else None

        dragging = self.drag_from_sq is not None

        for sq in chess.SQUARES:
            file_idx = chess.square_file(sq)
            rank_idx = chess.square_rank(sq)
            display_col = file_idx
            display_row = 7 - rank_idx
            if self.flip_board_view:
                display_col = 7 - display_col
                display_row = 7 - display_row

            x0 = pad + display_col * self.square_size
            y0 = pad + display_row * self.square_size
            x1 = x0 + self.square_size
            y1 = y0 + self.square_size

            is_light = (file_idx + rank_idx) % 2 == 0
            fill = light if is_light else dark
            if dragging and sq == self.drag_from_sq:
                fill = highlight_from
            self.canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="", tags=("square",))

            if dragging and sq in self._legal_moves_map:
                cx = (x0 + x1) / 2
                cy = (y0 + y1) / 2
                moves_here = self._legal_moves_map[sq]
                capture = any(self.board.is_capture(m) for m in moves_here)
                hint_fill = "#adadad"
                hint_stipple = "gray50"
                if capture:
                    self.canvas.create_oval(
                        x0 + 5,
                        y0 + 5,
                        x1 - 5,
                        y1 - 5,
                        outline="#9a9a9a",
                        width=4,
                        tags=("hint",),
                    )
                else:
                    r = max(5, self.square_size // 8)
                    self.canvas.create_oval(
                        cx - r,
                        cy - r,
                        cx + r,
                        cy + r,
                        fill=hint_fill,
                        outline="",
                        stipple=hint_stipple,
                        tags=("hint",),
                    )

        if last:
            for sq in (last.from_square, last.to_square):
                x0, y0, x1, y1 = self._square_bbox_canvas(sq)
                self.canvas.create_rectangle(x0, y0, x1, y1, outline=highlight_move, width=3)

        for sq in chess.SQUARES:
            piece = self.board.piece_at(sq)
            if piece is None:
                continue
            if dragging and sq == self.drag_from_sq:
                continue
            photo = self._get_piece_photo(piece)
            x0, y0, x1, y1 = self._square_bbox_canvas(sq)
            self.canvas.create_image((x0 + x1) / 2, (y0 + y1) / 2, image=photo, anchor=tk.CENTER, tags=("piece",))

        labels = "abcdefgh"
        for i in range(8):
            ci = 7 - i if self.flip_board_view else i
            self.canvas.create_text(
                pad + (i + 0.85) * self.square_size,
                pad + 8 * self.square_size - 6,
                text=labels[ci],
                font=("Segoe UI", 9),
                fill="#f5f5f5",
            )
            ri = i if self.flip_board_view else 7 - i
            self.canvas.create_text(
                pad + 6,
                pad + (i + 0.15) * self.square_size,
                text=str(ri + 1),
                font=("Segoe UI", 9),
                fill="#f5f5f5",
                anchor=tk.W,
            )

        self._sync_toolbar_state()

    def _sync_toolbar_state(self) -> None:
        busy = self.engine_thinking
        can_undo = (not busy) and len(self.board.move_stack) > 0
        self.undo_btn.configure(state=tk.NORMAL if can_undo else tk.DISABLED)
        self.explore_chk.configure(state=tk.NORMAL if not busy else tk.DISABLED)

    def _refresh_status_exploration(self) -> None:
        if self.board.is_game_over():
            self.status_var.set("Exploration — game over.")
            return
        side = "White" if self.board.turn == chess.WHITE else "Black"
        self.status_var.set(
            f"Exploration — {side} to move. Drag the side whose turn it is (try alternative Black replies)."
        )

    def _on_exploration_toggle(self) -> None:
        self._abort_drag()
        self._draw_board()
        if self.exploration_var.get():
            self._analysis_clear_placeholder()
            self._refresh_status_exploration()
            return
        if self.board.is_game_over():
            return
        if self.board.turn != self.human_color:
            self._schedule_engine_move()
        else:
            self.status_var.set(
                "Your turn — drag a piece." + (" (Check!)" if self.board.is_check() else "")
            )

    def undo_move(self) -> None:
        if self.engine_thinking or len(self.board.move_stack) == 0:
            return
        self._abort_drag()
        self.board.pop()
        self._draw_board()
        self._analysis_clear_placeholder()
        if self.exploration_var.get():
            self._refresh_status_exploration()
        elif self.board.is_game_over():
            pass
        elif self.board.turn != self.human_color:
            self._schedule_engine_move()
        else:
            self.status_var.set(
                "Your turn — drag a piece." + (" (Check!)" if self.board.is_check() else "")
            )

    def _on_board_press(self, event: tk.Event) -> None:
        self._last_pointer_xy = (event.x, event.y)
        if self.engine_thinking or self.board.is_game_over():
            return

        sq = self._square_from_canvas(event.x, event.y)
        if sq is None:
            return

        piece = self.board.piece_at(sq)
        if piece is None:
            return

        if self.exploration_var.get():
            if piece.color != self.board.turn:
                return
        else:
            if self.board.turn != self.human_color:
                return
            if piece.color != self.human_color:
                return

        self._abort_drag()
        self.drag_from_sq = sq
        self.drag_piece = piece
        self._legal_moves_map = self._legal_moves_from(sq)

        photo = self._get_piece_photo(piece)
        self.drag_ghost_photo = photo
        self._draw_board()
        self.drag_ghost_item = self.canvas.create_image(
            event.x, event.y, image=self.drag_ghost_photo, anchor=tk.CENTER, tags=("ghost",)
        )
        self.canvas.lift(self.drag_ghost_item)

    def _on_board_drag(self, event: tk.Event) -> None:
        self._last_pointer_xy = (event.x, event.y)
        if self.drag_ghost_item is None:
            return
        self.canvas.coords(self.drag_ghost_item, event.x, event.y)

    def _on_board_release(self, event: tk.Event) -> None:
        self._last_pointer_xy = (event.x, event.y)
        if self.drag_piece is None or self.drag_from_sq is None:
            return

        sq = self._square_from_canvas(event.x, event.y)
        moves_map = self._legal_moves_map

        if self.drag_ghost_item is not None:
            self.canvas.delete(self.drag_ghost_item)
            self.drag_ghost_item = None
        self.drag_ghost_photo = None

        self.drag_from_sq = None
        self.drag_piece = None
        self._legal_moves_map = {}

        if sq is None or sq not in moves_map:
            self._draw_board()
            return

        candidates = moves_map[sq]
        self._draw_board()

        if len(candidates) == 1:
            self._finish_move_from_drag(candidates[0])
        else:
            self._offer_promotion_and_play(candidates)

    def _offer_promotion_and_play(self, promo_moves: list[chess.Move]) -> None:
        choice = tk.Toplevel(self.root)
        choice.title("Promotion")
        choice.transient(self.root)
        choice.grab_set()
        ttk.Label(choice, text="Promote to:").pack(padx=12, pady=8)
        fr = ttk.Frame(choice)
        fr.pack(padx=12, pady=(0, 12))

        def pick(pt: chess.PieceType) -> None:
            for m in promo_moves:
                if m.promotion == pt:
                    choice.destroy()
                    self._finish_move_from_drag(m)
                    return

        for pt, label in (
            (chess.QUEEN, "Queen"),
            (chess.ROOK, "Rook"),
            (chess.BISHOP, "Bishop"),
            (chess.KNIGHT, "Knight"),
        ):
            ttk.Button(fr, text=label, command=lambda p=pt: pick(p)).pack(side=tk.LEFT, padx=4)

    def _finish_move_from_drag(self, move: chess.Move) -> None:
        if self.exploration_var.get():
            self.board.push(move)
            self._abort_drag()
            self._draw_board()
            self._refresh_status_exploration()
            return
        self._play_human_move(move)

    def _play_human_move(self, move: chess.Move) -> None:
        snapshot = self.board.copy()
        self.board.push(move)
        self._abort_drag()
        self._draw_board()

        if self.board.is_game_over():
            self._schedule_analysis_only(snapshot, move)
            self._game_over_message()
            return

        self._update_status_after_human()
        self._analysis_set_loading()
        self._schedule_analysis_then_engine(snapshot, move)

    def _update_status_after_human(self) -> None:
        base = "Stockfish is thinking…"
        if self.board.is_check():
            self.status_var.set(f"Check — {base}")
        else:
            self.status_var.set(base)

    def _schedule_analysis_only(self, snapshot: chess.Board, move: chess.Move) -> None:
        if not self.engine:
            return

        def worker() -> None:
            try:
                with self._engine_lock:
                    payload = classify_human_move(self.engine, snapshot, move)
                self.root.after(0, lambda p=payload: self._analysis_show_result(p))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._analysis_show_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_analysis_then_engine(self, snapshot: chess.Board, move: chess.Move) -> None:
        if not self.engine:
            self.status_var.set("No engine — install Stockfish.")
            return

        self.engine_thinking = True
        self._sync_toolbar_state()

        def worker() -> None:
            assert self.engine is not None
            try:
                with self._engine_lock:
                    payload = classify_human_move(self.engine, snapshot, move)
                self.root.after(0, lambda p=payload: self._analysis_show_result(p))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._analysis_show_error(e))

            try:
                with self._engine_lock:
                    result = self.engine.play(self.board, chess.engine.Limit(time=0.5))
                    bot_move = result.move
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._engine_failed(e))
                return

            def apply_bot() -> None:
                self.engine_thinking = False
                self._sync_toolbar_state()
                if bot_move is None:
                    self.status_var.set("Engine returned no move.")
                    return
                self.board.push(bot_move)
                self._draw_board()
                if self.board.is_game_over():
                    self._game_over_message()
                elif self.board.turn == self.human_color:
                    self.status_var.set(
                        "Your turn — drag a piece." + (" (Check!)" if self.board.is_check() else "")
                    )
                else:
                    self.status_var.set("…")

            self.root.after(0, apply_bot)

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_engine_move(self) -> None:
        if not self.engine:
            self.status_var.set("No engine — install Stockfish.")
            return
        self.engine_thinking = True
        self._sync_toolbar_state()

        def worker() -> None:
            assert self.engine is not None
            try:
                with self._engine_lock:
                    result = self.engine.play(self.board, chess.engine.Limit(time=0.5))
                    bot_move = result.move
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._engine_failed(e))
                return

            def apply_bot() -> None:
                self.engine_thinking = False
                self._sync_toolbar_state()
                if bot_move is None:
                    self.status_var.set("Engine returned no move.")
                    return
                self.board.push(bot_move)
                self._draw_board()
                if self.board.is_game_over():
                    self._game_over_message()
                elif self.board.turn == self.human_color:
                    self.status_var.set(
                        "Your turn — drag a piece." + (" (Check!)" if self.board.is_check() else "")
                    )
                else:
                    self.status_var.set("…")

            self.root.after(0, apply_bot)

        threading.Thread(target=worker, daemon=True).start()

    def _engine_failed(self, exc: Exception) -> None:
        self.engine_thinking = False
        self._sync_toolbar_state()
        messagebox.showerror("Engine error", str(exc))
        self.status_var.set("Engine error.")

    def _game_over_message(self) -> None:
        if self.board.is_checkmate():
            winner = "You win!" if self.board.turn != self.human_color else "Stockfish wins."
            self.status_var.set(f"Checkmate — {winner}")
            messagebox.showinfo("Game over", winner)
        elif self.board.is_stalemate():
            self.status_var.set("Stalemate.")
            messagebox.showinfo("Game over", "Stalemate.")
        elif self.board.is_insufficient_material():
            self.status_var.set("Draw — insufficient material.")
            messagebox.showinfo("Game over", "Draw (insufficient material).")
        elif self.board.is_seventyfive_moves():
            self.status_var.set("Draw — 75-move rule.")
            messagebox.showinfo("Game over", "Draw (75-move rule).")
        elif self.board.is_fivefold_repetition():
            self.status_var.set("Draw — repetition.")
            messagebox.showinfo("Game over", "Draw (repetition).")
        else:
            self.status_var.set("Game over.")
            messagebox.showinfo("Game over", "Game over.")

    def on_close(self) -> None:
        if self.engine:
            with self._engine_lock:
                self.engine.quit()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = ChessVsStockfishApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
