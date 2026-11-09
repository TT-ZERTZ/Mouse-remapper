import sys
import subprocess
import importlib

REQUIRED_PACKAGES = {
    "customtkinter": "customtkinter",
    "pynput": "pynput",
}


def ensure_dependencies():
    """Auto-install any missing packages, then ask the user to relaunch."""
    missing = []
    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return

    print(f"Missing dependencies detected: {', '.join(missing)}")
    print("Installing now...\n")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    except subprocess.CalledProcessError as e:
        print(f"\nFailed to install dependencies automatically: {e}")
        print("Try running manually: pip install customtkinter pynput")
        input("Press Enter to exit...")
        sys.exit(1)

    print("\nDependencies installed successfully.")
    input("Please relaunch the script now. Press Enter to exit...")
    sys.exit(0)


ensure_dependencies()

import json
import os
import threading
import tkinter.messagebox as messagebox

import customtkinter as ctk
from pynput import mouse, keyboard

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mouse_remap_config.json")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ---------------------------------------------------------------------------
# Button name <-> pynput.mouse.Button mapping
# ---------------------------------------------------------------------------

BUTTON_DISPLAY_TO_KEY = {
    "Left Click": "left",
    "Right Click": "right",
    "Middle Click": "middle",
    "Side Button 1 (Back)": "x1",
    "Side Button 2 (Forward)": "x2",
    "Scroll Up": "scroll_up",
    "Scroll Down": "scroll_down",
}
BUTTON_KEY_TO_DISPLAY = {v: k for k, v in BUTTON_DISPLAY_TO_KEY.items()}
BUTTON_OPTIONS = list(BUTTON_DISPLAY_TO_KEY.keys())

# pynput.mouse.Button -> internal name (for buttons that are real click events)
PYNPUT_BUTTON_TO_NAME = {
    mouse.Button.left: "left",
    mouse.Button.right: "right",
    mouse.Button.middle: "middle",
}
# x1/x2 don't exist on every platform/pynput build, add defensively
if hasattr(mouse.Button, "x1"):
    PYNPUT_BUTTON_TO_NAME[mouse.Button.x1] = "x1"
if hasattr(mouse.Button, "x2"):
    PYNPUT_BUTTON_TO_NAME[mouse.Button.x2] = "x2"

NAME_TO_PYNPUT_BUTTON = {v: k for k, v in PYNPUT_BUTTON_TO_NAME.items()}

# ---------------------------------------------------------------------------
# Key combo parsing ("alt+tab" -> [Key.alt, 't'... ] etc.)
# ---------------------------------------------------------------------------

SPECIAL_KEYS = {
    "alt": keyboard.Key.alt,
    "altgr": keyboard.Key.alt_gr,
    "ctrl": keyboard.Key.ctrl,
    "control": keyboard.Key.ctrl,
    "shift": keyboard.Key.shift,
    "cmd": keyboard.Key.cmd,
    "win": keyboard.Key.cmd,
    "super": keyboard.Key.cmd,
    "tab": keyboard.Key.tab,
    "esc": keyboard.Key.esc,
    "escape": keyboard.Key.esc,
    "enter": keyboard.Key.enter,
    "return": keyboard.Key.enter,
    "space": keyboard.Key.space,
    "backspace": keyboard.Key.backspace,
    "delete": keyboard.Key.delete,
    "del": keyboard.Key.delete,
    "up": keyboard.Key.up,
    "down": keyboard.Key.down,
    "left": keyboard.Key.left,
    "right": keyboard.Key.right,
    "home": keyboard.Key.home,
    "end": keyboard.Key.end,
    "pageup": keyboard.Key.page_up,
    "pagedown": keyboard.Key.page_down,
    "capslock": keyboard.Key.caps_lock,
    "printscreen": keyboard.Key.print_screen,
    "insert": keyboard.Key.insert,
    "menu": keyboard.Key.menu,
}
for i in range(1, 25):
    SPECIAL_KEYS[f"f{i}"] = getattr(keyboard.Key, f"f{i}", None)
SPECIAL_KEYS = {k: v for k, v in SPECIAL_KEYS.items() if v is not None}


