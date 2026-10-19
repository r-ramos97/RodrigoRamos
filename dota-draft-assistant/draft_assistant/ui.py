"""Exibição do modo automático: modelo de tela puro, saída no terminal e janela flutuante (Tkinter).

A janela é uma janela comum do sistema, sempre no topo e um pouco transparente, para ficar ao
lado ou por cima do Dota em modo "janela sem bordas" (em tela cheia exclusiva o jogo a cobre).
Ela não se conecta ao processo do jogo, não desenha dentro dele e não envia teclas nem cliques:
só mostra texto.
"""

import json
import os
import queue
import re
import sys
import threading
import time
import traceback
from pathlib import Path

from .models import AutoUpdate, DraftState, Hero
from .scoring import Suggestion

TITLE = "Dota Draft Assistant"
STATUS = {"idle": "Fora do draft", "draft": "Draft em andamento", "done": "Draft encerrado"}
SOURCES = {"gsi": "GSI", "screen": "Tela"}
WAITING = "Aguardando o início do draft..."
CLEAR = "\033[2J\033[H"
# (chave do breakdown, rótulo, mostra sinal): mesmos rótulos do `suggest` no terminal.
BREAKDOWN = (("counter", "counter", True), ("synergy", "sinergia", True), ("meta", "meta", True),
             ("pro", "pro", False), ("coach", "coach", False))
SECTIONS = {"picks": "Picks sugeridos", "bans_suggested": "Bans sugeridos"}
GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]-?\d+){2}$")


def turn_text(update: AutoUpdate) -> str:
    """"SUA VEZ: PICK (23s)", "SUA VEZ: BAN", "Vez do adversário" ou "" quando não se sabe."""
    turn = update.turn
    if update.phase != "draft" or turn is None or turn.my_turn is None:
        return ""
    if not turn.my_turn:
        return "Vez do adversário"
    kind = {True: ": PICK", False: ": BAN"}.get(turn.is_pick, "")
    secs = f" ({max(0, round(turn.seconds_left))}s)" if turn.seconds_left is not None else ""
    return f"SUA VEZ{kind}{secs}"


def breakdown_text(breakdown: dict[str, float]) -> str:
    """Resumo compacto, sem os componentes zerados: "counter +3.1 | meta +1.0 | coach 2.5"."""
    parts = []
    for key, label, signed in BREAKDOWN:
        value = breakdown.get(key, 0.0)
        if abs(value) >= 0.05:
            parts.append(f"{label} {value:+.1f}" if signed else f"{label} {value:.1f}")
    return " | ".join(parts)


def _item(s: Suggestion) -> dict:
    return {"name": s.hero.localized_name, "score": float(s.score),
            "detail": breakdown_text(s.breakdown), "reason": "; ".join(s.reasons[:2])}


def build_view(update: AutoUpdate, heroes: dict[int, Hero], picks: list[Suggestion],
               bans: list[Suggestion]) -> dict:
    """Converte a atualização automática em dados prontos para exibir (sem depender de Tk)."""
    state = update.state or DraftState()
    turn = update.turn
    my_turn = update.phase == "draft" and bool(turn and turn.my_turn)
    focus = "bans" if my_turn and turn.is_pick is False else "picks"

    def names(ids: list[int]) -> list[str]:
        return [heroes[i].localized_name for i in ids if i in heroes]

    view = {
        "title": TITLE,
        "phase": update.phase,
        "status": STATUS.get(update.phase, update.phase),
        "turn": turn_text(update),
        "my_turn": my_turn,
        "focus": focus,
        "side": (update.side or "").capitalize(),
        "source": SOURCES.get(update.source or "", update.source or ""),
        "allies": names(state.allies),
        "enemies": names(state.enemies),
        "bans": names(state.bans),
    }
    sections = {"picks": [_item(s) for s in picks], "bans_suggested": [_item(s) for s in bans]}
    view.update((key, sections[key]) for key in section_order(view))
    return view


def section_order(view: dict) -> tuple[str, str]:
    """Na fase de ban do jogador, os bans sugeridos vêm primeiro."""
    return ("bans_suggested", "picks") if view.get("focus") == "bans" else ("picks", "bans_suggested")


def _has_suggestions(view: dict) -> bool:
    return bool(view["picks"] or view["bans_suggested"])


