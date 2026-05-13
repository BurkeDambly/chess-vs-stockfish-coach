# Chess vs Stockfish Coach

Chess vs Stockfish Coach is a Python desktop chess app with a drag-and-drop board, optional Elo-limited Stockfish play, and post-move analysis. After each human move, Stockfish evaluates the position and labels the move using coaching-style categories such as Best, Good, Inaccuracy, Mistake, and Blunder.

## Features

- Drag-and-drop chess board built with Tkinter
- Stockfish opponent integration
- Adjustable approximate bot Elo
- Move analysis using multi-PV engine search
- Coaching-style move labels
- Legal move hints while dragging
- Manual opponent mode for exploring alternative lines
- PNG chess piece loading with fallback drawn sprites
