#!/usr/bin/env python3
"""
cheque_editor.py — Visual editor for VKR invoice/receipt JSON files (Slovenia).

Features:
  - Browse and edit JSON files in a selected folder
  - Navigate between files with arrow buttons or dropdown
  - DA/NE toggle for VAT taxpayer fields
  - Auto-numbered item rows (1-9) with add/remove controls
  - Create new empty invoice files from template
  - Save with Cmd+S, scale UI with Cmd+/−/0
  - Folder selection via GUI dialog

Usage:
    python3 cheque_editor.py
    python3 cheque_editor.py --dir /path/to/json/folder
"""

import copy
import json
import re
import argparse
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from pathlib import Path

# --- Colors ---

C_HEADER_BG = "#1a6b8a"
C_ROW_LIGHT = "#ddeef5"
C_ROW_DARK  = "white"
C_LABEL     = "#1a6b8a"
C_BORDER    = "#8ab8cc"
C_BG        = "#f4f9fc"

# Base scale factor — all sizes are multiplied by SCALE
SCALE = 1.0

# Item table constraints
MIN_ROWS       = 5
MAX_EXTRA_ROWS = 4
MAX_ROWS       = MIN_ROWS + MAX_EXTRA_ROWS  # 9

# Empty invoice template for creating new files
EMPTY_TEMPLATE = {
    "racun": {
        "dobavitelj": {
            "ime_in_naslov": "",
            "davčni_zavezanec_za_DDV": "",
            "ID_za_DDV_DS": ""
        },
        "kupec": {
            "ime_in_naslov": "",
            "davčni_zavezanec_za_DDV": "",
            "ID_za_DDV_DS": "",
            "racun_st": "",
            "kraj_in_datum_izdaje": {
                "kraj": "",
                "datum": ""
            }
        },
        "postavke": [],
        "drugi_podatki": {
            "opomba_DDV_izjema": {
                "tocka": "",
                "odstavek": "",
                "clen_ZDDV1": ""
            }
        },
        "skupaj": {
            "skupaj_vrednost_EUR": "",
            "osnova_DDV_1": {"stopnja_procent": "", "znesek_DDV": ""},
            "osnova_DDV_2": {"stopnja_procent": "", "znesek_DDV": ""},
            "datum_predplacila": "",
            "predplacilo_EUR": "",
            "skupaj_za_placilo_EUR": ""
        },
        "meta": {
            "zap_st_seta": "",
            "izdajatelj_zaloznik": "",
            "serijska_st_vezane_knjige_racunov": ""
        }
    }
}


def sc(n):
    """Scale a number (size, padding) by the current scale factor."""
    return max(1, int(n * SCALE))


def sf(family, size, *style):
    """Scale a font specification by the current scale factor."""
    return (family, max(6, int(size * SCALE))) + style


# Item table columns: (key, header, character width)
POSTAVKE_COLS = [
    ("zap_st",                   "Zap. št.",                 4),
    ("datum_dobave",             "Datum dobave",             9),
    ("vrsta_blaga_storitve",     "VRSTA BLAGA / STORITVE",   28),
    ("kolicina_in_merska_enota", "Količina in merska enota", 9),
    ("cena_na_enoto_brez_DDV",   "Cena brez DDV",            9),
    ("znesek_znizanja_popust",   "Znižanje/Popust",          8),
    ("vrednost_brez_DDV",        "Vrednost brez DDV",        9),
    ("DDV_stopnja",              "DDV stopnja",              7),
    ("DDV_znesek",               "DDV znesek",               8),
    ("vrednost_z_DDV",           "Vrednost z DDV",           8),
]

# --- Editor ---


