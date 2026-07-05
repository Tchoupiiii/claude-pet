"""Claude Pet - compagnon pixel-art qui reflete l'etat de Claude Code.
Etats (state.txt) : working / idle / question / sleeping / tired [HH:MM]
Windows : en bas a droite + icone tray. macOS : en haut au centre, sous l'encoche.
Clic droit sur le pet : deplacer / repositionner / taille / masquer / quitter.
Glisser = deplacer (position memorisee).
"""
import datetime
import json
import math
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes
    import ctypes.wintypes as wt
    import winsound
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.txt")
CONFIG_FILE = os.path.join(HERE, "config.json")
ICON_FILE = os.path.join(HERE, "icon.ico")
LOG_FILE = os.path.join(HERE, "error.log")


def log_exc(where):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {where}\n")
            f.write(traceback.format_exc() + "\n")
    except OSError:
        pass

TRANS = "#010203"
MAC_BG = "systemTransparent"
BASE = "#D97757"
DARK = "#B05730"
EYE = "#2B2020"
SCALES = {"petit": 4, "moyen": 6, "grand": 8}

# sprite exact du modele Piskel (New Piskel.png 32x32 -> 11x8 utile,
# couleur unique #D97757, yeux = pixels transparents), rectangulaire, pattes courtes.
BODY = [
    ".#########.",
    ".#########.",
    "###########",
    "###########",
    ".#########.",
    ".#########.",
]
GW, BODY_H = 11, 6
GH = 7
EYES = [(2, 2), (8, 2)]
LEGS = [1, 3, 7, 9]

DEFAULT_CFG = {"visible": True, "monitor": "primary", "size": "moyen",
               "sounds": True, "pos": None, "wander": False,
               "layer": "top", "eco": True}

SOUNDS_WIN = {
    "done": [(784, 80), (1046, 90), (1318, 140)],
    "question": [(880, 90), (740, 90), (1175, 160)],
    "tired": [(660, 130), (550, 130), (392, 240)],
    "heart": [(1046, 60), (1318, 90)],
    "grr": [(220, 140), (180, 200)],
}
SOUNDS_MAC = {
    "done": "/System/Library/Sounds/Glass.aiff",
    "question": "/System/Library/Sounds/Funk.aiff",
    "tired": "/System/Library/Sounds/Basso.aiff",
    "heart": "/System/Library/Sounds/Pop.aiff",
    "grr": "/System/Library/Sounds/Sosumi.aiff",
}


def play_sound(name):
    def run():
        try:
            if IS_WIN:
                for f, d in SOUNDS_WIN.get(name, []):
                    winsound.Beep(f, d)
            elif IS_MAC:
                path = SOUNDS_MAC.get(name)
                if path and os.path.exists(path):
                    subprocess.run(["afplay", path], timeout=5)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def cell_color(r, c):
    """Modele exact : couleur unique, pas d'ombrage."""
    if 0 <= r < BODY_H and 0 <= c < GW and BODY[r][c] != ".":
        return BASE
    return None


def load_cfg():
    try:
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            cfg = {**DEFAULT_CFG, **json.load(f)}
    except (OSError, ValueError):
        cfg = dict(DEFAULT_CFG)
    if cfg["size"] not in SCALES:
        cfg["size"] = "moyen"
    return cfg


def save_cfg(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


def get_monitors():
    if not IS_WIN:
        return []
    mons = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, hdc, rct, lp):
        r = rct.contents
        mons.append((r.left, r.top, r.right, r.bottom))
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(0, 0, proto(cb), 0)
    return mons


def scan_rate_limit():
    """Cherche une limite de tokens dans le transcript le plus recent (< 5 min)."""
    try:
        base = os.path.expanduser("~/.claude/projects")
        best = None
        cutoff = time.time() - 300
        for root_, _dirs, files in os.walk(base):
            for fn in files:
                if fn.endswith(".jsonl"):
                    p = os.path.join(root_, fn)
                    try:
                        m = os.path.getmtime(p)
                    except OSError:
                        continue
                    if m > cutoff and (best is None or m > best[0]):
                        best = (m, p)
        if not best:
            return None
        with open(best[1], "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 10000))
            tail = f.read().decode("utf-8", "ignore").lower()
        if ("usage limit" in tail or "limite d'utilisation" in tail
                or "rate limit" in tail or "out of tokens" in tail):
            mt = re.search(r"reset[^0-9]{0,30}(\d{1,2})\s*[:h]\s*(\d{2})", tail)
            if mt:
                return f"tired {mt.group(1)}:{mt.group(2)}"
            mt = re.search(r'"resets?at"\s*:\s*(\d{10,13})', tail)
            if mt:
                ts = int(mt.group(1))
                if ts > 1e12:
                    ts //= 1000
                lt = time.localtime(ts)
                return f"tired {lt.tm_hour}:{lt.tm_min:02d}"
            return "tired"
    except Exception:
        pass
    return None


def find_shortcut_pos(name="claude pet"):
    """Position ecran de l'icone du raccourci sur le bureau (lecture seule)."""
    if not IS_WIN:
        return None
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        u.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        k.OpenProcess.restype = ctypes.c_void_p
        k.VirtualAllocEx.restype = ctypes.c_void_p
        k.VirtualAllocEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                     ctypes.c_size_t, wt.DWORD, wt.DWORD]
        k.WriteProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        k.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        k.VirtualFreeEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t, wt.DWORD]
        defv = u.FindWindowExW(u.FindWindowW("Progman", None), 0, "SHELLDLL_DefView", None)
        hw = 0
        while not defv:
            hw = u.FindWindowExW(0, hw, "WorkerW", None)
            if not hw:
                break
            defv = u.FindWindowExW(hw, 0, "SHELLDLL_DefView", None)
        lv = u.FindWindowExW(defv, 0, "SysListView32", None) if defv else 0
        if not lv:
            return None
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(lv, ctypes.byref(pid))
        hp = k.OpenProcess(0x0438, False, pid.value)
        if not hp:
            return None

        class LVITEM(ctypes.Structure):
            _fields_ = [("mask", wt.UINT), ("iItem", ctypes.c_int),
                        ("iSubItem", ctypes.c_int), ("state", wt.UINT),
                        ("stateMask", wt.UINT), ("pszText", ctypes.c_void_p),
                        ("cchTextMax", ctypes.c_int), ("iImage", ctypes.c_int),
                        ("lParam", ctypes.c_void_p), ("iIndent", ctypes.c_int),
                        ("iGroupId", ctypes.c_int), ("cColumns", wt.UINT),
                        ("puColumns", ctypes.c_void_p), ("piColFmt", ctypes.c_void_p),
                        ("iGroup", ctypes.c_int)]

        try:
            buf = k.VirtualAllocEx(hp, None, 4096, 0x3000, 4)
            if not buf:
                return None
            count = u.SendMessageW(lv, 0x1004, 0, 0)  # LVM_GETITEMCOUNT
            txt = ctypes.create_unicode_buffer(256)
            for i in range(count):
                it = LVITEM(mask=1, iItem=i, pszText=buf + 2048, cchTextMax=255)
                k.WriteProcessMemory(hp, buf, ctypes.byref(it), ctypes.sizeof(it), None)
                u.SendMessageW(lv, 0x1073, i, buf)  # LVM_GETITEMTEXTW
                k.ReadProcessMemory(hp, buf + 2048, txt, 510, None)
                if name in txt.value.lower():
                    u.SendMessageW(lv, 0x1010, i, buf)  # LVM_GETITEMPOSITION
                    pt = wt.POINT()
                    k.ReadProcessMemory(hp, buf, ctypes.byref(pt), ctypes.sizeof(pt), None)
                    u.ClientToScreen(lv, ctypes.byref(pt))
                    return (pt.x, pt.y)
            return None
        finally:
            try:
                k.VirtualFreeEx(hp, buf, 0, 0x8000)
            except Exception:
                pass
            k.CloseHandle(hp)
    except Exception:
        return None