def format_view(view: dict) -> str:
    """Texto para o terminal."""
    meta = " | ".join(x for x in (view["source"], view["side"]) if x)
    lines = [view["title"] + (f"  [{meta}]" if meta else ""), view["status"]]
    if view["turn"]:
        lines.append(f">>> {view['turn']} <<<" if view["my_turn"] else view["turn"])
    lines += ["", f"Aliados:  {', '.join(view['allies']) or '-'}",
              f"Inimigos: {', '.join(view['enemies']) or '-'}",
              f"Banidos:  {', '.join(view['bans']) or '-'}"]
    if not _has_suggestions(view):
        if view["phase"] == "idle":
            lines += ["", WAITING]
        return "\n".join(lines)
    for key in section_order(view):
        lines += ["", SECTIONS[key] + ":"]
        for i, item in enumerate(view[key], 1):
            lines.append(f"{i:>2}. {item['name']:<20} {item['score']:+6.2f}   {item['detail']}")
            if item["reason"]:
                lines.append("      " + item["reason"])
        if not view[key]:
            lines.append("  -")
    return "\n".join(lines)


def overlay_segments(view: dict) -> list[tuple[str, str]]:
    """Trechos (texto, tag) da janela flutuante, na ordem em que entram no widget Text."""
    meta = " · ".join(x for x in (view["source"], view["side"]) if x)
    out = [(view["status"], "status"), (f"   {meta}\n" if meta else "\n", "muted")]
    if view["turn"]:
        out.append((view["turn"] + "\n", "turn_me" if view["my_turn"] else "turn_other"))
    for label, key, tag in (("Aliados ", "allies", "ally"), ("Inimigos ", "enemies", "enemy"),
                            ("Banidos ", "bans", "ban")):
        out += [(label, "label"), ((", ".join(view[key]) or "-") + "\n", tag)]
    if not _has_suggestions(view):
        if view["phase"] == "idle":
            out.append(("\n" + WAITING + "\n", "muted"))
        return out
    for key in section_order(view):
        out.append((SECTIONS[key].upper() + "\n", "section_ban" if key == "bans_suggested" else "section_pick"))
        for i, item in enumerate(view[key], 1):
            out += [(f"{i}. {item['name']}", "hero"), (f"  {item['score']:+.1f}\n", "score")]
            if item["detail"]:
                out.append((f"   {item['detail']}\n", "detail"))
            if item["reason"]:
                out.append((f"   {item['reason']}\n", "reason"))
        if not view[key]:
            out.append(("   -\n", "muted"))
    return out


