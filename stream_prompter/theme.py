"""Dark ttk theme."""

import tkinter.font as tkfont
from tkinter import ttk

BG, PANEL, FIELD, BORDER = "#17181d", "#22242b", "#2b2e37", "#3a3d48"
FG, MUTED = "#e8e9ed", "#9095a1"
ACCENT, ACCENT_HOVER = "#7c5cff", "#9177ff"
GREEN, GRAY_BAR, BLUE, YELLOW, RED = "#3ecf8e", "#4b5060", "#5aa9ff", "#ffcc4d", "#ff6b6b"


def setup_theme(root) -> dict:
    base = tkfont.nametofont("TkDefaultFont")
    base.configure(size=10)
    family = base.actual("family")
    fonts = {
        "bold": (family, 10, "bold"),
        "small": (family, 8),
        "big": (family, 17, "bold"),
        "state": (family, 10, "bold"),
    }
    root.configure(bg=BG)
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=BORDER,
                 lightcolor=BORDER, darkcolor=BORDER, troughcolor=FIELD, focuscolor=ACCENT,
                 selectbackground=ACCENT, selectforeground="#ffffff", insertcolor=FG,
                 arrowcolor=FG, font=base)
    st.configure("TFrame", background=BG)
    st.configure("Panel.TFrame", background=PANEL)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Muted.TLabel", foreground=MUTED)
    st.configure("Warn.TLabel", foreground=RED)
    st.configure("Small.TLabel", foreground=MUTED, font=fonts["small"])
    st.configure("Panel.TLabel", background=PANEL, foreground=FG)
    st.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
    st.configure("PanelWarn.TLabel", background=PANEL, foreground=RED)
    st.configure("Talk.TLabel", foreground=GREEN, font=fonts["state"])
    st.configure("Silent.TLabel", foreground=MUTED, font=fonts["state"])
    st.configure("Paused.TLabel", foreground=YELLOW, font=fonts["state"])
    st.configure("NoMic.TLabel", foreground=RED, font=fonts["state"])
    st.configure("TLabelframe", background=BG, bordercolor=BORDER, relief="solid")
    st.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=fonts["bold"])

    st.configure("TButton", background=PANEL, foreground=FG, bordercolor=BORDER, padding=(10, 4))
    st.map("TButton", background=[("disabled", BG), ("pressed", BORDER), ("active", FIELD)],
           foreground=[("disabled", MUTED)])
    st.configure("Small.TButton", padding=(8, 1))
    st.configure("Accent.TButton", background=ACCENT, bordercolor=ACCENT, foreground="#ffffff")
    st.map("Accent.TButton", background=[("pressed", ACCENT), ("active", ACCENT_HOVER)])

    for name, bg in (("TCheckbutton", BG), ("Panel.TCheckbutton", PANEL)):
        st.configure(name, background=bg, foreground=FG, indicatorbackground=FIELD,
                     indicatorforeground="#ffffff", indicatormargin=(0, 0, 8, 0))
        st.map(name, background=[("active", bg)],
               indicatorbackground=[("selected", ACCENT), ("active", BORDER)])

    st.configure("TCombobox", fieldbackground=FIELD, background=PANEL, foreground=FG, padding=4)
    st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
           selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
           background=[("active", FIELD)])
    st.configure("TEntry", fieldbackground=FIELD, foreground=FG, padding=4)
    st.map("TEntry", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
    st.configure("TSpinbox", fieldbackground=FIELD, background=PANEL, foreground=FG, padding=4)
    st.configure("Horizontal.TScale", background=ACCENT, troughcolor=FIELD, bordercolor=BORDER)
    st.map("Horizontal.TScale", background=[("active", ACCENT_HOVER)])
    st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=FIELD,
                 bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)
    st.configure("Vertical.TScrollbar", background=FIELD, troughcolor=PANEL, bordercolor=PANEL,
                 arrowcolor=MUTED, lightcolor=FIELD, darkcolor=FIELD)
    st.map("Vertical.TScrollbar", background=[("active", BORDER)])

    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    return fonts