def build_pil_image(px=5):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (GW * px, GH * px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r in range(BODY_H):
        for c in range(GW):
            col = cell_color(r, c)
            if col:
                d.rectangle([c * px, r * px, (c + 1) * px - 1, (r + 1) * px - 1], fill=col)
    for ex, ey in EYES:
        d.rectangle([ex * px, ey * px, (ex + 1) * px - 1, (ey + 1) * px - 1], fill=(0, 0, 0, 0))
    for c1 in LEGS:
        d.rectangle([c1 * px, BODY_H * px, (c1 + 1) * px - 1, (BODY_H + 1) * px - 1], fill=BASE)
    return img


def make_icon():
    from PIL import Image
    sprite = build_pil_image(8)
    side = max(sprite.width, sprite.height)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(sprite, ((side - sprite.width) // 2, (side - sprite.height) // 2), sprite)
    square.save(ICON_FILE, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])


class Pet:
    def __init__(self):
        self.cfg = load_cfg()
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda *a: log_exc("tk-callback")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        if IS_WIN:
            self.root.attributes("-transparentcolor", TRANS)
            self.bg = TRANS
            self.hole = TRANS  # yeux = vrais trous (color-key)
        else:
            try:
                self.root.attributes("-transparent", True)
                self.bg = MAC_BG
            except tk.TclError:
                self.bg = "#1E1E1E"
            self.hole = EYE  # pas de color-key sur mac : yeux sombres
        self.root.config(bg=self.bg)
        self.canvas = tk.Canvas(self.root, bg=self.bg, highlightthickness=0)
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<Button-3>", self._popup)
        if IS_MAC:
            self.canvas.bind("<Button-2>", self._popup)  # clic secondaire aqua
        if IS_WIN:
            u = ctypes.windll.user32
            u.GetAncestor.restype = ctypes.c_void_p
            u.WindowFromPoint.argtypes = [wt.POINT]
            u.WindowFromPoint.restype = ctypes.c_void_p
        self.covered = False
        self._make_fx_window()

        self.t = 0
        self.state = "idle"
        self.tired_until = None  # "HH:MM" de reinitialisation des tokens
        self.blink = 0
        self.next_blink = random.randint(60, 160)
        self.glance = 0
        self.next_glance = random.randint(90, 240)
        self.bounce_v = 0.0
        self.bounce_y = 0.0
        self.hover = False
        self.mouse = (0, 0)
        self.activity = None
        self.act_t = 0
        self.act_t0 = 1
        self.next_act = random.randint(250, 600)
        self.hover_frames = 0
        self.work_frames = 0
        self.party = 0
        self._party_x = 0
        self.hearts = []
        self.drowsy = 0  # reste eveille un moment apres un reveil
        self.walk = None  # cible (x, y) de la balade
        self.walking = False
        self.walk_dir = (0, 0)
        self._body_dy = 0
        self._cov_miss = 0
        self.grab_count = 0  # manipulations recentes
        self.annoyed = 0  # ras-le-bol (deplace trop souvent)
        self.tired_frames = 0
        self.walk_wait = random.randint(150, 450)
        self.peek = None  # [hwnd, x0, y0, frames] fenetre soulevee
        self.walk_goal = None
        self.selfie = 0  # animation devant son propre raccourci
        self._dirty = True
        self._dragging = False
        self.move_mode = False
        self._quit_flag = False
        self.tray = None
        if IS_WIN:
            self._start_tray()
        self.tick()
        self.root.mainloop()

    # ---------- overlay plein ecran pour les particules (confettis, coeurs...) ----------
    def _make_fx_window(self):
        self.fx = tk.Toplevel(self.root)
        self.fx.overrideredirect(True)
        self.fx.attributes("-topmost", True)
        try:
            self.fx.attributes("-disabledevents", True)  # clic-transparent (Windows)
        except tk.TclError:
            pass
        if IS_WIN:
            self.fx.attributes("-transparentcolor", TRANS)
            fx_bg = TRANS
        else:
            try:
                self.fx.attributes("-transparent", True)
                fx_bg = MAC_BG
            except tk.TclError:
                fx_bg = "#1E1E1E"
        self.fx.config(bg=fx_bg)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.fx.geometry(f"{sw}x{sh}+0+0")
        self.fx_canvas = tk.Canvas(self.fx, bg=fx_bg, highlightthickness=0, width=sw, height=sh)
        self.fx_canvas.pack()
        self.fx_clickthrough_ok = not IS_WIN  # sur mac, rien a verifier
        if IS_WIN:
            self._apply_fx_clickthrough(retries=20)
            if not self.fx_clickthrough_ok:
                # securite absolue : impossible de garantir le clic-transparent
                # -> on detruit l'overlay plutot que de risquer de bloquer tout l'ecran
                log_exc("fx-clickthrough-failed: destroying fx overlay")
                self.fx.destroy()
                self.fx = None
                self.fx_canvas = None

    def _apply_fx_clickthrough(self, retries=1):
        """Force le style click-through de la fenetre fx et VERIFIE qu'il est bien pris.
        Cette fenetre couvre tout l'ecran : si le style echoue, elle bloque tous les clics."""
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x20
        WS_EX_LAYERED = 0x80000
        u = ctypes.windll.user32
        for _ in range(retries):
            self.fx.update_idletasks()
            hwnd = u.GetAncestor(self.fx_canvas.winfo_id(), 2)  # GA_ROOT
            if hwnd:
                style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
                u.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_LAYERED)
                check = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if check & WS_EX_TRANSPARENT:
                    self.fx_clickthrough_ok = True
                    return
            time.sleep(0.05)
        self.fx_clickthrough_ok = False

    # ---------- config / placement ----------
    def apply_config(self):
        self.root.attributes("-topmost", self.cfg.get("layer", "top") != "desktop")
        s = SCALES[self.cfg["size"]]
        self.s = s
        self.cw = GW * s + 64
        self.chh = 46 + GH * s + 8
        self.canvas.config(width=self.cw, height=self.chh)
        pos = self.cfg.get("pos") or self._default_pos()
        if pos:
            # position hors ecran (a peine visible) -> on l'ignore
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            mons = get_monitors() or [(0, 0, sw, sh)]
            vx1 = min(m[0] for m in mons)
            vy1 = min(m[1] for m in mons)
            vx2 = max(m[2] for m in mons)
            vy2 = max(m[3] for m in mons)
            if not (vx1 - 20 <= pos[0] <= vx2 - 60 and vy1 - 10 <= pos[1] <= vy2 - 60):
                pos = None
                self.cfg["pos"] = None
                save_cfg(self.cfg)
        if pos:
            x, y = pos
        elif IS_MAC:
            # en haut au centre, juste sous l'encoche / la barre de menu
            sw = self.root.winfo_screenwidth()
            x = (sw - self.cw) // 2
            y = 0
        else:
            mons = get_monitors()
            if not mons:
                mons = [(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())]
            primary = next((m for m in mons if m[0] == 0 and m[1] == 0), mons[0])
            others = [m for m in mons if m != primary]
            rect = primary if self.cfg["monitor"] == "primary" or not others else others[0]
            x = rect[2] - self.cw - 14
            y = rect[3] - self.chh - 64
        self.root.geometry(f"{self.cw}x{self.chh}+{x}+{y}")
        if self.cfg["visible"]:
            self.root.deiconify()
        else:
            self.root.withdraw()

    def _default_pos(self):
        if IS_MAC:
            return (self.root.winfo_screenwidth() - self.cw) // 2, 0
        mons = get_monitors() or [(0, 0, self.root.winfo_screenwidth(),
                                   self.root.winfo_screenheight())]
        primary = next((m for m in mons if m[0] == 0 and m[1] == 0), mons[0])
        others = [m for m in mons if m != primary]
        rect = primary if self.cfg["monitor"] == "primary" or not others else others[0]
        return rect[2] - self.cw - 14, rect[3] - self.chh - 64

    def _set(self, key, val):
        self.cfg[key] = val
        if key == "monitor":
            self.cfg["pos"] = None
        save_cfg(self.cfg)
        self._dirty = True

    def _save_pos(self):
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_cfg(self.cfg)

    # ---------- tray (Windows uniquement) ----------
    def _start_tray(self):
        try:
            import pystray
        except ImportError:
            return
        if not os.path.exists(ICON_FILE):
            try:
                make_icon()
            except Exception:
                pass
        img = build_pil_image(6)
        M, I = pystray.Menu, pystray.MenuItem
        menu = M(
            I("Afficher le pet", lambda *a: self._set("visible", not self.cfg["visible"]),
              checked=lambda i: self.cfg["visible"]),
            I("Déplacer (clic pour poser)", lambda *a: self._start_move()),
            I("Remettre en bas à droite", lambda *a: self._set("pos", None)),
            I("Écran", M(
                I("Principal", lambda *a: self._set("monitor", "primary"),
                  radio=True, checked=lambda i: self.cfg["monitor"] == "primary"),
                I("Secondaire", lambda *a: self._set("monitor", "secondary"),
                  radio=True, checked=lambda i: self.cfg["monitor"] == "secondary"),
            )),
            I("Taille", M(*[
                I(name.capitalize(), lambda *a, n=name: self._set("size", n),
                  radio=True, checked=lambda i, n=name: self.cfg["size"] == n)
                for name in ("petit", "moyen", "grand")
            ])),
            I("Sons", lambda *a: self._set("sounds", not self.cfg.get("sounds")),
              checked=lambda i: bool(self.cfg.get("sounds"))),
            I("Balade sur le bureau", lambda *a: self._toggle_wander(),
              checked=lambda i: bool(self.cfg.get("wander"))),
            I("Avant-plan", M(
                I("Toujours devant", lambda *a: self._set("layer", "top"),
                  radio=True, checked=lambda i: self.cfg.get("layer", "top") == "top"),
                I("Sur le bureau (derrière)", lambda *a: self._set("layer", "desktop"),
                  radio=True, checked=lambda i: self.cfg.get("layer") == "desktop"),
            )),
            I("Éco (pause si recouvert)", lambda *a: self._set("eco", not self.cfg.get("eco")),
              checked=lambda i: bool(self.cfg.get("eco"))),
            I("Quitter", self._quit),
        )
        self.tray = pystray.Icon("claude-pet", img, "Claude Pet", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _quit(self, *a):
        self._quit_flag = True

    def _scan_limit(self):
        res = scan_rate_limit()
        if res:
            try:
                with open(STATE_FILE, "w", encoding="utf-8") as f:
                    f.write(res)
            except OSError:
                pass

    # ---------- interactions ----------
    def _popup(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Déplacer (clic pour poser)", command=self._start_move)
        reset_label = "Remettre sous l'encoche" if IS_MAC else "Remettre en bas à droite"
        m.add_command(label=reset_label, command=lambda: self._set("pos", None))
        size = tk.Menu(m, tearoff=0)
        for name in ("petit", "moyen", "grand"):
            size.add_command(label=name.capitalize(),
                             command=lambda n=name: self._set("size", n))
        m.add_cascade(label="Taille", menu=size)
        if IS_WIN:
            ecran = tk.Menu(m, tearoff=0)
            ecran.add_command(label="Principal", command=lambda: self._set("monitor", "primary"))
            ecran.add_command(label="Secondaire", command=lambda: self._set("monitor", "secondary"))
            m.add_cascade(label="Écran", menu=ecran)
        m.add_command(label="Sons " + ("✓" if self.cfg.get("sounds") else "✗"),
                      command=lambda: self._set("sounds", not self.cfg.get("sounds")))
        if IS_WIN:
            m.add_command(label="Balade sur le bureau " + ("✓" if self.cfg.get("wander") else "✗"),
                          command=self._toggle_wander)
            plan = tk.Menu(m, tearoff=0)
            lay = self.cfg.get("layer", "top")
            plan.add_command(label="Toujours devant" + (" ✓" if lay == "top" else ""),
                             command=lambda: self._set("layer", "top"))
            plan.add_command(label="Sur le bureau (derrière)" + (" ✓" if lay == "desktop" else ""),
                             command=lambda: self._set("layer", "desktop"))
            m.add_cascade(label="Avant-plan", menu=plan)
            m.add_command(label="Éco si recouvert " + ("✓" if self.cfg.get("eco") else "✗"),
                          command=lambda: self._set("eco", not self.cfg.get("eco")))
        m.add_command(label="Masquer", command=lambda: self._set("visible", False))
        m.add_separator()
        m.add_command(label="Quitter", command=self._quit)
        m.tk_popup(e.x_root, e.y_root)

    def _toggle_wander(self):
        on = not self.cfg.get("wander")
        if not on:
            self._end_peek()
        self._set("wander", on)  # desactivation -> retour a sa position

    def _start_move(self):
        self.move_mode = True

    def _drag_start(self, e):
        if self.move_mode:
            self.move_mode = False
            self._save_pos()
            self._grabbed()
            return
        self._dx, self._dy = e.x, e.y
        self._dragging = False

    def _drag_move(self, e):
        if self.move_mode:
            return
        self.walk = None  # on l'attrape : la balade s'arrete
        self.walk_goal = None
        self.walking = False
        self._dragging = True
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _grabbed(self):
        self.grab_count += 1
        if self.grab_count >= 3:  # trop manipule -> agace
            self.grab_count = 0
            self.annoyed = 200
            if self.cfg.get("sounds"):
                play_sound("grr")

    def _drag_end(self, e):
        if self._dragging:
            self._dragging = False
            self._save_pos()
            self._grabbed()
        else:
            # simple clic = caresse (pas de rebond cumule, il ne s'envole pas)
            self.hearts.append([e.x_root, e.y_root - 6, 0])  # position ecran fixe
            if self.bounce_y > -4:
                self.bounce_v = -3.0
            if self.cfg.get("sounds"):
                play_sound("heart")

    def _hwnd(self):
        """Handle natif ACTUEL (Tk recree la fenetre au premier affichage)."""
        return ctypes.windll.user32.GetAncestor(self.canvas.winfo_id(), 2)

    def _is_covered(self):
        """True si le corps du pet est entierement cache par d'autres fenetres."""
        try:
            u = ctypes.windll.user32
            x, y = self.root.winfo_x(), self.root.winfo_y()
            s = self.s
            oy = y + 46 + self._body_dy  # suit le corps (respiration / saut)
            x1, x2 = x + 8 + int(2.5 * s), x + 8 + int(8.5 * s)
            y1, y2 = oy + int(1.5 * s), oy + 4 * s
            pts = ((x1, y1), (x2, y1), (x1, y2), (x2, y2),
                   ((x1 + x2) // 2, (y1 + y2) // 2))
            mine = self._hwnd()
            for px_, py_ in pts:  # pause uniquement si TOUT le corps est cache
                h = u.WindowFromPoint(wt.POINT(px_, py_))
                if h and u.GetAncestor(h, 2) == mine:
                    return False
            return True
        except Exception:
            return False

    # ---------- balade sur le bureau ----------
    def _find_peek_window(self):
        """Fenetre visible la plus proche du pet (jamais la sienne ni une maximisee)."""
        if not IS_WIN:
            return None
        u = ctypes.windll.user32
        wins = []
        proto = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.LPARAM)

        me = os.getpid()

        def cb(h, lp):
            if u.IsWindowVisible(h) and not u.IsIconic(h) and not u.IsZoomed(h) \
                    and u.GetWindowTextLengthW(h) > 0:
                cls = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(h, cls, 64)
                if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd",
                                 "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"):
                    return 1  # jamais le bureau ni la barre des taches
                pid = wt.DWORD()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == me:
                    return 1
                r = wt.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                if r.right - r.left > 250 and r.bottom - r.top > 180:
                    wins.append((h, r.left, r.top))
            return 1

        u.EnumWindows(proto(cb), 0)
        if not wins:
            return None
        px_, py_ = self.root.winfo_x() + self.cw // 2, self.root.winfo_y() + self.chh // 2
        return min(wins, key=lambda w: abs(w[1] - px_) + abs(w[2] - py_))

    def _start_peek(self):
        w = self._find_peek_window()
        if not w:
            return
        h, wx0, wy0 = w
        try:
            # souleve la fenetre de 45 px, sans focus / taille / z-order
            ctypes.windll.user32.SetWindowPos(h, 0, wx0, wy0 - 45, 0, 0, 0x15)
            self.peek = [h, wx0, wy0, 55]
        except Exception:
            self.peek = None

    def _end_peek(self):
        if not self.peek:
            return
        h, x0, y0 = self.peek[0], self.peek[1], self.peek[2]
        try:
            if ctypes.windll.user32.IsWindow(h):
                ctypes.windll.user32.SetWindowPos(h, 0, x0, y0, 0, 0, 0x15)  # repose
        except Exception:
            pass
        self.peek = None

    def _wander(self):
        if self.hover or self.peek:
            return
        if self.walk:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            dx = self.walk[0] - x
            dy = self.walk[1] - y
            if max(abs(dx), abs(dy)) <= 3:
                goal = self.walk_goal
                self.walk = None
                self.walk_goal = None
                self.walking = False
                self.walk_wait = random.randint(250, 700)
                self.covered = False
                self._cov_miss = 0  # re-evaluation propre apres la marche
                if goal == "icon":
                    self.selfie = 170  # "hey, c'est moi !"
                elif random.random() < 0.7:
                    self._start_peek()  # curieux : regarde derriere une fenetre
            else:
                self.root.geometry(f"+{x + max(-2, min(2, dx))}+{y + max(-2, min(2, dy))}")
                self.walking = True
                self.walk_dir = ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))
        else:
            self.walk_wait -= 1
            if self.walk_wait <= 0:
                x, y = self.root.winfo_x(), self.root.winfo_y()
                if random.random() < 0.45:
                    ip = find_shortcut_pos()  # va voir son propre raccourci
                    if ip:
                        self.walk = (ip[0] + 52, ip[1] - self.chh + 76)
                        self.walk_goal = "icon"
                        return
                mons = get_monitors() or [(0, 0, self.root.winfo_screenwidth(),
                                           self.root.winfo_screenheight())]
                rect = next((m for m in mons if m[0] <= x < m[2] and m[1] <= y < m[3]),
                            mons[0])
                self.walk = (random.randint(rect[0] + 10, max(rect[0] + 11, rect[2] - self.cw - 10)),
                             random.randint(rect[1] + 10, max(rect[1] + 11, rect[3] - self.chh - 60)))

    # ---------- boucle ----------
    def read_state(self):
        try:
            with open(STATE_FILE, encoding="utf-8", errors="ignore") as f:
                raw = f.read().strip().lower()
        except OSError:
            return
        parts = raw.split()
        s = parts[0] if parts else ""
        if s not in ("working", "idle", "question", "sleeping", "tired"):
            return
        # session fermee / inactive sans hook Stop : working perime -> idle
        if s == "working":
            try:
                if time.time() - os.path.getmtime(STATE_FILE) > 180:
                    s = "idle"
            except OSError:
                pass
        if s == "tired" and len(parts) > 1 and re.fullmatch(r"\d{1,2}:\d{2}", parts[1]):
            self.tired_until = parts[1]
        elif s != "tired":
            self.tired_until = None
        if s != self.state and self.cfg.get("sounds"):
            if s == "question":
                play_sound("question")
            elif s == "tired":
                play_sound("tired")
            elif s == "idle" and self.state == "working":
                play_sound("done")
        if s == "idle" and self.state == "working" and self.work_frames > 1500:
            self.party = 210  # longue session terminee -> confettis
            self._party_x = self.root.winfo_x()  # zone ecran fixe autour de sa position
        if s == "question" and self.state != "question":
            self.bounce_v = -6.0
        # le travail reprend pendant une balade -> il rentre a sa place en marchant
        if (IS_WIN and s in ("working", "question", "tired")
                and self.state in ("idle", "sleeping") and self.cfg.get("wander")):
            home = self.cfg.get("pos") or self._default_pos()
            here = (self.root.winfo_x(), self.root.winfo_y())
            if abs(home[0] - here[0]) + abs(home[1] - here[1]) > 30:
                self._end_peek()
                self.selfie = 0
                self.walk = home
                self.walk_goal = "home"
        self.state = s

    def remaining_text(self):
        """Temps restant avant la reinitialisation des tokens ('1h23' / '12 min')."""
        if not self.tired_until:
            return None
        try:
            h, mn = map(int, self.tired_until.split(":"))
            now = datetime.datetime.now()
            target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            delta = int((target - now).total_seconds()) // 60
            if delta >= 12 * 60:  # heure probablement deja passee -> incoherent
                return None
            if delta >= 60:
                return f"{delta // 60}h{delta % 60:02d}"
            return f"{max(1, delta)} min"
        except ValueError:
            return None

    def tick(self):
        if self._quit_flag:
            self._end_peek()
            if self.tray:
                self.tray.stop()
            self.root.destroy()
            return
        try:
            self._tick_body()
        except Exception:
            log_exc("tick")  # une erreur ne tue plus le pet
        self.root.after(33, self.tick)

    def _tick_body(self):
        if self._dirty:
            self._dirty = False
            self.apply_config()
        if self.move_mode:
            x = self.root.winfo_pointerx() - self.cw // 2
            y = self.root.winfo_pointery() - self.chh // 2
            self.root.geometry(f"+{x}+{y}")
        if self.annoyed > 0:
            self.annoyed -= 1
        if self.t % 300 == 0 and self.grab_count > 0:
            self.grab_count -= 1  # il oublie peu a peu qu'on l'a embete
        if self.selfie > 0:
            self.selfie -= 1
            if self.selfie % 45 == 0:
                self.bounce_v = -5.0  # saute de joie devant son raccourci
            if self.selfie % 55 == 0:
                self.hearts.append([self.root.winfo_x() + self.cw // 2 + random.randint(-10, 10),
                                    self.root.winfo_y() + 44, 0])  # position ecran fixe
        # fenetre soulevee : la reposer apres quelques secondes
        if self.peek:
            self.peek[3] -= 1
            if self.peek[3] <= 0:
                self._end_peek()
        if (IS_WIN and self.cfg.get("wander")
                and (self.state in ("idle", "sleeping") or self.walk_goal == "home")
                and self.cfg["visible"]
                and not self.move_mode and not self._dragging):
            # (il continue de se balader meme cache : il finit par ressortir)
            self._wander()
        else:
            self.walk = None
            self.walking = False
        self.t += 1
        if IS_WIN and self.t % 50 == 0 and self.fx_canvas is not None:
            # re-verifie le clic-transparent de l'overlay fx (au cas ou Tk recree la fenetre native)
            # cette fenetre couvre tout l'ecran : si le style saute, plus aucun clic ne passe.
            u = ctypes.windll.user32
            hwnd_fx = u.GetAncestor(self.fx_canvas.winfo_id(), 2)
            style = u.GetWindowLongW(hwnd_fx, -20) if hwnd_fx else 0
            if not hwnd_fx or not (style & 0x20):
                self._apply_fx_clickthrough(retries=20)
                if not self.fx_clickthrough_ok:
                    log_exc("fx-clickthrough-lost: destroying fx overlay")
                    try:
                        self.fx.destroy()
                    except Exception:
                        pass
                    self.fx = None
                    self.fx_canvas = None
        # mode "sur le bureau" : reste derriere toutes les fenetres
        if IS_WIN and self.cfg.get("layer") == "desktop":
            if self.t % 20 == 0:
                self.root.lower()
                ctypes.windll.user32.SetWindowPos(self._hwnd(), 1, 0, 0, 0, 0, 0x13)
            if self.cfg.get("eco") and self.t % 30 == 0:
                # eco : pause seulement apres 2 detections consecutives
                self._cov_miss = self._cov_miss + 1 if self._is_covered() else 0
                self.covered = self._cov_miss >= 2
        else:
            self.covered = False
        if self.t % 1000 == 500 and self.state != "tired":
            threading.Thread(target=self._scan_limit, daemon=True).start()
        # tired sans heure de reset : on abandonne apres 15 min
        if self.state == "tired" and not self.tired_until:
            self.tired_frames += 1
            if self.tired_frames > 27000:
                self.tired_frames = 0
                self.state = "idle"
        else:
            self.tired_frames = 0
        if self.t % 8 == 0:
            self.read_state()
            # reveil auto quand les tokens sont revenus
            if self.state == "tired" and self.tired_until and self.remaining_text() is None:
                self.tired_until = None
                self.state = "idle"
        # petites activites quand il ne fait rien (pas pendant une balade) ;
        # endormi, il se reveille aussi de temps en temps pour s'occuper
        if self.walk or self.peek or self.selfie > 0:
            self.activity = None
        elif self.state in ("idle", "sleeping"):
            if self.activity:
                self.act_t -= 1
                if self.act_t <= 0:
                    self.activity = None
                    self.next_act = random.randint(350, 800)
            else:
                self.next_act -= 1
                if self.next_act <= 0:
                    acts = ["book", "ball", "music", "coffee", "game",
                            "bug", "stretch", "paint"]
                    self.activity = random.choice(acts * 2 + ["nap"])  # sieste plus rare
                    self.act_t = random.randint(350, 650)
                    self.act_t0 = self.act_t
        else:
            self.activity = None
        self.work_frames = self.work_frames + 1 if self.state == "working" else 0
        if self.t % 150 == 0:
            try:
                with open(os.path.join(HERE, "hb.txt"), "w") as f:
                    f.write(f"t={self.t} state={self.state} covered={self.covered} "
                            f"vis={self.cfg['visible']} layer={self.cfg.get('layer')} "
                            f"pos={self.root.winfo_x()},{self.root.winfo_y()}")
            except OSError:
                pass
        busy = (self.walking or self.peek or self.selfie > 0
                or (self.activity and self.state in ("idle", "sleeping")))
        if self.cfg["visible"] and (not self.covered or busy):
            mx, my = self.root.winfo_pointerxy()
            wx, wy = self.root.winfo_x(), self.root.winfo_y()
            m = 100  # perimetre de detection autour du pet
            over = wx - m <= mx <= wx + self.cw + m and wy - m <= my <= wy + self.chh + m
            if over and not self.hover:
                self.bounce_v = -4.0  # sursaut de reveil
                if self.walk or self.walking:
                    # la souris l'interrompt : il s'arrete net pour te regarder
                    self.walk = None
                    self.walk_goal = None
                    self.walking = False
                    self.walk_wait = random.randint(120, 300)
            self.hover = over
            self.hover_frames = self.hover_frames + 1 if over else 0
            self.mouse = (mx, my)
            # apres un reveil, il se rendort en douceur (somnole d'abord)
            if self.state == "sleeping":
                if over:
                    self.drowsy = 150
                elif self.drowsy > 0:
                    self.drowsy -= 1
            self.draw()

    # ---------- rendu ----------
    def cell(self, gx, gy, w, h, color):
        s = self.s
        x = self.ox + gx * s
        y = self.oy + gy * s
        self.canvas.create_rectangle(x, y, x + w * s, y + h * s, fill=color, outline="")

    def draw(self):
        c = self.canvas
        c.delete("all")
        t, s, st = self.t, self.s, self.state
        # reveille le temps d'une activite / balade, meme si l'etat global dit "sleeping"
        if st == "sleeping" and (self.activity or self.walk or self.peek or self.selfie > 0):
            st = "idle"
        if self.walking:
            st = "idle"  # en marche : posture de balade, pas de laptop fige
        # transition douce : l'objet n'apparait qu'apres un temps de preparation
        elapsed = self.act_t0 - self.act_t
        act = self.activity if (elapsed > 45 and self.act_t > 30) else None

        # rebond (question)
        self.bounce_v += 0.8
        self.bounce_y = max(-16.0, min(0.0, self.bounce_y + self.bounce_v))
        if self.bounce_y >= 0:
            self.bounce_v = 0.0
        if st == "question" and t % 50 == 0:
            self.bounce_v = -5.0

        if st == "working":
            bob = round(math.sin(t * 0.45) * 1.5)
        elif st == "sleeping":
            bob = round(math.sin(t * 0.05) * 2)
        elif st == "tired":
            bob = round(math.sin(t * 0.04) * 2.5)  # respiration lourde et lente
        elif st == "idle" and act == "music":
            bob = round(math.sin(t * 0.28) * 2)  # se dandine en rythme
        else:
            bob = round(math.sin(t * 0.09) * 2)
        if st == "idle" and self.activity == "nap":
            ph = t % 200
            if ph < 150:
                bob += min(3, ph // 30)  # pique du nez petit a petit
            elif ph == 150:
                self.bounce_v = -4.0  # sursaute et se reveille
        bob += int(self.bounce_y)

        self.ox = 8
        self.oy = 46 + bob
        self._body_dy = bob
        if self.annoyed > 140:
            self.ox += round(math.sin(t * 1.5) * 2)  # secoue la tete, agace
        if st == "idle" and act == "stretch":
            ph = (t // 50) % 4
            if ph == 0:
                self.ox -= 2  # penche a gauche
            elif ph == 1:
                self.ox += 2  # penche a droite
            elif ph == 3 and t % 50 == 0:
                self.bounce_v = -5.0  # petit saut
        ground = 46 + GH * s
        if st == "tired":
            self.oy = 46 + 2 * s + bob  # avachi par terre, pattes repliees
        if st == "idle" and self.activity == "ball":
            self.ball = (self.ox + GW * s + 22 + round(math.sin(t * 0.05) * 8),
                         ground - 4 - abs(math.sin(t * 0.11)) * 3 * s)
        if st == "idle" and self.activity == "bug":
            self.bugx = self.cw - ((t * 1.2) % (self.cw + 30)) - 15
            if abs(self.bugx - (self.ox + GW * s // 2)) < s and t % 12 == 0:
                self.bounce_v = -3.0  # l'insecte passe sous lui, il sursaute

        # pattes (fixes au sol)
        if st != "tired":
            for i, c1 in enumerate(LEGS):
                x1 = self.ox + c1 * s
                y1 = self.oy + BODY_H * s
                lift = s // 2 if self.walking and (i + t // 5) % 2 else 0
                c.create_rectangle(x1, y1, x1 + s, ground - lift, fill=BASE, outline="")

        # corps
        for r in range(BODY_H):
            for ci in range(GW):
                col = cell_color(r, ci)
                if col:
                    self.cell(ci, r, 1, 1, col)

        # yeux
        self.blink -= 1
        if self.blink < -self.next_blink:
            self.blink = 4
            self.next_blink = random.randint(60, 160)
        shy = self.hover and self.hover_frames > 240  # trop de calins -> timide
        weary = st == "working" and self.work_frames > 2700 and t % 400 < 110
        drowsy_half = st == "sleeping" and not self.hover and 0 < self.drowsy < 70
        closed = (st == "sleeping" and not self.hover and self.drowsy <= 0) or self.blink > 0
        if st == "idle" and not self.hover:
            if self.activity == "nap" and t % 200 < 150:
                closed = True
            elif self.activity == "coffee" and t % 130 < 25:
                closed = True  # savoure sa gorgee
        if st in ("idle", "question") and t % self.next_glance == 0:
            self.glance = random.choice([-1, 0, 0, 1])
            self.next_glance = random.randint(90, 240)
        if self.hover:
            # reveille : les yeux suivent la souris
            mx, my = self.mouse
            cx = self.root.winfo_x() + self.ox + (GW * s) // 2
            cy = self.root.winfo_y() + self.oy + 2 * s
            lim = max(1, s // 2)
            dxp = max(-lim, min(lim, (mx - cx) // 6))
            dyp = max(-lim, min(lim, (my - cy) // 6))
        elif self.walking:
            # regarde dans la direction de sa marche
            dxp = self.walk_dir[0] * (s // 2)
            dyp = self.walk_dir[1] * (s // 2)
        elif self.peek:
            dxp, dyp = 0, -(s // 2)  # regarde en haut, derriere la fenetre
        elif self.selfie > 0:
            dxp, dyp = -(s // 2), s // 2  # regarde son raccourci en bas a gauche
        elif st == "idle" and self.activity in ("book", "game", "coffee"):
            dxp, dyp = 0, s // 2  # yeux baisses sur l'objet
        elif st == "idle" and self.activity == "ball":
            dxp = s // 2  # suit le ballon a droite
            dyp = 0 if self.ball[1] < ground - 2 * s else s // 2
        elif st == "idle" and self.activity == "bug":
            dxp = -(s // 2) if self.bugx < self.ox + GW * s // 2 else s // 2
            dyp = s // 2  # suit l'insecte au sol
        elif st == "idle" and self.activity == "paint":
            dxp, dyp = s // 2, 0  # regarde sa toile
        else:
            dxp = self.glance * (s // 2) if st in ("idle", "question") else 0
            dyp = s // 2 if st == "working" else 0
        # yeux = trous transparents comme dans le modele (couleur sombre sur mac)
        for ex, ey in EYES:
            x = self.ox + ex * s + dxp
            y = self.oy + ey * s + dyp
            if shy:
                # se cache les yeux avec ses pattes
                bx0 = self.ox + ex * s
                by0 = self.oy + ey * s
                c.create_rectangle(bx0 - 2, by0 - 2, bx0 + s + 2, by0 + s + 2,
                                   fill=BASE, outline=DARK, width=1)
                continue
            if (st == "tired" and not self.hover) or drowsy_half or weary or self.annoyed > 0:
                c.create_rectangle(x, y + s // 3, x + s, y + s, fill=self.hole, outline="")  # mi-clos
            elif closed:
                pass  # paupieres fermees = trou rebouche
            else:
                c.create_rectangle(x, y, x + s, y + s, fill=self.hole, outline="")

        if shy:
            for cxc in (1, 8):  # joues qui rougissent
                c.create_rectangle(self.ox + cxc * s + s // 2, self.oy + 3 * s + 2,
                                   self.ox + (cxc + 1) * s + s // 2, self.oy + 3 * s + s // 2 + 3,
                                   fill="#E8938C", outline="")

        gx2 = self.ox + GW * s
        top = 46

        # ----- travail : laptop + paws + code qui defile -----
        if st == "working":
            lx1 = self.ox + 1 * s
            lx2 = self.ox + 8 * s
            sy1 = top + 4 * s
            sy2 = top + 7 * s
            c.create_rectangle(lx1, sy1, lx2, sy2, fill="#4A4A4A", outline="")
            c.create_rectangle(lx1 + 2, sy1 + 2, lx2 - 2, sy2 - 2, fill="#1B1B1B", outline="")
            palette = ["#7EC699", "#6FA8DC", "#E8A87C", "#CFCFCF"]
            inner_w = (lx2 - lx1) - 8
            lh = max(1, s // 3)
            for i in range(3):
                prog = ((t * 3 + i * 40) % 120) / 120
                w = int(inner_w * min(1.0, prog * 1.6))
                if w > 1:
                    y = sy1 + 4 + i * (lh + max(2, s // 2))
                    c.create_rectangle(lx1 + 4, y, lx1 + 4 + w, y + lh,
                                       fill=palette[(i + t // 120) % 4], outline="")
            ky1 = sy2
            ky2 = sy2 + int(1.2 * s)
            c.create_rectangle(lx1 - s, ky1, lx2 + s, ky2, fill="#5A5A5A", outline="")
            c.create_rectangle(lx1 - s, ky1, lx2 + s, ky1 + 2, fill="#333333", outline="")
            if weary:
                # ras-le-bol : pattes posees, soupir
                c.create_rectangle(lx1, ky1 - s // 2, lx1 + s, ky1 + s // 2, fill=BASE, outline="")
                c.create_rectangle(lx2 - s, ky1 - s // 2, lx2, ky1 + s // 2, fill=BASE, outline="")
                sx = gx2 + 6 + (t % 110) * 0.2
                sy = top - 6 - (t % 110) * 0.15
                c.create_text(sx, sy, text="...", fill="#B08268", font=("Consolas", 11, "bold"))
            else:
                tap = (t // 4) % 2
                paw_y1 = ky1 - s + (0 if tap == 0 else s // 2)
                paw_y2 = ky1 - s + (s // 2 if tap == 0 else 0)
                c.create_rectangle(lx1, paw_y1, lx1 + s, paw_y1 + s, fill=BASE, outline="")
                c.create_rectangle(lx2 - s, paw_y2, lx2, paw_y2 + s, fill=BASE, outline="")
        elif st == "question":
            qy = top - 24 + math.sin(t * 0.15) * 3
            c.create_rectangle(gx2 - 6, qy - 14, gx2 + 26, qy + 12, fill="#FFF6EF", outline=DARK, width=2)
            c.create_polygon(gx2 - 2, qy + 10, gx2 - 8, qy + 20, gx2 + 8, qy + 11,
                             fill="#FFF6EF", outline=DARK, width=2)
            c.create_text(gx2 + 10, qy - 1, text="?", fill=BASE, font=("Consolas", 13, "bold"))
        elif st == "idle" and act == "book":
            bx1 = self.ox + 2 * s
            bx2 = self.ox + 9 * s
            mid = (bx1 + bx2) // 2
            by1 = self.oy + 4 * s
            by2 = self.oy + 6 * s + s // 2
            c.create_rectangle(bx1, by1, mid, by2, fill="#FFF6EF", outline=DARK, width=2)
            c.create_rectangle(mid, by1, bx2, by2, fill="#FFF6EF", outline=DARK, width=2)
            lh = max(1, s // 3)
            seed = (t // 110) % 3  # les lignes changent a chaque page tournee
            for i in range(3):
                y = by1 + 4 + i * (lh + 3)
                w = (mid - bx1 - 10) * (2 + (i + seed) % 3) // 4
                c.create_rectangle(bx1 + 5, y, bx1 + 5 + w, y + lh, fill="#C9A28E", outline="")
                w2 = (bx2 - mid - 10) * (2 + (i + seed + 1) % 3) // 4
                c.create_rectangle(mid + 5, y, mid + 5 + w2, y + lh, fill="#C9A28E", outline="")
            flip = t % 110
            if flip < 10:  # page qui se tourne
                fx = bx2 - (bx2 - mid) * flip // 10
                c.create_rectangle(mid, by1, max(mid + 2, fx), by2, fill="#FFE9DC", outline=DARK)
            # pattes qui tiennent le livre
            c.create_rectangle(bx1 - s // 2, by1 - 2, bx1 + s // 2, by1 + s - 2, fill=BASE, outline="")
            c.create_rectangle(bx2 - s // 2, by1 - 2, bx2 + s // 2, by1 + s - 2, fill=BASE, outline="")
        elif st == "idle" and act == "ball":
            bx, by = self.ball
            r_ = max(4, s - 1)
            c.create_oval(bx - r_, by - r_, bx + r_, by + r_, fill="#D9534F", outline="#A93A38")
            c.create_line(bx - r_, by, bx + r_, by, fill="#FFF6EF", width=2)
            # patte qui frappe quand le ballon est bas
            if by > ground - int(1.5 * s):
                c.create_rectangle(gx2 - s // 2, self.oy + 4 * s, gx2 + s // 2,
                                   self.oy + 5 * s, fill=BASE, outline="")
        elif st == "idle" and act == "coffee":
            sip = t % 130 < 25
            cy_ = self.oy + (3 if sip else 4) * s  # leve la tasse pour boire
            cx1 = self.ox + 5 * s
            c.create_rectangle(cx1, cy_, cx1 + 2 * s, cy_ + 2 * s,
                               fill="#FFF6EF", outline=DARK, width=2)
            c.create_rectangle(cx1 + 2 * s + 1, cy_ + s // 2, cx1 + 2 * s + s // 2 + 2,
                               cy_ + 3 * s // 2, outline=DARK, width=2, fill="")
            c.create_rectangle(cx1 + 3, cy_ + 3, cx1 + 2 * s - 3, cy_ + s - 1,
                               fill="#6B4226", outline="")
            if not sip:
                for i in range(2):  # vapeur qui monte
                    svy = cy_ - 5 - ((t * 0.4 + i * 9) % 16)
                    svx = cx1 + s // 2 + i * s + math.sin(t * 0.2 + i) * 2
                    c.create_oval(svx - 2, svy - 2, svx + 2, svy + 2, fill="#DDD5CE", outline="")
            c.create_rectangle(cx1 - s // 2, cy_ + s, cx1 + s // 3, cy_ + 2 * s,
                               fill=BASE, outline="")
        elif st == "idle" and act == "game":
            rage = t % 170 > 140
            shake = round(math.sin(t * 1.4) * 2) if rage else 0
            gp1 = self.ox + 3 * s + shake
            gpy = self.oy + 4 * s
            c.create_rectangle(gp1, gpy, gp1 + 5 * s, gpy + 2 * s,
                               fill="#4A4A4A", outline="#333333", width=2)
            c.create_rectangle(gp1 + s // 2, gpy + s - s // 4, gp1 + s + s // 2,
                               gpy + s + s // 4, fill="#222222", outline="")
            c.create_rectangle(gp1 + s - s // 4, gpy + s // 2, gp1 + s + s // 4,
                               gpy + 3 * s // 2, fill="#222222", outline="")
            c.create_oval(gp1 + 4 * s - 2, gpy + s // 2, gp1 + 4 * s + s // 2,
                          gpy + s, fill="#D9534F", outline="")
            c.create_oval(gp1 + 3 * s + 2, gpy + s, gp1 + 3 * s + s // 2 + 4,
                          gpy + 3 * s // 2, fill="#7EC699", outline="")
            tap = (t // 3) % 2
            c.create_rectangle(gp1 - s // 2, gpy - s + (0 if tap else s // 2),
                               gp1 + s // 4, gpy + s // 4, fill=BASE, outline="")
            c.create_rectangle(gp1 + 5 * s - s // 4, gpy - s + (s // 2 if tap else 0),
                               gp1 + 5 * s + s // 2, gpy + s // 4, fill=BASE, outline="")
            if rage:  # secoue la manette
                c.create_text(gx2 + 12, top - 4, text="!", fill="#D9534F",
                              font=("Consolas", 13, "bold"))
        elif st == "idle" and act == "bug":
            bx = self.bugx
            wig = (t // 5) % 2
            c.create_rectangle(bx, ground - 4, bx + 6, ground - 1, fill="#5B4636", outline="")
            c.create_rectangle(bx - 2 + wig, ground - 2, bx + wig, ground, fill="#5B4636", outline="")
            c.create_rectangle(bx + 6 - wig, ground - 2, bx + 8 - wig, ground, fill="#5B4636", outline="")
        elif st == "idle" and act == "stretch":
            if (t // 50) % 4 == 2:  # bras leves
                c.create_rectangle(self.ox + s, self.oy - s, self.ox + 2 * s,
                                   self.oy, fill=BASE, outline="")
                c.create_rectangle(self.ox + 9 * s, self.oy - s, self.ox + 10 * s,
                                   self.oy, fill=BASE, outline="")
        elif st == "idle" and act == "paint":
            ex1, ey1 = gx2 + 8, top + s
            ex2, ey2 = min(gx2 + 44, self.cw - 4), top + s + 26
            c.create_line(ex1 + 4, ey2, ex1 - 2, ground, fill="#8A6B4F", width=2)
            c.create_line(ex2 - 4, ey2, ex2 + 2, ground, fill="#8A6B4F", width=2)
            c.create_rectangle(ex1, ey1, ex2, ey2, fill="#FFF6EF", outline="#8A6B4F", width=2)
            palette = ["#D9534F", "#7EC699", "#6FA8DC", "#F0C674"]
            n = min(24, (self.act_t0 - self.act_t) // 12)
            for k in range(n):  # la toile se remplit peu a peu
                pxk = ex1 + 3 + (k % 6) * ((ex2 - ex1 - 6) // 6)
                pyk = ey1 + 3 + (k // 6) * ((ey2 - ey1 - 6) // 4)
                c.create_rectangle(pxk, pyk, pxk + 4, pyk + 4,
                                   fill=palette[(k * 7) % 4], outline="")
            c.create_rectangle(gx2 - s // 2, self.oy + 3 * s, gx2 + s // 2,
                               self.oy + 4 * s, fill=BASE, outline="")
            c.create_line(gx2 + s // 2, self.oy + 3 * s + s // 2,
                          ex1 + 3 + (n % 6) * 5, ey1 + 6 + (n // 6) * 5,
                          fill="#8A6B4F", width=2)
        elif st == "idle" and act == "music":
            # casque audio
            hy = self.oy - s // 2
            c.create_rectangle(self.ox + 2 * s, hy - 2, self.ox + 9 * s, hy + s // 2,
                               fill="#4A4A4A", outline="")
            c.create_rectangle(self.ox - 2, self.oy + s, self.ox + s - 2,
                               self.oy + 3 * s, fill="#4A4A4A", outline="")
            c.create_rectangle(self.ox + 10 * s + 2, self.oy + s, self.ox + 11 * s + 2,
                               self.oy + 3 * s, fill="#4A4A4A", outline="")
            for i in range(3):
                nx = gx2 + 8 + i * 12 + math.sin(t * 0.09 + i * 2) * 3
                ny = top + 16 - ((t * 0.5 + i * 16) % 48)
                c.create_text(nx, ny, text="♪" if i % 2 else "♫", fill=DARK,
                              font=("Consolas", 11, "bold"))
        elif st == "sleeping" and not self.hover and self.drowsy <= 0:
            for i, size in enumerate((8, 11, 14)):
                zx = gx2 + 4 + i * 10 + math.sin(t * 0.05 + i) * 2
                zy = top - 2 - i * 13 - (t * 0.25 % 13)
                c.create_text(zx, zy, text="z", fill="#B08268", font=("Consolas", size, "bold"))
        elif st == "tired":
            # batterie vide qui clignote au-dessus
            bw, bh = 5 * s, 2 * s + 2
            bx = self.ox + (GW * s - bw) // 2
            by = top - bh - 6
            c.create_rectangle(bx, by, bx + bw, by + bh, fill="#2B2020", outline="#8A8A8A", width=2)
            c.create_rectangle(bx + bw, by + bh // 4, bx + bw + 4, by + 3 * bh // 4,
                               fill="#8A8A8A", outline="")
            if (t // 15) % 2 == 0:
                c.create_rectangle(bx + 3, by + 3, bx + 3 + max(2, bw // 8), by + bh - 3,
                                   fill="#D9534F", outline="")
            # timer : temps restant avant le retour des tokens
            rem = self.remaining_text()
            if rem:
                c.create_text(bx + bw // 2, max(8, by - 9), text=rem,
                              fill="#FFF6EF", font=("Consolas", 10, "bold"))
            # goutte de sueur qui glisse le long de la tete
            drop = (t * 0.5) % (4 * s)
            dx0 = self.ox + 10 * s + s // 2
            dy0 = self.oy + s + drop
            c.create_oval(dx0 - 2, dy0 - 3, dx0 + 2, dy0 + 3, fill="#7EB8DC", outline="")
            # haletement : petite bouche qui s'ouvre (trou comme les yeux)
            if (t // 12) % 2 == 0:
                self.cell(5, 4, 1, 1, self.hole)

        if self.selfie > 0:
            # salue son raccourci de la patte, tout excite
            wave = (t // 7) % 2
            c.create_rectangle(self.ox + 10 * s, self.oy - (s if wave else s // 2),
                               self.ox + 11 * s, self.oy + s // 2, fill=BASE, outline="")
            if self.selfie % 60 > 35:
                c.create_text(self.ox + GW * s // 2, top - 14, text="!",
                              fill=BASE, font=("Consolas", 15, "bold"))

        if self.annoyed > 0 and (t // 10) % 3:
            # marque d'agacement pixel au-dessus de la tete
            ax = self.ox + (GW - 1) * s
            ay = top - 16
            for rr, cc in ((0, 0), (0, 2), (1, 1), (2, 0), (2, 2)):
                c.create_rectangle(ax + cc * 4, ay + rr * 4, ax + cc * 4 + 3,
                                   ay + rr * 4 + 3, fill="#D9534F", outline="")

        if self.peek:  # bras leves pour soulever la fenetre
            c.create_rectangle(self.ox + s, self.oy - s, self.ox + 2 * s,
                               self.oy, fill=BASE, outline="")
            c.create_rectangle(self.ox + 9 * s, self.oy - s, self.ox + 10 * s,
                               self.oy, fill=BASE, outline="")

        now = datetime.datetime.now()
        # bonnet de nuit apres 23h
        if st == "sleeping" and (now.hour >= 23 or now.hour < 7):
            cap = "#3B4A6B"
            c.create_rectangle(self.ox + s, self.oy - s, self.ox + 10 * s, self.oy + 2, fill=cap, outline="")
            c.create_rectangle(self.ox + 3 * s, self.oy - 2 * s, self.ox + 9 * s, self.oy - s, fill=cap, outline="")
            c.create_rectangle(self.ox + 6 * s, self.oy - 3 * s, self.ox + 10 * s, self.oy - 2 * s, fill=cap, outline="")
            c.create_oval(self.ox + 10 * s - 2, self.oy - 3 * s - 3, self.ox + 10 * s + 6,
                          self.oy - 3 * s + 5, fill="#FFF6EF", outline="")
        # accessoires de saison
        if st in ("idle", "question") and not self.hover:
            if now.month in (12, 1, 2):  # echarpe en hiver
                c.create_rectangle(self.ox, self.oy + 5 * s, self.ox + GW * s,
                                   self.oy + 6 * s, fill="#B33A3A", outline="")
                c.create_rectangle(self.ox + 2 * s, self.oy + 6 * s, self.ox + 3 * s,
                                   self.oy + 7 * s + s // 2, fill="#B33A3A", outline="")
                c.create_rectangle(self.ox + 2 * s, self.oy + 7 * s + s // 2 - 2,
                                   self.ox + 3 * s, self.oy + 7 * s + s // 2 + 2,
                                   fill="#8A2C2C", outline="")

        self._draw_fx(t)

    def _draw_fx(self, t):
        """Particules (confettis, coeurs) dans une fenetre a part, en coords ecran
        fixes : elles restent a leur place meme si le pet bouge ou est deplace."""
        fc = self.fx_canvas
        if fc is None:
            return
        fc.delete("all")

        if self.party > 0:
            self.party -= 1
            if self.party % 45 == 0:
                self.bounce_v = -6.0  # saute de joie
            cols = ["#D9534F", "#7EC699", "#6FA8DC", "#F0C674"]
            px0 = self._party_x
            for k in range(18):
                cx0 = px0 + (k * 37 + (k % 5) * 11) % self.cw
                spd = 1.2 + (k % 3) * 0.7
                cy0 = self.root.winfo_y() + ((t * spd + k * 53) % (self.chh + 20)) - 10
                fc.create_rectangle(cx0, cy0, cx0 + 4, cy0 + 4, fill=cols[k % 4], outline="")

        # coeurs de caresses (pixel-art, fondu progressif)
        heart_px = ["0110110", "1111111", "1111111", "0111110", "0011100", "0001000"]
        alive = []
        for h_ in self.hearts:
            h_[2] += 1
            if h_[2] < 45:
                alive.append(h_)
                age = h_[2]
                col = "#E8556A" if age < 18 else ("#EF8FA0" if age < 32 else "#F7C6CE")
                p = 2 if age < 25 else 1  # retrecit en s'envolant
                hx0 = h_[0] - 7 * p // 2 + round(math.sin(age * 0.25) * 2)
                hy0 = h_[1] - age * 1.1 - 6 * p
                for rr, row in enumerate(heart_px):
                    for cc, v in enumerate(row):
                        if v == "1":
                            fc.create_rectangle(hx0 + cc * p, hy0 + rr * p,
                                                hx0 + (cc + 1) * p, hy0 + (rr + 1) * p,
                                                fill=col, outline="")
        self.hearts = alive


if __name__ == "__main__":
    if "--make-icon" in sys.argv:
        make_icon()
        sys.exit(0)
    try:
        _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _lock.bind(("127.0.0.1", 47823))
    except OSError:
        sys.exit(0)
    Pet()
