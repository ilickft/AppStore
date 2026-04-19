import tkinter as tk
import customtkinter as ctk
import requests
import threading
import webbrowser
import time
import re
import os
import json
import subprocess
from PIL import Image, ImageDraw
from io import BytesIO

SEARCH_QUERY = "termux+desktop+OR+termux+gui+OR+termux+x11+OR+topic:termux-desktop+OR+topic:termux-x11"
GITHUB_API_BASE = "https://api.github.com"
CLIENT_ID = "Iv1.b08f870e6c6c180a"
VERIFIED_REPOS_URL = "https://raw.githubusercontent.com/ilickft/AppStore/refs/heads/main/repos.txt"
APPSTORE_REPO_URL = "https://github.com/ilickft/AppStore/"
INSTALL_BASE = os.path.expanduser("~/.appstore/apps")
INSTALL_DB_PATH = os.path.expanduser("~/.appstore/installed.json")

os.makedirs(INSTALL_BASE, exist_ok=True)
os.makedirs(os.path.dirname(INSTALL_DB_PATH), exist_ok=True)


def _circle_crop(img, size):
    img = img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


def _placeholder_icon(name, size):
    palette = ["#1a3a6a", "#2d1a6a", "#1a4a3a", "#4a2d1a", "#3a1a4a", "#1a4a4a", "#4a1a2e"]
    color = palette[sum(ord(c) for c in (name or "?")) % len(palette)]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=color)
    letter = (name or "?")[0].upper()
    draw.text((size // 2 - size // 9, size // 2 - size // 7), letter, fill="#ffffff")
    return img


class InstalledDB:
    def __init__(self):
        self._db = {}
        self._load()

    def _load(self):
        if os.path.exists(INSTALL_DB_PATH):
            try:
                with open(INSTALL_DB_PATH) as f:
                    self._db = json.load(f)
            except Exception:
                self._db = {}

    def _save(self):
        with open(INSTALL_DB_PATH, "w") as f:
            json.dump(self._db, f, indent=2)

    def is_installed(self, full_name):
        return full_name in self._db and os.path.isdir(self._db[full_name].get("path", ""))

    def get(self, full_name):
        return self._db.get(full_name)

    def add(self, full_name, name, path, pushed_at):
        self._db[full_name] = {"name": name, "path": path, "pushed_at": pushed_at}
        self._save()

    def remove(self, full_name):
        self._db.pop(full_name, None)
        self._save()

    def needs_update(self, full_name, current_pushed_at):
        entry = self._db.get(full_name)
        return bool(entry and entry.get("pushed_at", "") < current_pushed_at)


class GitHubAPI:
    def __init__(self):
        self.token = None
        self.verified_repos = set()
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Termux-AppStore",
        }

    def set_token(self, token):
        self.token = token
        self.headers["Authorization"] = f"token {token}"

    def fetch_verified_repos(self):
        try:
            r = requests.get(VERIFIED_REPOS_URL, timeout=5)
            if r.status_code == 200:
                self.verified_repos = {
                    line.strip() for line in r.text.splitlines()
                    if line.strip() and "/" in line and not line.lower().startswith("example")
                }
        except Exception:
            pass
        return self.verified_repos

    def is_verified(self, full_name):
        return full_name in self.verified_repos

    def search_apps(self, query=SEARCH_QUERY):
        apps = []
        seen = set()
        for repo_name in list(self.verified_repos):
            d = self._get_repo(repo_name)
            if d:
                apps.append(d)
                seen.add(d["full_name"])
        url = f"{GITHUB_API_BASE}/search/repositories?q={query}&per_page=60&sort=updated"
        try:
            r = requests.get(url, headers=self.headers, timeout=12)
            if r.status_code == 200:
                items = r.json().get("items", [])
                for item in items:
                    if item["full_name"] not in seen:
                        apps.append(item)
                        seen.add(item["full_name"])
            elif r.status_code == 403:
                print("GitHub API rate limit exceeded. Login for a higher quota.")
            else:
                print(f"GitHub Search API returned status: {r.status_code}")
        except Exception as e:
            print(f"Search error: {e}")
        return apps

    def _get_repo(self, full_name):
        try:
            r = requests.get(f"{GITHUB_API_BASE}/repos/{full_name}", headers=self.headers, timeout=5)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def get_readme(self, full_name):
        try:
            r = requests.get(f"{GITHUB_API_BASE}/repos/{full_name}/readme", headers=self.headers, timeout=5)
            if r.status_code == 200:
                dl = r.json().get("download_url")
                if dl:
                    return requests.get(dl, timeout=8).text
        except Exception:
            pass
        return None

    def get_readme_images(self, full_name):
        text = self.get_readme(full_name)
        if not text:
            return []
        return re.findall(r'!\[.*?\]\((.*?)\)', text)

    def start_device_flow(self):
        try:
            r = requests.post(
                "https://github.com/login/device/code",
                data={"client_id": CLIENT_ID, "scope": "public_repo"},
                headers={"Accept": "application/json"}, timeout=10
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def poll_for_token(self, device_code, interval):
        data = {
            "client_id": CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        while True:
            try:
                r = requests.post(
                    "https://github.com/login/oauth/access_token",
                    data=data, headers={"Accept": "application/json"}, timeout=10
                )
                d = r.json()
                if "access_token" in d:
                    return d["access_token"]
                if d.get("error", "") not in ("authorization_pending", "slow_down"):
                    return None
            except Exception:
                return None
            time.sleep(interval)


class AppStoreApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Termux AppStore")
        self.geometry("880x640")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#0f0f1a")

        self.api = GitHubAPI()
        self.db = InstalledDB()
        self.loaded_apps = []
        self._search_after = None
        self._search_visible = False
        self._icon_cache = {}
        self._ctk_img_refs = {}
        self._tile_icon_labels = {}
        self._ss_refs = []
        self._detail_readme_visible = False
        self._readme_loaded = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_search_row()
        self._build_body()
        self.show_home()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color="#12122a")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)
        hdr.grid_propagate(False)

        ctk.CTkLabel(
            hdr, text="  ◈  AppStore",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#7eb3ff"
        ).grid(row=0, column=0, padx=12, pady=10, sticky="w")

        icons_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        icons_frame.grid(row=0, column=2, padx=8, sticky="e")

        ibtn = dict(width=38, height=38, corner_radius=19,
                    fg_color="transparent", hover_color="#1e1e40",
                    font=ctk.CTkFont(size=19))

        ctk.CTkButton(icons_frame, text="⌂", command=self.show_home, **ibtn).pack(side="left", padx=2)
        ctk.CTkButton(icons_frame, text="⌕", command=self._toggle_search, **ibtn).pack(side="left", padx=2)
        self._profile_btn = ctk.CTkButton(
            icons_frame, text="◉", command=self._login, **ibtn
        )
        self._profile_btn.pack(side="left", padx=(2, 4))

    def _build_search_row(self):
        self._search_row = ctk.CTkFrame(self, height=46, corner_radius=0, fg_color="#0d0d20")
        self._search_entry = ctk.CTkEntry(
            self._search_row,
            placeholder_text="Search apps by name, description or topic...",
            height=32, font=ctk.CTkFont(size=13),
            fg_color="#1a1a30", border_color="#2a2a50", border_width=1,
        )
        self._search_entry.pack(fill="x", padx=14, pady=7)
        self._search_entry.bind("<KeyRelease>", self._on_search_key)

    def _build_body(self):
        self.body = ctk.CTkFrame(self, fg_color="#0f0f1a", corner_radius=0)
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.home_view = ctk.CTkScrollableFrame(self.body, fg_color="#0f0f1a", corner_radius=0)
        self.home_view.grid(row=0, column=0, sticky="nsew")

        self.detail_view = ctk.CTkScrollableFrame(self.body, fg_color="#0f0f1a", corner_radius=0)

    def _toggle_search(self):
        if self._search_visible:
            self._search_row.grid_forget()
            self._search_visible = False
        else:
            self._search_row.grid(row=1, column=0, sticky="ew")
            self._search_visible = True
            self._search_entry.focus()

    def _on_search_key(self, _=None):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(280, self._apply_filter)

    def _apply_filter(self):
        q = self._search_entry.get().strip().lower()
        if not q:
            self._render_home(self.loaded_apps)
        else:
            filtered = [
                a for a in self.loaded_apps
                if q in a.get("name", "").lower()
                or q in (a.get("description") or "").lower()
                or any(q in t.lower() for t in (a.get("topics") or []))
            ]
            self._render_home(filtered)

    def show_home(self):
        self.title("Home - AppStore")
        self.detail_view.grid_forget()
        self.home_view.grid(row=0, column=0, sticky="nsew")
        for w in self.home_view.winfo_children():
            w.destroy()
        self._tile_icon_labels.clear()
        self._show_loading_state()
        threading.Thread(target=self._fetch_apps, daemon=True).start()

    def _show_loading_state(self):
        f = ctk.CTkFrame(self.home_view, fg_color="transparent")
        f.pack(expand=True, pady=80)
        ctk.CTkLabel(f, text="Fetching apps from GitHub...",
                     text_color="#555", font=ctk.CTkFont(size=14)).pack()

    def _fetch_apps(self):
        self.api.fetch_verified_repos()
        apps = self.api.search_apps()
        self.loaded_apps = apps
        self.after(0, lambda: self._render_home(apps))

    def _render_home(self, apps):
        for w in self.home_view.winfo_children():
            w.destroy()
        self._tile_icon_labels.clear()

        if not apps:
            f = ctk.CTkFrame(self.home_view, fg_color="transparent")
            f.pack(expand=True, pady=80)
            ctk.CTkLabel(f, text="No apps found",
                         text_color="#555", font=ctk.CTkFont(size=17, weight="bold")).pack()
            ctk.CTkLabel(f, text="GitHub API may be rate-limited — login for more results.",
                         text_color="#444", font=ctk.CTkFont(size=11)).pack(pady=4)
            ctk.CTkButton(f, text="↻  Retry", width=90, height=32,
                          command=self.show_home).pack(pady=12)
            return

        COLS = 5
        grid = ctk.CTkFrame(self.home_view, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=14, pady=14)
        for c in range(COLS):
            grid.grid_columnconfigure(c, weight=1)

        for i, app in enumerate(apps):
            r, c = divmod(i, COLS)
            self._make_tile(grid, app, r, c)

    def _make_tile(self, parent, app, row, col):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        avatar_url = app.get("owner", {}).get("avatar_url")

        tile = ctk.CTkFrame(parent, fg_color="transparent", cursor="hand2")
        tile.grid(row=row, column=col, padx=8, pady=14, sticky="n")

        ph = _placeholder_icon(name, 64)
        ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(64, 64))
        icon_lbl = ctk.CTkLabel(tile, image=ctk_ph, text="", cursor="hand2")
        icon_lbl.pack()
        self._tile_icon_labels[full_name] = icon_lbl
        self._ctk_img_refs[full_name + "_ph"] = ctk_ph

        display_name = name if len(name) <= 13 else name[:12] + "…"
        name_lbl = ctk.CTkLabel(
            tile, text=display_name,
            font=ctk.CTkFont(size=11), text_color="#c8cacd",
            cursor="hand2", wraplength=76
        )
        name_lbl.pack(pady=(5, 0))

        for widget in (tile, icon_lbl, name_lbl):
            widget.bind("<Button-1>", lambda e, a=app: self.show_detail(a))

        if avatar_url:
            threading.Thread(
                target=self._load_tile_icon, args=(full_name, avatar_url), daemon=True
            ).start()

    def _load_tile_icon(self, full_name, url):
        try:
            if url in self._icon_cache:
                pil = self._icon_cache[url]
            else:
                r = requests.get(url, timeout=5)
                pil = _circle_crop(Image.open(BytesIO(r.content)), 64)
                self._icon_cache[url] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(64, 64))
            self._ctk_img_refs[full_name] = ctk_img
            self.after(0, lambda fn=full_name, ci=ctk_img: self._apply_tile_icon(fn, ci))
        except Exception:
            pass

    def _apply_tile_icon(self, full_name, ctk_img):
        lbl = self._tile_icon_labels.get(full_name)
        if lbl and lbl.winfo_exists():
            lbl.configure(image=ctk_img)

    def show_detail(self, app):
        name = app.get("name", "App")
        self.title(f"{name} - AppStore")
        self.home_view.grid_forget()
        self.detail_view.grid(row=0, column=0, sticky="nsew")
        for w in self.detail_view.winfo_children():
            w.destroy()
        self._ss_refs = []
        self._detail_readme_visible = False
        self._readme_loaded = False
        self._build_detail(app)

    def _build_detail(self, app):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        desc = app.get("description") or ""
        stars = app.get("stargazers_count", 0)
        forks = app.get("forks_count", 0)
        lang = app.get("language") or "N/A"
        pushed_at = app.get("pushed_at", "")
        owner = app.get("owner", {})
        avatar_url = owner.get("avatar_url")
        html_url = app.get("html_url", "")
        is_verified = self.api.is_verified(full_name)
        is_inst = self.db.is_installed(full_name)
        needs_upd = self.db.needs_update(full_name, pushed_at) if is_inst else False

        ctk.CTkButton(
            self.detail_view, text="← Back",
            width=72, height=30, corner_radius=15,
            fg_color="transparent", border_width=1, border_color="#2a2a50",
            font=ctk.CTkFont(size=12), text_color="#9aa0a6",
            command=self.show_home
        ).pack(anchor="w", padx=16, pady=(12, 6))

        if not is_verified:
            warn_frame = ctk.CTkFrame(self.detail_view, fg_color="#3c1a00", corner_radius=8)
            warn_frame.pack(fill="x", padx=16, pady=(0, 10))
            ctk.CTkLabel(
                warn_frame,
                text="⚠   THIS APP IS NOT VERIFIED BY APPSTORE",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#ff9800"
            ).pack(padx=14, pady=11)

        top_row = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(0, 6))

        ph = _placeholder_icon(name, 82)
        ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(82, 82))
        self._detail_icon_ref = ctk_ph
        self._detail_icon_lbl = ctk.CTkLabel(top_row, image=ctk_ph, text="")
        self._detail_icon_lbl.pack(side="left", padx=(0, 18))

        if avatar_url:
            threading.Thread(
                target=self._load_detail_icon, args=(avatar_url,), daemon=True
            ).start()

        info_col = ctk.CTkFrame(top_row, fg_color="transparent")
        info_col.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            info_col, text=name,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#e8eaed", anchor="w"
        ).pack(fill="x")

        ctk.CTkLabel(
            info_col, text=owner.get("login", ""),
            font=ctk.CTkFont(size=12), text_color="#1a73e8", anchor="w"
        ).pack(fill="x", pady=(2, 6))

        meta_row = ctk.CTkFrame(info_col, fg_color="transparent")
        meta_row.pack(fill="x")
        for icon, val in [("★", f"{stars:,}"), ("⑂", str(forks)), ("◎", lang)]:
            chip = ctk.CTkFrame(meta_row, fg_color="#1a1a30", corner_radius=10)
            chip.pack(side="left", padx=(0, 6))
            ctk.CTkLabel(chip, text=f" {icon} {val} ",
                         font=ctk.CTkFont(size=10), text_color="#9aa0a6").pack()

        ctk.CTkFrame(self.detail_view, height=1, fg_color="#1e1e40").pack(fill="x", padx=16, pady=14)

        btn_row = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 6))

        if not is_inst:
            primary_text, primary_fg, primary_hv = "Install", "#1a73e8", "#1256b4"
        elif needs_upd:
            primary_text, primary_fg, primary_hv = "Update", "#e65c00", "#b34700"
        else:
            primary_text, primary_fg, primary_hv = "Launch", "#1e7e34", "#155a24"

        self._primary_btn = ctk.CTkButton(
            btn_row, text=primary_text,
            width=150, height=42, corner_radius=21,
            fg_color=primary_fg, hover_color=primary_hv,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=lambda: self._primary_action(app, primary_text)
        )
        self._primary_btn.pack(side="left", padx=(0, 10))

        if is_inst:
            ctk.CTkButton(
                btn_row, text="Uninstall",
                width=110, height=42, corner_radius=21,
                fg_color="#2a0a0a", hover_color="#4a1010",
                text_color="#ff6b6b", border_width=1, border_color="#5a1a1a",
                font=ctk.CTkFont(size=13),
                command=lambda: self._uninstall(app)
            ).pack(side="left")

        if html_url:
            ctk.CTkButton(
                btn_row, text="GitHub ↗",
                width=90, height=42, corner_radius=21,
                fg_color="transparent", border_width=1, border_color="#2a2a50",
                text_color="#7eb3ff", font=ctk.CTkFont(size=12),
                command=lambda: webbrowser.open(html_url)
            ).pack(side="right")

        if desc:
            ctk.CTkLabel(
                self.detail_view, text=desc,
                wraplength=820, justify="left",
                font=ctk.CTkFont(size=13), text_color="#9aa0a6", anchor="w"
            ).pack(fill="x", padx=16, pady=(10, 0))

        ctk.CTkFrame(self.detail_view, height=1, fg_color="#1e1e40").pack(fill="x", padx=16, pady=14)

        self._screenshots_outer = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        self._screenshots_outer.pack(fill="x", padx=16)
        self._ss_loading_lbl = ctk.CTkLabel(
            self._screenshots_outer, text="Loading screenshots...",
            text_color="#3a3a5a", font=ctk.CTkFont(size=11)
        )
        self._ss_loading_lbl.pack(anchor="w", pady=2)
        threading.Thread(
            target=self._load_screenshots, args=(full_name,), daemon=True
        ).start()

        ctk.CTkFrame(self.detail_view, height=1, fg_color="#1e1e40").pack(fill="x", padx=16, pady=(16, 0))

        self._about_btn = ctk.CTkButton(
            self.detail_view,
            text="  About this App    ▼",
            height=50, corner_radius=0,
            fg_color="transparent", hover_color="#141428",
            text_color="#e8eaed", font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
            command=lambda: self._toggle_readme(full_name)
        )
        self._about_btn.pack(fill="x", padx=4)

        self._readme_frame = ctk.CTkFrame(self.detail_view, fg_color="#0b0b1a", corner_radius=10)

        ctk.CTkFrame(self.detail_view, height=30, fg_color="transparent").pack()

    def _load_detail_icon(self, url):
        try:
            if url in self._icon_cache:
                pil = self._icon_cache[url]
            else:
                r = requests.get(url, timeout=5)
                pil = _circle_crop(Image.open(BytesIO(r.content)), 82)
                self._icon_cache[url] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(82, 82))
            self._detail_icon_ref = ctk_img
            self.after(0, lambda: (
                self._detail_icon_lbl.configure(image=ctk_img)
                if self._detail_icon_lbl.winfo_exists() else None
            ))
        except Exception:
            pass

    def _load_screenshots(self, full_name):
        images = self.api.get_readme_images(full_name)
        self.after(0, lambda: self._render_screenshots(images))

    def _render_screenshots(self, urls):
        if self._ss_loading_lbl.winfo_exists():
            self._ss_loading_lbl.destroy()
        if not urls:
            return
        scroll = ctk.CTkScrollableFrame(
            self._screenshots_outer, height=210,
            orientation="horizontal", fg_color="transparent"
        )
        scroll.pack(fill="x", pady=4)
        for url in urls[:6]:
            threading.Thread(
                target=self._load_one_screenshot, args=(scroll, url), daemon=True
            ).start()

    def _load_one_screenshot(self, parent, url):
        try:
            r = requests.get(url, timeout=10)
            img = Image.open(BytesIO(r.content))
            h = 190
            w = int(img.width * h / img.height)
            img = img.resize((w, h), Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))
            self.after(0, lambda ci=ctk_img, p=parent: self._place_screenshot(ci, p))
        except Exception:
            pass

    def _place_screenshot(self, ctk_img, parent):
        if not parent.winfo_exists():
            return
        self._ss_refs.append(ctk_img)
        frame = ctk.CTkFrame(parent, fg_color="#1a1a2e", corner_radius=10)
        frame.pack(side="left", padx=4)
        ctk.CTkLabel(frame, image=ctk_img, text="", corner_radius=10).pack(padx=4, pady=4)

    def _toggle_readme(self, full_name):
        if self._detail_readme_visible:
            self._readme_frame.pack_forget()
            self._about_btn.configure(text="  About this App    ▼")
            self._detail_readme_visible = False
        else:
            self._about_btn.configure(text="  About this App    ▲")
            self._readme_frame.pack(fill="x", padx=16, pady=(0, 20), after=self._about_btn)
            self._detail_readme_visible = True
            if not self._readme_loaded:
                self._readme_loaded = True
                lbl = ctk.CTkLabel(
                    self._readme_frame, text="Loading README...",
                    text_color="#3a3a5a", font=ctk.CTkFont(size=11)
                )
                lbl.pack(padx=12, pady=10)
                threading.Thread(
                    target=self._fetch_readme, args=(full_name, lbl), daemon=True
                ).start()

    def _fetch_readme(self, full_name, loading_lbl):
        text = self.api.get_readme(full_name) or "No README found for this repository."
        self.after(0, lambda: self._show_readme(text, loading_lbl))

    def _show_readme(self, text, loading_lbl):
        if loading_lbl.winfo_exists():
            loading_lbl.destroy()
        tb = ctk.CTkTextbox(
            self._readme_frame,
            height=340,
            font=ctk.CTkFont(size=11, family="monospace"),
            fg_color="#0b0b1a",
            text_color="#8a9aaa",
            wrap="word",
            border_width=0
        )
        tb.pack(fill="both", padx=10, pady=10)
        tb.insert("1.0", text)
        tb.configure(state="disabled")

    def _primary_action(self, app, action):
        if action == "Install":
            self._install(app)
        elif action == "Update":
            self._install(app, is_update=True)
        elif action == "Launch":
            self._launch(app)

    def _install(self, app, is_update=False):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        repo_url = app.get("html_url", "")
        pushed_at = app.get("pushed_at", "")
        install_path = os.path.join(INSTALL_BASE, name)

        action_label = "Updating" if is_update else "Installing"
        win = ctk.CTkToplevel(self)
        win.title(f"{action_label} {name}")
        win.geometry("640x420")
        win.configure(fg_color="#0f0f1a")

        ctk.CTkLabel(
            win, text=f"{action_label} {name}",
            font=ctk.CTkFont(size=15, weight="bold"), text_color="#e8eaed"
        ).pack(pady=(18, 8), padx=16, anchor="w")

        log = ctk.CTkTextbox(
            win, height=320,
            font=ctk.CTkFont(size=11, family="monospace"),
            fg_color="#0b0b1a", text_color="#8a9aaa", border_width=0
        )
        log.pack(fill="both", padx=12, pady=(0, 14))

        def w(msg):
            self.after(0, lambda m=msg: (log.insert("end", m), log.see("end")))

        def run():
            try:
                w(f"Cloning {repo_url}...\n")
                subprocess.run(["rm", "-rf", install_path], check=True)
                res = subprocess.run(
                    ["git", "clone", "--depth", "1", repo_url, install_path],
                    capture_output=True, text=True
                )
                w(res.stdout + res.stderr)
                if res.returncode != 0:
                    w("Clone failed.\n")
                    return
                script = os.path.join(install_path, "install.sh")
                if os.path.exists(script):
                    w("Running install.sh...\n")
                    proc = subprocess.Popen(
                        ["bash", "install.sh"], cwd=install_path,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                    )
                    for line in proc.stdout:
                        w(line)
                    proc.wait()
                    if proc.returncode == 0:
                        self.db.add(full_name, name, install_path, pushed_at)
                        w(f"\nInstallation complete!\n")
                        self.after(800, lambda: self.show_detail(app))
                    else:
                        w(f"\nFailed (exit code {proc.returncode})\n")
                else:
                    self.db.add(full_name, name, install_path, pushed_at)
                    w(f"No install.sh found — cloned to:\n{install_path}\n")
                    self.after(800, lambda: self.show_detail(app))
            except Exception as e:
                w(f"Error: {e}\n")

        threading.Thread(target=run, daemon=True).start()

    def _launch(self, app):
        name = app.get("name", "")
        install_path = os.path.join(INSTALL_BASE, name)
        launch_script = os.path.join(install_path, "launch.sh")
        if os.path.exists(launch_script):
            subprocess.Popen(["bash", "launch.sh"], cwd=install_path)
        else:
            tk.messagebox.showinfo(
                "Launch",
                f"No launch.sh found in app directory.\n\nPath:\n{install_path}"
            )

    def _uninstall(self, app):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        if not tk.messagebox.askyesno("Uninstall", f"Remove {name}?"):
            return
        try:
            install_path = os.path.join(INSTALL_BASE, name)
            subprocess.run(["rm", "-rf", install_path], check=True)
            self.db.remove(full_name)
            self.show_detail(app)
        except Exception as e:
            tk.messagebox.showerror("Error", str(e))

    def _login(self):
        if self.api.token:
            tk.messagebox.showinfo("GitHub", "You are already logged in.")
            return
        data = self.api.start_device_flow()
        if not data:
            tk.messagebox.showerror("Error", "Could not connect to GitHub login service.")
            return

        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        device_code = data["device_code"]
        interval = data["interval"]

        win = ctk.CTkToplevel(self)
        win.title("GitHub Login")
        win.geometry("400x280")
        win.configure(fg_color="#0f0f1a")

        ctk.CTkLabel(win, text="Visit the URL below and enter your code:",
                     font=ctk.CTkFont(size=13), text_color="#9aa0a6").pack(pady=(26, 4))
        ctk.CTkLabel(win, text=verification_uri,
                     text_color="#7eb3ff", font=ctk.CTkFont(size=12)).pack()
        ctk.CTkLabel(win, text=user_code,
                     font=ctk.CTkFont(size=32, weight="bold"),
                     text_color="#e8eaed").pack(pady=16)
        ctk.CTkButton(win, text="Open in Browser",
                      width=160, height=36, corner_radius=18,
                      command=lambda: webbrowser.open(verification_uri)).pack(pady=4)

        def wait():
            token = self.api.poll_for_token(device_code, interval)
            if token:
                self.api.set_token(token)
                self.after(0, lambda: (
                    win.destroy(),
                    self._profile_btn.configure(text_color="#34a853")
                ))

        threading.Thread(target=wait, daemon=True).start()


if __name__ == "__main__":
    app = AppStoreApp()
    app.mainloop()