class ChequeEditor:
    def __init__(self, root: tk.Tk, json_dir: Path):
        self.root      = root
        self.json_dir  = json_dir
        self.data      = {}
        self.modified  = False
        self.current_file: Path | None = None
        self.file_list: list[str] = []

        # Flag to suppress edit events during programmatic widget updates
        self._suppress_edit = False

        self.postavke_rows:  list[dict]       = []
        self.postavke_frame: tk.Frame | None  = None
        self.btn_add_row:    tk.Button | None = None
        self.btn_remove_row: tk.Button | None = None

        self._setup_window()
        self._build_ui()
        self._load_file_list()
        self._bind_keys()

    # -- Window setup --

    def _setup_window(self):
        self.root.title("VKR Invoice Editor")
        self.root.configure(bg=C_BG)
        self.root.geometry("1060x900")
        self.root.minsize(700, 500)

    # -- Key bindings --

    def _bind_keys(self):
        # Cmd+S / Ctrl+S — save
        self.root.bind_all("<Command-s>", lambda e: (self._save(), "break"))
        self.root.bind_all("<Control-s>", lambda e: (self._save(), "break"))

        # UI scale shortcuts
        self.root.bind_all("<Command-equal>",  lambda e: self._rescale(1.1))
        self.root.bind_all("<Command-minus>",  lambda e: self._rescale(0.9))
        self.root.bind_all("<Command-0>",      lambda e: self._rescale(None))
        self.root.bind_all("<Control-equal>",  lambda e: self._rescale(1.1))
        self.root.bind_all("<Control-minus>",  lambda e: self._rescale(0.9))
        self.root.bind_all("<Control-0>",      lambda e: self._rescale(None))

        # Click outside input fields — release focus
        self.root.bind_all("<Button-1>", self._on_click_defocus, add="+")

    def _on_click_defocus(self, event):
        """Release focus from Entry/Text when clicking outside them.

        Skips if the click is inside a dialog (Toplevel) window,
        so that simpledialog / messagebox buttons work normally.
        """
        widget = event.widget
        if isinstance(widget, (tk.Entry, tk.Text, ttk.Combobox)):
            return
        # Don't steal focus from dialog windows (Toplevel)
        try:
            toplevel = widget.winfo_toplevel()
            if toplevel is not self.root:
                return
        except tk.TclError:
            return
        self.root.focus_set()

    # -- Rescaling --

    def _rescale(self, factor):
        global SCALE
        if factor is None:
            SCALE = 1.0
        else:
            SCALE = max(0.5, min(2.5, SCALE * factor))

        # Rebuild entire UI while preserving current file
        for widget in self.root.winfo_children():
            widget.destroy()
        self._build_ui()
        self._load_file_list_preserving()
        self._bind_keys()

    def _load_file_list_preserving(self):
        """Reload file list while keeping the current file selected."""
        current_name = self.current_file.name if self.current_file else None
        self._load_file_list(select_name=current_name)

    # -- Main UI layout --

    def _build_ui(self):
        # -- Top bar --
        topbar = tk.Frame(self.root, bg=C_HEADER_BG, pady=sc(6))
        topbar.pack(fill="x", side="top")

        # Folder selection button (opens system folder picker)
        tk.Button(
            topbar, text="📁 Mapa", command=self._choose_folder,
            bg="#0d4f6b", fg="black", activeforeground="#999999",
            font=sf("Helvetica", 10, "bold"),
            relief="flat", padx=sc(8), pady=sc(2), cursor="hand2"
        ).pack(side="left", padx=(sc(12), sc(6)))

        # Previous file button
        self.btn_prev = tk.Button(
            topbar, text="‹", command=self._prev_file,
            bg="#0d4f6b", fg="black", activeforeground="#999999",
            font=sf("Helvetica", 14, "bold"),
            relief="flat", padx=sc(6), pady=0, cursor="hand2", width=2
        )
        self.btn_prev.pack(side="left", padx=(0, sc(2)))

        # File selector dropdown
        self.file_var   = tk.StringVar()
        self.file_combo = ttk.Combobox(
            topbar, textvariable=self.file_var,
            width=38, font=sf("Helvetica", 11), state="readonly"
        )
        self.file_combo.pack(side="left", padx=sc(2))
        self.file_combo.bind("<<ComboboxSelected>>", self._on_file_selected)

        # Next file button
        self.btn_next = tk.Button(
            topbar, text="›", command=self._next_file,
            bg="#0d4f6b", fg="black", activeforeground="#999999",
            font=sf("Helvetica", 14, "bold"),
            relief="flat", padx=sc(6), pady=0, cursor="hand2", width=2
        )
        self.btn_next.pack(side="left", padx=(sc(2), sc(8)))

        # Save/edit status indicator
        self.status_label = tk.Label(
            topbar, text="", bg=C_HEADER_BG, fg="white",
            font=sf("Helvetica", 11, "bold"), width=10
        )
        self.status_label.pack(side="left", padx=sc(4))

        # File counter (X / Y)
        self.counter_label = tk.Label(
            topbar, text="", bg=C_HEADER_BG, fg="#aaddee",
            font=sf("Helvetica", 9)
        )
        self.counter_label.pack(side="left", padx=sc(4))

        # Create new file button
        tk.Button(
            topbar, text="+ Nova", command=self._create_new_file,
            bg="#1a8a5a", fg="black", activeforeground="#999999",
            font=sf("Helvetica", 10, "bold"),
            relief="flat", padx=sc(8), pady=sc(2), cursor="hand2"
        ).pack(side="left", padx=(sc(6), sc(4)))

        # Keyboard shortcuts hint
        tk.Label(topbar, text="⌘+  ⌘−  ⌘0 — scale  |  ⌘S — save",
                 bg=C_HEADER_BG, fg="#aaddee",
                 font=sf("Helvetica", 9)).pack(side="right", padx=sc(12))

        # -- Scrollable area --
        canvas_frame = tk.Frame(self.root, bg=C_BG)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas   = tk.Canvas(canvas_frame, bg=C_BG, highlightthickness=0)
        scrollbar     = ttk.Scrollbar(canvas_frame, orient="vertical",
                                      command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.form_frame    = tk.Frame(self.canvas, bg=C_BG,
                                      padx=sc(20), pady=sc(10))
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.form_frame, anchor="nw"
        )

        self.form_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>",     self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>",   self._on_mousewheel)
        self.canvas.bind_all("<Button-5>",   self._on_mousewheel)

        self._build_form()

    def _on_frame_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # -- Form layout --

    def _build_form(self):
        f      = self.form_frame
        self.w = {}

        # Supplier section (all fields editable, no defaults)
        self._section_header(f, "DOBAVITELJ BLAGA OZ. IZVAJALEC STORITVE")
        dob = self._card(f)
        self._field_row(dob, "Ime in naslov:",  "dob_ime", height=3)
        self._ddv_field(dob, "dob_ddv")
        self._field_row(dob, "ID za DDV / DŠ:", "dob_id")

        # Buyer section
        self._section_header(f, "KUPEC ALI NAROČNIK")
        kup_outer = tk.Frame(f, bg=C_BG)
        kup_outer.pack(fill="x", pady=(0, sc(4)))

        kup_left  = self._card(kup_outer, side="left", expand=True, fill="both")
        kup_right = self._card(kup_outer, side="left", expand=True, fill="both")

        self._field_row(kup_left, "Ime in naslov:",  "kup_ime", height=2)
        self._ddv_field(kup_left, "kup_ddv")
        self._field_row(kup_left, "ID za DDV / DŠ:", "kup_id")

        tk.Label(kup_right, text="Račun št:", bg="white", fg=C_LABEL,
                 font=sf("Helvetica", 12, "bold")).grid(
            row=0, column=0, sticky="w", padx=sc(8), pady=(sc(8), sc(2)))
        self.w["racun_st"] = self._grid_entry(kup_right, row=0, col=1, width=22)

        tk.Label(kup_right, text="Kraj:", bg="white", fg=C_LABEL,
                 font=sf("Helvetica", 11)).grid(
            row=1, column=0, sticky="w", padx=sc(8), pady=sc(2))
        self.w["kraj"] = self._grid_entry(kup_right, row=1, col=1, width=18)

        tk.Label(kup_right, text="Datum izdaje:", bg="white", fg=C_LABEL,
                 font=sf("Helvetica", 11)).grid(
            row=2, column=0, sticky="w", padx=sc(8), pady=sc(2))
        self.w["datum"] = self._grid_entry(kup_right, row=2, col=1, width=18)

        # Items table section
        self._section_header(f, "POSTAVKE")
        self.postavke_container = tk.Frame(f, bg=C_BG)
        self.postavke_container.pack(fill="x", pady=(0, sc(4)))
        self._build_postavke_table()

        btn_row = tk.Frame(f, bg=C_BG)
        btn_row.pack(fill="x", pady=(sc(2), sc(6)))
        self.btn_add_row = tk.Button(
            btn_row, text="+ Dodaj vrstico",
            command=self._add_postavka_row,
            bg="#e3f2fd", fg=C_LABEL,
            font=sf("Helvetica", 11),
            relief="flat", padx=sc(10), pady=sc(4),
            cursor="hand2")
        self.btn_add_row.pack(side="left")

        self.btn_remove_row = tk.Button(
            btn_row, text="− Odstrani zadnjo",
            command=self._remove_last_postavka,
            bg="#fce4e4", fg="#c62828",
            font=sf("Helvetica", 11),
            relief="flat", padx=sc(10), pady=sc(4),
            cursor="hand2")
        self.btn_remove_row.pack(side="left", padx=sc(6))

        # Other data + Totals (side by side)
        bottom = tk.Frame(f, bg=C_BG)
        bottom.pack(fill="x", pady=(0, sc(4)))

        lc = self._card(bottom, side="left", fill="both", expand=True)
        self._section_header(lc, "Drugi podatki", small=True)
        tk.Label(lc,
                 text="Davek na dodano vrednost ni obračunan od prometa, "
                      "navedenega v ... točki, ... odstavka, ... člena ZDDV-1",
                 font=sf("Helvetica", 9), bg="white", fg="#555",
                 justify="left", wraplength=sc(260)).pack(
            anchor="w", padx=sc(8), pady=sc(4))
        for lbl, key in [("Točka:", "tocka"),
                         ("Odstavek:", "odstavek"),
                         ("Člen ZDDV-1:", "clen")]:
            rf = tk.Frame(lc, bg="white")
            rf.pack(fill="x", padx=sc(8), pady=sc(1))
            tk.Label(rf, text=lbl, font=sf("Helvetica", 9), bg="white",
                     fg=C_LABEL, width=12, anchor="w").pack(side="left")
            e = self._make_entry(rf, width=16)
            e.pack(side="left", padx=sc(2))
            self.w[f"ddv_{key}"] = e

        rc = self._card(bottom, side="left", fill="both", expand=True)
        self._section_header(rc, "Skupaj", small=True)
        for lbl, key in [
            ("Skupaj vrednost EUR:",    "skupaj_vrednost"),
            ("Osnova DDV 1 stopnja %:", "osnova1_stopnja"),
            ("Osnova DDV 1 znesek:",    "osnova1_znesek"),
            ("Osnova DDV 2 stopnja %:", "osnova2_stopnja"),
            ("Osnova DDV 2 znesek:",    "osnova2_znesek"),
            ("Datum predplačila:",      "datum_predplacila"),
            ("Predplačilo EUR:",        "predplacilo"),
            ("SKUPAJ ZA PLAČILO EUR:", "skupaj_placilo"),
        ]:
            rf   = tk.Frame(rc, bg="white")
            rf.pack(fill="x", padx=sc(8), pady=sc(1))
            bold = lbl.startswith("SKUPAJ ZA")
            fnt  = sf("Helvetica", 11, "bold") if bold else sf("Helvetica", 9)
            tk.Label(rf, text=lbl, font=fnt, bg="white",
                     fg=C_LABEL, width=22, anchor="w").pack(side="left")
            e = self._make_entry(rf, width=14, bold=bold)
            e.pack(side="left", padx=sc(2))
            self.w[key] = e

        # Meta / Printer section (izdajatelj now editable, no defaults)
        self._section_header(f, "Meta / Tiskar")
        mc = self._card(f)
        self._field_row(mc, "Zap. št. seta:",                      "meta_zap_st")
        self._field_row(mc, "Izdajatelj/založnik:",                "meta_izdajatelj",
                        height=2)
        self._field_row(mc, "Serijska št. vezane knjige računov:", "meta_serijska")

        # Save and delete buttons
        sr = tk.Frame(f, bg=C_BG)
        sr.pack(fill="x", pady=sc(10))
        tk.Button(sr, text="💾  Shrani  (⌘S)", command=self._save,
                  bg=C_HEADER_BG, fg="black", activeforeground="#999999",
                  font=sf("Helvetica", 12, "bold"),
                  relief="flat", padx=sc(20), pady=sc(8),
                  cursor="hand2").pack(side="right")
        tk.Button(sr, text="🗑  Izbriši datoteko", command=self._delete_file,
                  bg="#c62828", fg="black", activeforeground="#999999",
                  font=sf("Helvetica", 11, "bold"),
                  relief="flat", padx=sc(14), pady=sc(8),
                  cursor="hand2").pack(side="right", padx=(0, sc(10)))

    # -- UI helper methods --

    def _section_header(self, parent, text, small=False):
        """Create a colored section header label."""
        bg   = "#2a8aaa" if small else C_HEADER_BG
        font = sf("Helvetica", 10, "bold") if small else sf("Helvetica", 11, "bold")
        tk.Label(parent, text=text, font=font, bg=bg, fg="white",
                 anchor="w", padx=sc(8), pady=sc(4)).pack(
            fill="x", pady=(sc(6), 0))

    def _card(self, parent, side="top", expand=False, fill="x"):
        """Create a white card container with a subtle border."""
        f = tk.Frame(parent, bg="white", relief="flat",
                     highlightbackground=C_BORDER, highlightthickness=1)
        f.pack(side=side, fill=fill, expand=expand,
               padx=sc(2), pady=(0, sc(2)))
        return f

    def _make_entry(self, parent, width=24, disabled=False, bold=False):
        """Create a styled Entry widget with clipboard shortcuts."""
        state = "disabled" if disabled else "normal"
        bg    = "#e8e8e8" if disabled else "#f0f8ff"
        font  = sf("Helvetica", 11, "bold") if bold else sf("Helvetica", 11)
        e = tk.Entry(parent, font=font, relief="flat",
                     bg=bg, width=width, state=state)
        if not disabled:
            e.bind("<Key>",       self._on_edit)
            e.bind("<Command-c>", lambda ev: e.event_generate("<<Copy>>"))
            e.bind("<Command-x>", lambda ev: e.event_generate("<<Cut>>"))
            e.bind("<Command-v>", lambda ev: e.event_generate("<<Paste>>"))
            e.bind("<Command-a>",
                   lambda ev: (e.select_range(0, "end"), "break"))
            e.bind("<Command-z>", lambda ev: e.event_generate("<<Undo>>"))
        return e

    def _make_text(self, parent, width=52, height=2, disabled=False):
        """Create a styled multi-line Text widget with clipboard shortcuts."""
        state = "disabled" if disabled else "normal"
        bg    = "#e8e8e8" if disabled else "#f0f8ff"
        t = tk.Text(parent, font=sf("Helvetica", 11), relief="flat",
                    bg=bg, height=height, width=width, state=state)
        if not disabled:
            t.bind("<Key>",       self._on_edit)
            t.bind("<Command-c>", lambda ev: t.event_generate("<<Copy>>"))
            t.bind("<Command-x>", lambda ev: t.event_generate("<<Cut>>"))
            t.bind("<Command-v>", lambda ev: t.event_generate("<<Paste>>"))
            t.bind("<Command-a>",
                   lambda ev: (t.tag_add("sel", "1.0", "end"), "break"))
            t.bind("<Command-z>", lambda ev: t.event_generate("<<Undo>>"))
        return t

    def _grid_entry(self, parent, row, col, width=24, disabled=False):
        """Create an Entry and place it in a grid layout."""
        e = self._make_entry(parent, width=width, disabled=disabled)
        e.grid(row=row, column=col, padx=sc(6), pady=sc(2), sticky="w")
        return e

    def _field_row(self, parent, label, key, height=1, disabled=False):
        """Create a labeled field row (Entry or Text depending on height)."""
        rf = tk.Frame(parent, bg="white")
        rf.pack(fill="x", padx=sc(8), pady=sc(2))
        tk.Label(rf, text=label, font=sf("Helvetica", 11), bg="white",
                 fg=C_LABEL, width=20, anchor="w").pack(side="left")
        if height > 1:
            w = self._make_text(rf, width=52, height=height,
                                disabled=disabled)
        else:
            w = self._make_entry(rf, width=52, disabled=disabled)
        w.pack(side="left", fill="x", expand=True)
        self.w[key] = w

    def _ddv_field(self, parent, key):
        """Create a DA/NE radio-button toggle for the VAT taxpayer field.

        Selecting DA excludes NE and vice versa (standard Radiobutton
        behavior via shared StringVar).
        """
        rf = tk.Frame(parent, bg="white")
        rf.pack(fill="x", padx=sc(8), pady=sc(2))
        tk.Label(rf, text="Davčni zavezanec za DDV:",
                 font=sf("Helvetica", 11),
                 bg="white", fg=C_LABEL, width=20, anchor="w").pack(side="left")

        var = tk.StringVar(value="")
        btn_frame = tk.Frame(rf, bg="white")
        btn_frame.pack(side="left")

        tk.Radiobutton(
            btn_frame, text="DA", variable=var, value="DA",
            font=sf("Helvetica", 11), bg="white",
            activebackground="white", indicatoron=True, cursor="hand2"
        ).pack(side="left", padx=(0, sc(10)))

        tk.Radiobutton(
            btn_frame, text="NE", variable=var, value="NE",
            font=sf("Helvetica", 11), bg="white",
            activebackground="white", indicatoron=True, cursor="hand2"
        ).pack(side="left")

        # Track changes for edit status
        var.trace_add("write", lambda *_: self._on_edit())
        self.w[key] = var

    # -- Items table --

    def _build_postavke_table(self):
        """Build (or rebuild) the items table header row."""
        if self.postavke_frame:
            self.postavke_frame.destroy()

        self.postavke_frame = tk.Frame(
            self.postavke_container, bg="white",
            highlightbackground=C_BORDER, highlightthickness=1
        )
        self.postavke_frame.pack(fill="x", padx=sc(2))
        self.postavke_rows = []

        for col_idx, (_, _, char_w) in enumerate(POSTAVKE_COLS):
            self.postavke_frame.columnconfigure(
                col_idx, weight=char_w, minsize=sc(char_w * 7))

        # Table header
        for col_idx, (_, header_text, char_w) in enumerate(POSTAVKE_COLS):
            tk.Label(
                self.postavke_frame,
                text=header_text,
                font=sf("Helvetica", 9),
                bg=C_HEADER_BG, fg="white",
                anchor="center",
                padx=sc(2), pady=sc(3),
                wraplength=sc(char_w * 7)
            ).grid(row=0, column=col_idx, sticky="nsew", padx=1, pady=(0, 1))

    def _add_postavka_row(self, data: dict | None = None):
        """Add a row to the items table.

        The Zap. št. column is auto-numbered and non-editable.
        Maximum number of rows is MAX_ROWS (9).
        """
        if len(self.postavke_rows) >= MAX_ROWS:
            return

        idx    = len(self.postavke_rows)
        bg     = C_ROW_LIGHT if idx % 2 == 0 else C_ROW_DARK
        grid_r = idx + 1
        row_num = idx + 1  # Sequential numbering starting at 1

        row_widgets = {}
        for col_idx, (key, _, char_w) in enumerate(POSTAVKE_COLS):
            if key == "zap_st":
                # Auto-numbered label (non-editable)
                lbl = tk.Label(
                    self.postavke_frame, text=str(row_num),
                    font=sf("Helvetica", 11), bg=bg,
                    anchor="center", width=char_w
                )
                lbl.grid(row=grid_r, column=col_idx,
                         sticky="ew", padx=1, pady=1)
                row_widgets[key] = lbl
            else:
                # Editable Entry for all other columns
                e = tk.Entry(
                    self.postavke_frame,
                    font=sf("Helvetica", 11),
                    relief="flat", bg=bg, width=char_w
                )
                e.grid(row=grid_r, column=col_idx,
                       sticky="ew", padx=1, pady=1)
                e.bind("<Key>",       self._on_edit)
                e.bind("<Command-c>",
                       lambda ev, w=e: w.event_generate("<<Copy>>"))
                e.bind("<Command-x>",
                       lambda ev, w=e: w.event_generate("<<Cut>>"))
                e.bind("<Command-v>",
                       lambda ev, w=e: w.event_generate("<<Paste>>"))
                e.bind("<Command-a>",
                       lambda ev, w=e: (w.select_range(0, "end"), "break"))
                e.bind("<Command-z>",
                       lambda ev, w=e: w.event_generate("<<Undo>>"))
                row_widgets[key] = e

        # Populate row with data if provided (zap_st is auto-numbered)
        if data:
            for key, widget in row_widgets.items():
                if key == "zap_st":
                    continue
                elif key == "DDV_stopnja":
                    val = data.get("DDV", {}).get("DDV_stopnja", "")
                elif key == "DDV_znesek":
                    val = data.get("DDV", {}).get("DDV_znesek", "")
                else:
                    val = data.get(key, "")
                widget.insert(0, str(val) if val else "")

        self.postavke_rows.append(row_widgets)
        self._update_row_buttons()

    def _remove_last_postavka(self):
        """Remove the last row from the items table.

        Cannot go below MIN_ROWS (5).
        """
        if len(self.postavke_rows) <= MIN_ROWS:
            return
        idx = len(self.postavke_rows) - 1
        for w in self.postavke_frame.grid_slaves(row=idx + 1):
            w.destroy()
        self.postavke_rows.pop()
        self._mark_edited()
        self._update_row_buttons()

    def _update_row_buttons(self):
        """Enable/disable add and remove buttons based on current row count."""
        if self.btn_add_row:
            state = "normal" if len(self.postavke_rows) < MAX_ROWS else "disabled"
            self.btn_add_row.configure(state=state)
        if self.btn_remove_row:
            state = ("normal" if len(self.postavke_rows) > MIN_ROWS
                     else "disabled")
            self.btn_remove_row.configure(state=state)

    # -- Folder and file management --

    def _choose_folder(self):
        """Open a system folder picker to select the working directory."""
        if self.modified:
            answer = messagebox.askyesnocancel(
                "Neshranjene spremembe",
                "Datoteka ima neshranjene spremembe. "
                "Shraniti pred nalaganjem nove mape?"
            )
            if answer is True:
                self._save()
            elif answer is None:
                return

        init = str(self.json_dir) if self.json_dir.exists() else "."
        folder = filedialog.askdirectory(
            title="Izberi mapo z JSON datotekami",
            initialdir=init
        )
        if not folder:
            return  # User cancelled the dialog

        self.json_dir     = Path(folder)
        self.current_file = None
        self.modified     = False
        self._load_file_list()

    def _create_new_file(self):
        """Create a new empty invoice JSON file from the built-in template."""
        name = simpledialog.askstring(
            "Nova datoteka",
            "Ime datoteke (brez .json):",
            parent=self.root
        )
        if not name:
            return

        # Sanitize filename (remove characters invalid on most OS)
        name = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
        if not name:
            return

        if not name.endswith(".json"):
            name += ".json"

        path = self.json_dir / name
        if path.exists():
            overwrite = messagebox.askyesno(
                "Datoteka obstaja",
                f"Datoteka '{name}' že obstaja. Prepisati?"
            )
            if not overwrite:
                return

        # Save current file if it has unsaved changes
        if self.modified:
            answer = messagebox.askyesnocancel(
                "Neshranjene spremembe",
                "Datoteka ima neshranjene spremembe. Shraniti?"
            )
            if answer is True:
                self._save()
            elif answer is None:
                return

        try:
            template = copy.deepcopy(EMPTY_TEMPLATE)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(template, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("Napaka", f"Napaka pri ustvarjanju: {e}")
            return

        # Reload file list and select the newly created file
        self._load_file_list(select_name=name)

    def _natural_key(self, s: str):
        """Natural sort key (handles embedded numbers correctly)."""
        return [int(c) if c.isdigit() else c.lower()
                for c in re.split(r'(\d+)', s)]

    def _load_file_list(self, select_name: str | None = None):
        """Load the list of JSON files from the current working directory."""
        files = sorted(
            self.json_dir.glob("*.json"),
            key=lambda p: self._natural_key(p.name)
        )
        self.file_list = [f.name for f in files]
        self.file_combo["values"] = self.file_list

        if not self.file_list:
            self._update_counter()
            return

        if select_name and select_name in self.file_list:
            idx = self.file_list.index(select_name)
        else:
            idx = 0

        self.file_combo.current(idx)
        self._load_file(self.json_dir / self.file_list[idx])

    def _current_index(self) -> int:
        """Return the index of the currently selected file."""
        name = self.file_var.get()
        try:
            return self.file_list.index(name)
        except ValueError:
            return 0

    def _prev_file(self):
        idx = self._current_index()
        if idx > 0:
            self._switch_to_index(idx - 1)

    def _next_file(self):
        idx = self._current_index()
        if idx < len(self.file_list) - 1:
            self._switch_to_index(idx + 1)

    def _switch_to_index(self, idx: int):
        """Switch to a file at the given index, prompting to save if needed."""
        if self.modified:
            answer = messagebox.askyesnocancel(
                "Neshranjene spremembe",
                "Datoteka ima neshranjene spremembe. "
                "Shraniti pred nalaganjem nove?"
            )
            if answer is True:
                self._save()
            elif answer is None:
                return
        self.file_combo.current(idx)
        self._load_file(self.json_dir / self.file_list[idx])

    def _on_file_selected(self, event=None):
        """Handle file selection from the dropdown combobox."""
        if self.modified:
            answer = messagebox.askyesnocancel(
                "Neshranjene spremembe",
                "Datoteka ima neshranjene spremembe. "
                "Shraniti pred nalaganjem nove?"
            )
            if answer is True:
                self._save()
            elif answer is None:
                # Revert combobox to the previously loaded file
                if self.current_file:
                    try:
                        i = self.file_list.index(self.current_file.name)
                        self.file_combo.current(i)
                    except ValueError:
                        pass
                return
        name = self.file_var.get()
        self._load_file(self.json_dir / name)

    def _update_counter(self):
        """Update the file counter label and prev/next button states."""
        if not self.file_list:
            self.counter_label.configure(text="")
            return
        idx   = self._current_index() + 1
        total = len(self.file_list)
        self.counter_label.configure(text=f"{idx} / {total}")
        self.btn_prev.configure(
            state="normal" if idx > 1     else "disabled")
        self.btn_next.configure(
            state="normal" if idx < total else "disabled")

    def _load_file(self, path: Path):
        """Load a JSON file and populate the form."""
        try:
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except Exception as e:
            messagebox.showerror("Napaka", f"Napaka pri nalaganju: {e}")
            return

        self.current_file = path
        self.modified     = False
        self._populate_form()
        self._set_status("saved")
        self._update_counter()
        self.root.title(f"VKR Invoice Editor — {path.name}")
        self.canvas.yview_moveto(0)

    # -- Form data population --

    def _populate_form(self):
        """Fill all form widgets with data from the loaded JSON."""
        r = self.data.get("racun", {})

        # Supplier
        dob = r.get("dobavitelj", {})
        self._set_w("dob_ime", dob.get("ime_in_naslov", ""))
        self._set_w("dob_ddv", dob.get("davčni_zavezanec_za_DDV", ""))
        self._set_w("dob_id",  dob.get("ID_za_DDV_DS", ""))

        # Buyer
        kup = r.get("kupec", {})
        self._set_w("kup_ime",  kup.get("ime_in_naslov", ""))
        self._set_w("kup_ddv",  kup.get("davčni_zavezanec_za_DDV", ""))
        self._set_w("kup_id",   kup.get("ID_za_DDV_DS", ""))
        self._set_w("racun_st", kup.get("racun_st", ""))
        kid = kup.get("kraj_in_datum_izdaje", {})
        self._set_w("kraj",  kid.get("kraj", ""))
        self._set_w("datum", kid.get("datum", ""))

        # Items table (rebuild, then populate rows)
        self._build_postavke_table()
        for p in r.get("postavke", []):
            self._add_postavka_row(p)
        # Pad to minimum number of rows
        while len(self.postavke_rows) < MIN_ROWS:
            self._add_postavka_row()

        # Other data — VAT exception
        ddv_izj = r.get("drugi_podatki", {}).get("opomba_DDV_izjema", {})
        self._set_w("ddv_tocka",    ddv_izj.get("tocka", ""))
        self._set_w("ddv_odstavek", ddv_izj.get("odstavek", ""))
        self._set_w("ddv_clen",     ddv_izj.get("clen_ZDDV1", ""))

        # Totals
        sk = r.get("skupaj", {})
        self._set_w("skupaj_vrednost",
                     sk.get("skupaj_vrednost_EUR", ""))
        self._set_w("osnova1_stopnja",
                     sk.get("osnova_DDV_1", {}).get("stopnja_procent", ""))
        self._set_w("osnova1_znesek",
                     sk.get("osnova_DDV_1", {}).get("znesek_DDV", ""))
        self._set_w("osnova2_stopnja",
                     sk.get("osnova_DDV_2", {}).get("stopnja_procent", ""))
        self._set_w("osnova2_znesek",
                     sk.get("osnova_DDV_2", {}).get("znesek_DDV", ""))
        self._set_w("datum_predplacila",
                     sk.get("datum_predplacila", ""))
        self._set_w("predplacilo",
                     sk.get("predplacilo_EUR", ""))
        self._set_w("skupaj_placilo",
                     sk.get("skupaj_za_placilo_EUR", ""))

        # Meta
        meta = r.get("meta", {})
        self._set_w("meta_zap_st",
                     meta.get("zap_st_seta", ""))
        self._set_w("meta_izdajatelj",
                     meta.get("izdajatelj_zaloznik", ""))
        self._set_w("meta_serijska",
                     meta.get("serijska_st_vezane_knjige_racunov", ""))

    def _set_w(self, key: str, value: str):
        """Set a widget's value programmatically.

        Suppresses edit-status events so that loading a file
        does not mark the form as modified.
        """
        w = self.w.get(key)
        if w is None:
            return
        self._suppress_edit = True
        if isinstance(w, tk.StringVar):
            w.set(value)
        elif isinstance(w, tk.Text):
            before = w.cget("state")
            w.configure(state="normal")
            w.delete("1.0", "end")
            w.insert("1.0", value)
            w.configure(state=before)
        else:
            before = w.cget("state")
            w.configure(state="normal")
            w.delete(0, "end")
            w.insert(0, value)
            w.configure(state=before)
        self._suppress_edit = False

    def _get_w(self, key: str) -> str:
        """Get a widget's current text value."""
        w = self.w.get(key)
        if w is None:
            return ""
        if isinstance(w, tk.StringVar):
            return w.get().strip()
        if isinstance(w, tk.Text):
            return w.get("1.0", "end").strip()
        return w.get().strip()

    # -- Save / collect --

    def _collect_form(self) -> dict:
        """Collect all form data into a JSON-serializable dict."""
        r = self.data.get("racun", {})

        # Supplier (editable)
        r.setdefault("dobavitelj", {})
        r["dobavitelj"]["ime_in_naslov"]           = self._get_w("dob_ime")
        r["dobavitelj"]["davčni_zavezanec_za_DDV"] = self._get_w("dob_ddv")
        r["dobavitelj"]["ID_za_DDV_DS"]            = self._get_w("dob_id")

        # Buyer
        r.setdefault("kupec", {})
        r["kupec"]["ime_in_naslov"]           = self._get_w("kup_ime")
        r["kupec"]["davčni_zavezanec_za_DDV"] = self._get_w("kup_ddv")
        r["kupec"]["ID_za_DDV_DS"]            = self._get_w("kup_id")
        r["kupec"]["racun_st"]                = self._get_w("racun_st")
        r["kupec"].setdefault("kraj_in_datum_izdaje", {})
        r["kupec"]["kraj_in_datum_izdaje"]["kraj"]  = self._get_w("kraj")
        r["kupec"]["kraj_in_datum_izdaje"]["datum"] = self._get_w("datum")

        # Items (zap_st is derived from row index, not from widget)
        postavke = []
        for i, rw in enumerate(self.postavke_rows):
            p = {
                "zap_st":                   str(i + 1),
                "datum_dobave":             rw["datum_dobave"].get().strip(),
                "vrsta_blaga_storitve":
                    rw["vrsta_blaga_storitve"].get().strip(),
                "kolicina_in_merska_enota":
                    rw["kolicina_in_merska_enota"].get().strip(),
                "cena_na_enoto_brez_DDV":
                    rw["cena_na_enoto_brez_DDV"].get().strip(),
                "znesek_znizanja_popust":
                    rw["znesek_znizanja_popust"].get().strip(),
                "vrednost_brez_DDV":
                    rw["vrednost_brez_DDV"].get().strip(),
                "DDV": {
                    "DDV_stopnja":
                        rw["DDV_stopnja"].get().strip(),
                    "DDV_znesek":
                        rw["DDV_znesek"].get().strip(),
                },
                "vrednost_z_DDV":
                    rw["vrednost_z_DDV"].get().strip(),
            }
            # Only include rows that have at least some data
            if (any(v for k, v in p.items() if k != "DDV")
                    or any(p["DDV"].values())):
                postavke.append(p)
        r["postavke"] = postavke

        # Other data — VAT exception
        r.setdefault("drugi_podatki", {}).setdefault(
            "opomba_DDV_izjema", {})
        ddv_exc = r["drugi_podatki"]["opomba_DDV_izjema"]
        ddv_exc["tocka"]      = self._get_w("ddv_tocka")
        ddv_exc["odstavek"]   = self._get_w("ddv_odstavek")
        ddv_exc["clen_ZDDV1"] = self._get_w("ddv_clen")

        # Totals
        r.setdefault("skupaj", {})
        r["skupaj"]["skupaj_vrednost_EUR"] = self._get_w("skupaj_vrednost")
        r["skupaj"].setdefault("osnova_DDV_1", {})
        r["skupaj"]["osnova_DDV_1"]["stopnja_procent"] = \
            self._get_w("osnova1_stopnja")
        r["skupaj"]["osnova_DDV_1"]["znesek_DDV"] = \
            self._get_w("osnova1_znesek")
        r["skupaj"].setdefault("osnova_DDV_2", {})
        r["skupaj"]["osnova_DDV_2"]["stopnja_procent"] = \
            self._get_w("osnova2_stopnja")
        r["skupaj"]["osnova_DDV_2"]["znesek_DDV"] = \
            self._get_w("osnova2_znesek")
        r["skupaj"]["datum_predplacila"] = \
            self._get_w("datum_predplacila")
        r["skupaj"]["predplacilo_EUR"] = self._get_w("predplacilo")
        r["skupaj"]["skupaj_za_placilo_EUR"] = \
            self._get_w("skupaj_placilo")

        # Meta (now includes izdajatelj)
        r.setdefault("meta", {})
        r["meta"]["zap_st_seta"] = self._get_w("meta_zap_st")
        r["meta"]["izdajatelj_zaloznik"] = \
            self._get_w("meta_izdajatelj")
        r["meta"]["serijska_st_vezane_knjige_racunov"] = \
            self._get_w("meta_serijska")

        self.data["racun"] = r
        return self.data

    def _save(self):
        """Save the current form data back to the JSON file."""
        if not self.current_file:
            return
        try:
            data = self._collect_form()
            with open(self.current_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            self.modified = False
            self._set_status("saved")
        except Exception as e:
            messagebox.showerror("Napaka pri shranjevanju", str(e))

    def _delete_file(self):
        """Delete the current JSON file after user confirmation."""
        if not self.current_file:
            return
        name = self.current_file.name
        confirmed = messagebox.askyesno(
            "Izbrisati datoteko?",
            f"Ali res želite izbrisati datoteko '{name}'?"
        )
        if not confirmed:
            return
        try:
            self.current_file.unlink()
        except Exception as e:
            messagebox.showerror("Napaka", f"Napaka pri brisanju: {e}")
            return
        self.current_file = None
        self.modified     = False
        self._load_file_list()

    # -- Status tracking --

    def _on_edit(self, event=None):
        """Called when any form field is modified by the user."""
        if self._suppress_edit:
            return
        if not self.modified:
            self._mark_edited()

    def _mark_edited(self):
        self.modified = True
        self._set_status("edited")

    def _set_status(self, status: str):
        if status == "saved":
            self.status_label.configure(text="✓ saved", fg="#90ee90")
        else:
            self.status_label.configure(text="● edited", fg="#ffcccc")


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(
        description="VKR Invoice Editor (JSON)")
    parser.add_argument(
        "--dir", "-d",
        type=Path,
        default=None,
        help="Folder with JSON files (if omitted, starts in current directory)"
    )
    args = parser.parse_args()

    root = tk.Tk()
    ttk.Style().configure("TCombobox", font=("Helvetica", 11))

    json_dir = args.dir

    if json_dir is None:
        # No directory specified — start with current working directory;
        # user can switch to any folder via the Mapa button in the GUI
        json_dir = Path(".")

    if not json_dir.exists():
        print(f"ERROR: Folder does not exist: {json_dir}")
        print("Check the path or use --dir /path/to/folder")
        raise SystemExit(1)

    ChequeEditor(root, json_dir)
    root.mainloop()


if __name__ == "__main__":
    main()