def parse_combo(text):
    """Turn 'ctrl+shift+esc' into a list of pynput key objects/characters."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty key combo")
    keys = []
    for p in parts:
        if p in SPECIAL_KEYS:
            keys.append(SPECIAL_KEYS[p])
        elif len(p) == 1:
            keys.append(p)
        else:
            raise ValueError(f"Unrecognized key: '{p}'")
    return keys


# ---------------------------------------------------------------------------
# Remapping engine
# ---------------------------------------------------------------------------

class RemapEngine:
    def __init__(self, get_mappings, on_status_change):
        self.get_mappings = get_mappings  # callable -> dict[name] = "combo text"
        self.on_status_change = on_status_change
        self.mouse_controller = mouse.Controller()
        self.keyboard_controller = keyboard.Controller()
        self.mouse_listener = None
        self.panic_listener = None
        self.active = False
        self._injecting = False

    # -- lifecycle ----------------------------------------------------
    def start(self):
        if self.active:
            return
        self.mouse_listener = mouse.Listener(
            on_click=self._on_click, on_scroll=self._on_scroll, suppress=True
        )
        self.mouse_listener.start()

        if self.panic_listener is None:
            self.panic_listener = keyboard.Listener(on_press=self._on_panic_key)
            self.panic_listener.start()

        self.active = True
        self.on_status_change(True)

    def stop(self):
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        self.active = False
        self.on_status_change(False)

    def shutdown(self):
        self.stop()
        if self.panic_listener:
            self.panic_listener.stop()
            self.panic_listener = None

    def _on_panic_key(self, key):
        if key == keyboard.Key.f9 and self.active:
            self.stop()

    # -- click / scroll handling ---------------------------------------
    def _fire_combo(self, combo_text):
        try:
            keys = parse_combo(combo_text)
        except ValueError:
            return

        def do_fire():
            for k in keys:
                self.keyboard_controller.press(k)
            for k in reversed(keys):
                self.keyboard_controller.release(k)

        threading.Thread(target=do_fire, daemon=True).start()

    def _on_click(self, x, y, button, pressed):
        if self._injecting:
            return
        name = PYNPUT_BUTTON_TO_NAME.get(button)
        if name is None:
            return
        mappings = self.get_mappings()
        if name in mappings and pressed:
            self._fire_combo(mappings[name])
        elif name not in mappings:
            # Pass the original click through untouched
            self._injecting = True
            try:
                if pressed:
                    self.mouse_controller.press(button)
                else:
                    self.mouse_controller.release(button)
            finally:
                self._injecting = False

    def _on_scroll(self, x, y, dx, dy):
        if self._injecting:
            return
        name = "scroll_up" if dy > 0 else ("scroll_down" if dy < 0 else None)
        mappings = self.get_mappings()
        if name and name in mappings:
            self._fire_combo(mappings[name])
        else:
            self._injecting = True
            try:
                self.mouse_controller.scroll(dx, dy)
            finally:
                self._injecting = False


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Mouse Button Remapper")
        self.geometry("480x560")
        self.minsize(440, 500)

        self.mappings = {}  # name -> combo text, e.g. {"x1": "alt+tab"}
        self.engine = RemapEngine(lambda: self.mappings, self._on_engine_status)

        self._load_config()
        self._build_ui()
        self._refresh_mapping_list()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- UI construction -------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 16, "pady": 8}

        title = ctk.CTkLabel(self, text="Mouse Button Remapper", font=ctk.CTkFont(size=20, weight="bold"))
        title.pack(**pad)

        form = ctk.CTkFrame(self)
        form.pack(fill="x", **pad)

        ctk.CTkLabel(form, text="Mouse button:").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        self.button_dropdown = ctk.CTkOptionMenu(form, values=BUTTON_OPTIONS)
        self.button_dropdown.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

        ctk.CTkLabel(form, text="Key or combo (e.g. alt+tab, ctrl+c, f5):").grid(
            row=2, column=0, sticky="w", padx=10, pady=(0, 4)
        )
        self.key_entry = ctk.CTkEntry(form, placeholder_text="alt+tab")
        self.key_entry.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))

        form.grid_columnconfigure(0, weight=1)

        btn_row = ctk.CTkFrame(form, fg_color="transparent")
        btn_row.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))
        btn_row.grid_columnconfigure((0, 1), weight=1)

        assign_btn = ctk.CTkButton(btn_row, text="Assign", command=self._assign_mapping)
        assign_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5))

        clear_btn = ctk.CTkButton(btn_row, text="Remove mapping", fg_color="gray30",
                                   hover_color="gray20", command=self._remove_mapping)
        clear_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0))

        # Current mappings list
        list_label = ctk.CTkLabel(self, text="Current mappings", font=ctk.CTkFont(size=14, weight="bold"))
        list_label.pack(anchor="w", padx=16, pady=(8, 0))

        self.list_frame = ctk.CTkScrollableFrame(self, height=180)
        self.list_frame.pack(fill="both", expand=True, **pad)

        # Start/stop + status
        control_row = ctk.CTkFrame(self, fg_color="transparent")
        control_row.pack(fill="x", **pad)
        control_row.grid_columnconfigure(0, weight=1)

        self.toggle_btn = ctk.CTkButton(control_row, text="Start Remapping", command=self._toggle_engine,
                                         fg_color="#2fa84f", hover_color="#238040")
        self.toggle_btn.grid(row=0, column=0, sticky="ew")

        self.status_label = ctk.CTkLabel(self, text="Status: stopped  |  Panic key: F9", text_color="gray")
        self.status_label.pack(pady=(0, 12))

    # -- actions -----------------------------------------------------
    def _assign_mapping(self):
        display_name = self.button_dropdown.get()
        combo_text = self.key_entry.get().strip()
        if not combo_text:
            messagebox.showerror("Missing key", "Type a key or combo first, e.g. alt+tab")
            return
        try:
            parse_combo(combo_text)  # validate
        except ValueError as e:
            messagebox.showerror("Invalid key combo", str(e))
            return

        name = BUTTON_DISPLAY_TO_KEY[display_name]
        self.mappings[name] = combo_text
        self._save_config()
        self._refresh_mapping_list()

    def _remove_mapping(self):
        display_name = self.button_dropdown.get()
        name = BUTTON_DISPLAY_TO_KEY[display_name]
        if name in self.mappings:
            del self.mappings[name]
            self._save_config()
            self._refresh_mapping_list()

    def _refresh_mapping_list(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        if not self.mappings:
            ctk.CTkLabel(self.list_frame, text="No mappings yet.", text_color="gray").pack(pady=10)
            return

        for name, combo_text in self.mappings.items():
            row = ctk.CTkFrame(self.list_frame)
            row.pack(fill="x", pady=3)
            label = ctk.CTkLabel(row, text=f"{BUTTON_KEY_TO_DISPLAY[name]}  →  {combo_text}", anchor="w")
            label.pack(side="left", fill="x", expand=True, padx=8, pady=6)
            remove_btn = ctk.CTkButton(row, text="✕", width=30, fg_color="gray30", hover_color="#8a2f2f",
                                        command=lambda n=name: self._remove_by_name(n))
            remove_btn.pack(side="right", padx=8, pady=6)

    def _remove_by_name(self, name):
        if name in self.mappings:
            del self.mappings[name]
            self._save_config()
            self._refresh_mapping_list()

    def _toggle_engine(self):
        if self.engine.active:
            self.engine.stop()
        else:
            if "left" in self.mappings:
                proceed = messagebox.askyesno(
                    "Remapping Left Click",
                    "You're remapping Left Click. If anything goes wrong you can lose the "
                    "ability to click. Press F9 at any time to instantly stop remapping.\n\n"
                    "Continue?",
                )
                if not proceed:
                    return
            self.engine.start()

    def _on_engine_status(self, active):
        # Called from the listener thread, so hop back to the UI thread
        self.after(0, self._update_status_ui, active)

    def _update_status_ui(self, active):
        if active:
            self.toggle_btn.configure(text="Stop Remapping", fg_color="#a83b2f", hover_color="#80302a")
            self.status_label.configure(text="Status: ACTIVE  |  Panic key: F9")
        else:
            self.toggle_btn.configure(text="Start Remapping", fg_color="#2fa84f", hover_color="#238040")
            self.status_label.configure(text="Status: stopped  |  Panic key: F9")

    # -- config persistence -------------------------------------------
    def _save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(self.mappings, f, indent=2)
        except OSError:
            pass

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    self.mappings = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.mappings = {}

    def _on_close(self):
        self.engine.shutdown()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