class TerminalView:
    """Mostra a última atualização no terminal, limpando a tela a cada push()."""

    def __init__(self):
        self._lock = threading.Lock()
        self._closed = threading.Event()
        if os.name == "nt":
            os.system("")  # liga as sequências ANSI no console clássico do Windows

    def push(self, view: dict) -> None:
        with self._lock:
            print(CLEAR + format_view(view), flush=True)

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Só espera (as atualizações chegam por push() de outras threads).

        Volta com close(), com `stop_event` ou com Ctrl+C, e então marca `stop_event`.
        """
        try:
            while not self._closed.is_set() and not (stop_event and stop_event.is_set()):
                # sleep e não Event.wait: Ctrl+C dentro de Event.wait pode corromper o lock interno
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            if stop_event:
                stop_event.set()

    def close(self) -> None:
        self._closed.set()


FONT = "Segoe UI" if os.name == "nt" else "Helvetica"
BG, FG, MUTED, ACCENT, GOOD, BAD = "#15171c", "#e8e8e8", "#8a8f98", "#ffcc33", "#7fd17f", "#ff7b72"
TAGS = {
    "status": {"foreground": "#c9ccd3", "font": (FONT, 10, "bold")},
    "muted": {"foreground": MUTED, "font": (FONT, 9)},
    "turn_me": {"foreground": ACCENT, "font": (FONT, 14, "bold"), "spacing1": 4, "spacing3": 4},
    "turn_other": {"foreground": MUTED, "font": (FONT, 11), "spacing1": 2, "spacing3": 2},
    "label": {"foreground": MUTED, "font": (FONT, 9)},
    "ally": {"foreground": GOOD, "font": (FONT, 9)},
    "enemy": {"foreground": BAD, "font": (FONT, 9)},
    "ban": {"foreground": "#a0a4ab", "font": (FONT, 9)},
    "section_pick": {"foreground": GOOD, "font": (FONT, 10, "bold"), "spacing1": 10, "spacing3": 2},
    "section_ban": {"foreground": BAD, "font": (FONT, 10, "bold"), "spacing1": 10, "spacing3": 2},
    "hero": {"foreground": FG, "font": (FONT, 11, "bold"), "spacing1": 3},
    "score": {"foreground": ACCENT, "font": (FONT, 10)},
    "detail": {"foreground": MUTED, "font": (FONT, 8)},
    "reason": {"foreground": "#b8bcc4", "font": (FONT, 8, "italic")},
}


def _load_geometry(path: Path | None) -> str | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8")).get("geometry")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and GEOMETRY_RE.match(value) else None


def _save_geometry(path: Path | None, geometry: str) -> None:
    if not path or not GEOMETRY_RE.match(geometry):
        return
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"geometry": geometry}), encoding="utf-8")
    except OSError:
        pass


class OverlayWindow:
    """Janela pequena e escura, sempre no topo. push() e close() podem ser chamados de qualquer thread.

    Crie a janela e chame run() na mesma thread (a principal): o Tk só aceita ser usado por ela.
    `borderless=True` tira a barra de título; aí a janela é arrastada com o mouse e fecha com Esc.
    """

    def __init__(self, alpha: float = 0.92, geometry_file: Path | None = None, borderless: bool = False,
                 default_geometry: str = "360x560+40+40"):
        import tkinter as tk  # import tardio: ui.py precisa importar mesmo sem Tk instalado

        self._tk = tk
        self.geometry_file = Path(geometry_file) if geometry_file else None
        self._queue: queue.Queue[dict] = queue.Queue()
        self._closing = threading.Event()
        self._stop: threading.Event | None = None
        self._destroyed = False
        self._owner = threading.get_ident()
        if sys.platform == "win32":
            try:  # texto nítido com escala de tela de 125%/150% (só afeta este processo)
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        self.root = tk.Tk()  # TclError quando não há tela gráfica
        self.root.title(TITLE)
        self.root.configure(bg=BG)
        self.root.geometry(_load_geometry(self.geometry_file) or default_geometry)
        for name, value in (("-topmost", True), ("-alpha", alpha)):
            try:
                self.root.attributes(name, value)
            except tk.TclError:
                pass  # ex.: X11 sem compositor não faz transparência
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self._on_error
        self.text = tk.Text(self.root, bg=BG, fg=FG, bd=0, highlightthickness=0, padx=10, pady=8,
                            wrap="word", width=44, height=30, cursor="arrow", font=(FONT, 10))
        self.text.pack(fill="both", expand=True)
        for tag, options in TAGS.items():
            self.text.tag_configure(tag, **options)
        if borderless:
            self.root.overrideredirect(True)
            self.text.bind("<ButtonPress-1>", self._drag_start)
            self.text.bind("<B1-Motion>", self._drag_move)
            self.root.bind("<Escape>", lambda _event: self.close())
        self._write([("Aguardando o Dota...\n", "muted")])

    def push(self, view: dict) -> None:
        self._queue.put(view)

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Roda o loop do Tk até a janela fechar (close(), botão fechar, Ctrl+C ou `stop_event`)."""
        self._stop = stop_event
        try:
            if not self._destroyed:
                self.root.after(100, self._poll)
                self.root.mainloop()
        finally:
            self._shutdown()
            if stop_event:
                stop_event.set()

    def close(self) -> None:
        self._closing.set()
        if threading.get_ident() == self._owner:
            self._shutdown()
        # Em outra thread, o _poll da thread do Tk fecha em até 100 ms.

    def _poll(self) -> None:
        if self._closing.is_set() or (self._stop is not None and self._stop.is_set()):
            self._shutdown()
            return
        view = None
        try:
            while True:  # só a mais recente interessa
                view = self._queue.get_nowait()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)  # agenda antes de desenhar: um erro aqui não congela a janela
        if view is not None:
            self.root.title(f"{TITLE} - {view['turn']}" if view["turn"] else TITLE)
            self._write(overlay_segments(view))

    def _write(self, segments: list[tuple[str, str]]) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for text, tag in segments:
            self.text.insert("end", text, tag)
        self.text.configure(state="disabled")

    def _shutdown(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        try:
            if self.root.state() not in ("iconic", "withdrawn"):  # minimizada, a posição vem errada
                _save_geometry(self.geometry_file, self.root.geometry())
            self.root.destroy()
        except self._tk.TclError:
            pass
        if self._stop is not None:
            self._stop.set()

    def _on_error(self, exc, value, tb) -> None:
        if issubclass(exc, KeyboardInterrupt):
            self._shutdown()
        else:
            traceback.print_exception(exc, value, tb)

    def _drag_start(self, event) -> None:
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        dx, dy = self._drag
        self.root.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")


def make_display(prefer_gui: bool = True, geometry_file: Path | None = None) -> OverlayWindow | TerminalView:
    """Janela flutuante quando há Tk e tela gráfica; senão, o terminal (com um aviso do motivo)."""
    if prefer_gui:
        try:
            import tkinter
        except ImportError:
            print("Aviso: tkinter não está instalado; as sugestões vão aparecer no terminal.", file=sys.stderr)
        else:
            try:
                return OverlayWindow(geometry_file=geometry_file)
            except tkinter.TclError as exc:
                print(f"Aviso: sem tela gráfica para a janela ({exc}); as sugestões vão aparecer no terminal.",
                      file=sys.stderr)
    return TerminalView()
