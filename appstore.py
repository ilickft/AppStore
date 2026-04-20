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
import signal
import shutil
from PIL import Image, ImageDraw
from io import BytesIO

GITHUB_API_BASE = "https://api.github.com"
VERIFIED_REPOS_URL = "https://raw.githubusercontent.com/ilickft/AppStore/refs/heads/main/repos.txt"
APPSTORE_REPO_URL = "https://github.com/ilickft/AppStore/"
APPSTORE_VERSION = "3.6.5"
INSTALL_BASE = os.path.expanduser("~/.appstore/apps")
INSTALL_DB_PATH = os.path.expanduser("~/.appstore/installed.json")
CONFIG_PATH = os.path.expanduser("~/.config/appstore/config.json")
APP_CACHE_PATH = os.path.expanduser("~/.appstore/cache.json")

os.makedirs(INSTALL_BASE, exist_ok=True)
os.makedirs(os.path.dirname(INSTALL_DB_PATH), exist_ok=True)
os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)

BG_MAIN    = ("#f2f2fc", "#0f0f1a")
BG_HEADER  = ("#e0e0f0", "#12122a")
BG_CARD    = ("#e8e8f8", "#12122a")
BG_CARD2   = ("#dcdcf0", "#1a1a2e")
BG_INPUT   = ("#d4d4ec", "#1a1a30")
BG_HOVER   = ("#c8c8e4", "#1e1e40")
BG_SECTION = ("#ebebfc", "#0d0d20")
BG_DARK    = ("#d0d0e8", "#0b0b1a")
TEXT_PRI   = ("#1a1a2e", "#e8eaed")
TEXT_SEC   = ("#4a5068", "#9aa0a6")
TEXT_MUTED = ("#888899", "#555555")
BORDER     = ("#c0c0d8", "#2a2a50")
SEP        = ("#c0c0d8", "#1e1e40")

ACCENT_COLORS = {
    "Blue":   "#1a73e8",
    "Purple": "#7c3aed",
    "Green":  "#1e7e34",
    "Teal":   "#0d9488",
    "Orange": "#ea580c",
    "Pink":   "#db2777",
}


def _round_sq_crop(img, size):
    try:
        radius = max(size // 4, 1)
        img = img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(img, mask=mask)
        return out
    except Exception:
        return Image.new("RGBA", (size, size), (30, 30, 50, 255))


def _placeholder_icon(name, size):
    try:
        palette = ["#1a3a6a", "#2d1a6a", "#1a4a3a", "#4a2d1a", "#3a1a4a", "#1a4a4a", "#4a1a2e"]
        color = palette[sum(ord(c) for c in (name or "?")) % len(palette)]
        radius = max(size // 4, 1)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=color)
        letter = (name or "?")[0].upper()
        draw.text((size // 2 - size // 9, size // 2 - size // 7), letter, fill="#ffffff")
        return img
    except Exception:
        return Image.new("RGBA", (size, size), (30, 30, 50, 255))


class InstalledDB:
    def __init__(self):
        self._db = {}
        self._load()

    def _load(self):
        if os.path.exists(INSTALL_DB_PATH):
            try:
                with open(INSTALL_DB_PATH) as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._db = data
                    else:
                        self._db = {}
            except (json.JSONDecodeError, OSError, ValueError):
                self._db = {}

    def _save(self):
        try:
            with open(INSTALL_DB_PATH, "w") as f:
                json.dump(self._db, f, indent=2)
        except OSError:
            pass

    def is_installed(self, full_name):
        return full_name in self._db and os.path.isdir(self._db[full_name].get("path", ""))

    def get(self, full_name):
        return self._db.get(full_name)

    def add(self, full_name, name, path, pushed_at, app_data=None):
        self._db[full_name] = {
            "name": name,
            "path": path,
            "pushed_at": pushed_at,
            "app_data": app_data
        }
        self._save()

    def get_all_installed(self):
        apps = []
        if not os.path.exists(INSTALL_BASE):
            return apps
        try:
            entries = os.listdir(INSTALL_BASE)
        except OSError:
            return apps
        for name in entries:
            path = os.path.join(INSTALL_BASE, name)
            if not os.path.isdir(path):
                continue
            db_entry = next((v for v in self._db.values() if v.get("name") == name), None)
            app_data = db_entry.get("app_data") if db_entry else None
            if app_data:
                app_data = app_data.copy()
            else:
                full_name = next((k for k, v in self._db.items() if v.get("name") == name), f"local/{name}")
                app_data = {
                    "full_name": full_name,
                    "name": name,
                    "description": "Locally installed application.",
                    "stargazers_count": 0,
                    "forks_count": 0,
                    "language": "Bash",
                    "pushed_at": db_entry.get("pushed_at", "") if db_entry else "",
                    "html_url": "",
                    "owner": {"login": "local", "avatar_url": ""},
                    "subdir": name,
                    "repo_name": "local",
                    "category": "Installed",
                    "default_branch": "main"
                }
            app_data["icon_path"] = os.path.join(path, "icon.png")
            app_data["readme_path"] = os.path.join(path, "readme.md")
            app_data["category"] = "Installed"
            apps.append(app_data)
        return apps

    def remove(self, full_name):
        self._db.pop(full_name, None)
        self._save()

    def needs_update(self, full_name, current_pushed_at):
        entry = self._db.get(full_name)
        return bool(entry and entry.get("pushed_at", "") < current_pushed_at)


class ConfigDB:
    def __init__(self):
        self._path = CONFIG_PATH
        self._data = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._data = data
                    else:
                        self._data = {}
            except (json.JSONDecodeError, OSError, ValueError):
                self._data = {}

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
            os.chmod(self._path, 0o600)
        except OSError:
            pass

    def get_token(self):
        return self._data.get("token")

    def set_token(self, token):
        self._data["token"] = token
        self._save()

    def clear_token(self):
        self._data.pop("token", None)
        self._data.pop("username", None)
        self._save()

    def set_username(self, username):
        self._data["username"] = username
        self._save()

    def get_username(self):
        return self._data.get("username")

    def set_display_name(self, name):
        self._data["display_name"] = name
        self._save()

    def get_display_name(self):
        return self._data.get("display_name") or self.get_username()

    def get_client_id(self):
        return self._data.get("client_id")

    def set_client_id(self, client_id):
        self._data["client_id"] = client_id
        self._save()

    def get_theme(self):
        return self._data.get("theme", "dark")

    def set_theme(self, theme):
        self._data["theme"] = theme
        self._save()

    def get_auto_update(self):
        return self._data.get("auto_update", False)

    def set_auto_update(self, val):
        self._data["auto_update"] = bool(val)
        self._save()

    def get_fetch_errors(self):
        return self._data.get("fetch_errors", {})

    def set_fetch_errors(self, errors_dict):
        self._data["fetch_errors"] = errors_dict
        self._save()

    def get_grid_cols(self):
        return int(self._data.get("grid_cols", 5))

    def set_grid_cols(self, val):
        self._data["grid_cols"] = int(val)
        self._save()

    def get_icon_size(self):
        return int(self._data.get("icon_size", 64))

    def set_icon_size(self, val):
        self._data["icon_size"] = int(val)
        self._save()

    def get_accent_color(self):
        return self._data.get("accent_color", "#1a73e8")

    def set_accent_color(self, color):
        self._data["accent_color"] = color
        self._save()

    def get_show_category_badge(self):
        return self._data.get("show_category_badge", True)

    def set_show_category_badge(self, val):
        self._data["show_category_badge"] = bool(val)
        self._save()

    def get_compact_downloads(self):
        return self._data.get("compact_downloads", False)

    def set_compact_downloads(self, val):
        self._data["compact_downloads"] = bool(val)
        self._save()


class CacheDB:
    def __init__(self):
        self._path = APP_CACHE_PATH

    def load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else None
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        return None

    def save(self, apps):
        try:
            with open(self._path, "w") as f:
                json.dump(apps, f, indent=2)
        except OSError:
            pass

    def clear(self):
        if os.path.exists(self._path):
            try:
                os.remove(self._path)
            except OSError:
                pass

    def size_str(self):
        try:
            if os.path.exists(self._path):
                size = os.path.getsize(self._path)
                if size > 1024 * 1024:
                    return f"{size / (1024*1024):.1f} MB"
                return f"{size // 1024} KB"
        except OSError:
            pass
        return "0 KB"


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
        if token:
            self.headers["Authorization"] = f"token {token}"
        elif "Authorization" in self.headers:
            del self.headers["Authorization"]

    def get_current_user(self):
        if not self.token:
            return None
        try:
            r = requests.get(f"{GITHUB_API_BASE}/user", headers=self.headers, timeout=8)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                return None
        except requests.RequestException:
            pass
        return None

    def fetch_verified_repos(self):
        try:
            r = requests.get(VERIFIED_REPOS_URL, timeout=5)
            if r.status_code == 200:
                self.verified_repos = {
                    line.strip() for line in r.text.splitlines()
                    if line.strip() and "/" in line and not line.lower().startswith("example")
                }
        except requests.RequestException:
            pass
        return self.verified_repos

    def is_verified(self, full_name):
        if full_name.startswith("App-Store-tmx/") or full_name == "ilickft/AppStore":
            return True
        return full_name in self.verified_repos

    def search_by_topic(self, topic):
        apps = []
        url = f"{GITHUB_API_BASE}/search/repositories?q=topic:{topic}"
        try:
            r = requests.get(url, headers=self.headers, timeout=12)
            if r.status_code == 200:
                items = r.json().get("items", [])
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    app = {
                        "full_name": item.get("full_name", ""),
                        "name": item.get("name", ""),
                        "description": item.get("description"),
                        "stargazers_count": item.get("stargazers_count", 0),
                        "forks_count": item.get("forks_count", 0),
                        "language": item.get("language"),
                        "pushed_at": item.get("pushed_at", ""),
                        "html_url": item.get("html_url", ""),
                        "owner": item.get("owner", {}),
                        "subdir": "",
                        "repo_name": item.get("name", ""),
                        "default_branch": item.get("default_branch", "main"),
                        "icon_url": f"https://raw.githubusercontent.com/{item.get('full_name', '')}/{item.get('default_branch', 'main')}/icon.png"
                    }
                    apps.append(app)
            elif r.status_code == 403:
                pass
        except requests.RequestException:
            pass
        return apps

    def search_apps(self):
        all_apps = []
        errors = []

        repos = ["App-Store-tmx/Games", "App-Store-tmx/Apps"]
        for repo_full_name in repos:
            url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents"
            try:
                r = requests.get(url, headers=self.headers, timeout=12)
                if r.status_code == 200:
                    items = r.json()
                    if not isinstance(items, list):
                        errors.append(f"Unexpected response from {repo_full_name}")
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "dir" and not item.get("name", "").startswith("."):
                            category = "Games" if "Games" in repo_full_name else "Apps"
                            app = {
                                "full_name": f"{repo_full_name}:{item['name']}",
                                "name": item["name"],
                                "description": f"An app from the {category} store.",
                                "stargazers_count": 0, "forks_count": 0, "language": "Bash",
                                "pushed_at": "", "html_url": f"https://github.com/{repo_full_name}",
                                "owner": {"login": repo_full_name.split('/')[0], "avatar_url": ""},
                                "subdir": item["name"], "repo_name": repo_full_name.split('/')[1],
                                "category": category,
                                "default_branch": "main",
                                "icon_url": f"https://raw.githubusercontent.com/{repo_full_name}/main/{item['name']}/icon.png"
                            }
                            all_apps.append(app)
                elif r.status_code == 404:
                    errors.append(f"{repo_full_name} not found (404)")
                elif r.status_code == 403:
                    errors.append(f"Rate limited fetching {repo_full_name}")
                else:
                    errors.append(f"Failed to load {repo_full_name} (HTTP {r.status_code})")
            except requests.Timeout:
                errors.append(f"Timeout loading {repo_full_name}")
            except requests.ConnectionError:
                errors.append(f"Network error loading {repo_full_name}")
            except requests.RequestException as e:
                errors.append(f"Error loading {repo_full_name}: {str(e)[:40]}")

        for tag, cat in [("termux-desk-app", "Apps"), ("termux-desk-game", "Games"),
                         ("termux-desk", "Public"), ("termux-desk-tool", "Public")]:
            try:
                tagged = self.search_by_topic(tag)
                for a in tagged:
                    a["category"] = cat
                    all_apps.append(a)
            except Exception:
                pass

        unique = []
        seen = set()
        for a in all_apps:
            key = (a["full_name"], a.get("category", ""))
            if key not in seen:
                unique.append(a)
                seen.add(key)
        return unique, errors

    def _get_repo(self, full_name):
        try:
            r = requests.get(f"{GITHUB_API_BASE}/repos/{full_name}", headers=self.headers, timeout=5)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        return None

    def get_readme(self, full_name, default_branch="main"):
        try:
            if ":" in full_name:
                repo, subdir = full_name.split(":", 1)
                url = f"{GITHUB_API_BASE}/repos/{repo}/readme/{subdir}?ref={default_branch}"
            else:
                url = f"{GITHUB_API_BASE}/repos/{full_name}/readme?ref={default_branch}"
            r = requests.get(url, headers=self.headers, timeout=5)
            if r.status_code == 200:
                dl = r.json().get("download_url")
                if dl:
                    return requests.get(dl, timeout=8).text
        except requests.RequestException:
            pass
        return None

    def get_readme_images(self, full_name, default_branch="main"):
        text = self.get_readme(full_name, default_branch)
        if not text:
            return []
        return re.findall(r'!\[.*?\]\((.*?)\)', text)

    def check_appstore_update(self):
        try:
            url = f"{GITHUB_API_BASE}/repos/ilickft/AppStore/contents"
            r = requests.get(url, headers=self.headers, timeout=8)
            if r.status_code == 200:
                versions = []
                for item in r.json():
                    name = item.get("name", "")
                    if re.match(r'^\d+\.\d+\.\d+$', name):
                        versions.append(name)
                if versions:
                    versions.sort(key=lambda x: [int(v) for v in x.split('.')])
                    return versions[-1]
        except requests.RequestException:
            pass
        return None

    def start_device_flow(self, client_id):
        try:
            r = requests.post(
                "https://github.com/login/device/code",
                data={"client_id": client_id, "scope": "public_repo"},
                headers={"Accept": "application/json"}, timeout=10
            )
            if r.status_code == 200:
                return r.json()
            return None
        except requests.RequestException:
            return None

    def poll_for_token(self, client_id, device_code, interval):
        data = {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        for _ in range(60):
            try:
                r = requests.post(
                    "https://github.com/login/oauth/access_token",
                    data=data, headers={"Accept": "application/json"}, timeout=10
                )
                d = r.json()
                if "access_token" in d:
                    return d["access_token"]
                err = d.get("error", "")
                if err not in ("authorization_pending", "slow_down"):
                    return None
                if err == "slow_down":
                    interval += 5
            except requests.RequestException:
                return None
            time.sleep(interval)
        return None


class AppStoreApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Termux AppStore")
        self.geometry("680x480")
        ctk.set_default_color_theme("blue")

        self.api = GitHubAPI()
        self.config_db = ConfigDB()
        self.db = InstalledDB()
        self.cache_db = CacheDB()
        self.remote_appstore_version = None

        ctk.set_appearance_mode(self.config_db.get_theme())
        self.configure(fg_color=BG_MAIN)

        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            try:
                img = tk.PhotoImage(file=icon_path)
                self.wm_iconphoto(True, img)
            except tk.TclError:
                pass

        saved_token = self.config_db.get_token()
        if saved_token:
            self.api.set_token(saved_token)

        self.loaded_apps = []
        self._apps_fetched = False
        self._download_history = []
        self._queue_lock = threading.Lock()
        self._queue_worker_running = False
        self._active_view = "home"

        self._search_after = None
        self._search_visible = False
        self._icon_cache = {}
        self._ctk_img_refs = {}
        self._tile_icon_labels = {}
        self._ss_refs = []
        self._detail_readme_visible = False
        self._readme_loaded = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.current_category = "All"
        self._build_header()
        self._build_tabs()
        self._build_search_row()
        self._build_body()
        self.show_home()
        threading.Thread(target=self._check_updates_silent, daemon=True).start()

        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)
        self.bind_all("<MouseWheel>", self._on_mousewheel)

    def _get_task(self, full_name):
        with self._queue_lock:
            for t in self._download_history:
                if (t["full_name"] == full_name
                        and not t.get("finished")
                        and not t.get("error")
                        and not t.get("cancelled")):
                    return t
        return None

    def _on_mousewheel(self, event):
        view_map = {
            "home": self.home_view,
            "downloads": self.downloads_view,
            "settings": self._settings_view,
        }
        sf = view_map.get(self._active_view, self.detail_view)
        try:
            canvas = getattr(sf, "_parent_canvas", getattr(sf, "_canvas", None))
            if not canvas:
                return
            if event.num == 4:
                canvas.yview_scroll(-3, "units")
            elif event.num == 5:
                canvas.yview_scroll(3, "units")
            elif hasattr(event, "delta") and event.delta != 0:
                canvas.yview_scroll(int(-1 * (event.delta / 30)), "units")
        except Exception:
            pass

    def _check_updates_silent(self):
        try:
            remote_v = self.api.check_appstore_update()
            self.remote_appstore_version = remote_v
            if remote_v and remote_v != APPSTORE_VERSION:
                if self.config_db.get_auto_update():
                    self.after(0, self._do_update_appstore)
                else:
                    self.after(0, lambda: self._update_btn.configure(text="⤒ ●", text_color="#ff9800"))
            elif remote_v == APPSTORE_VERSION:
                self.after(0, lambda: (
                    self._update_btn.configure(text="⤒ ●", text_color="#34a853")
                    if self._update_btn.winfo_exists() else None
                ))
        except Exception:
            pass

    def _build_header(self):
        hdr = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color=BG_HEADER)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)
        hdr.grid_propagate(False)

        ctk.CTkLabel(
            hdr, text="  ◈  AppStore",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=self.config_db.get_accent_color()
        ).grid(row=0, column=0, padx=12, pady=10, sticky="w")

        icons_frame = ctk.CTkFrame(hdr, fg_color="transparent")
        icons_frame.grid(row=0, column=2, padx=10, sticky="e")

        ibtn = dict(width=34, height=34, corner_radius=17,
                    fg_color="transparent", hover_color=BG_HOVER,
                    font=ctk.CTkFont(size=17))

        ctk.CTkButton(icons_frame, text="⌕", command=self._toggle_search, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons_frame, text="⌂", command=self.show_home, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons_frame, text="⬇", command=self.show_downloads, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons_frame, text="↻", command=self._force_refresh_apps, **ibtn).pack(side="left", padx=1)

        self._update_btn = ctk.CTkButton(icons_frame, text="⤒", command=self._update_appstore, **ibtn)
        self._update_btn.pack(side="left", padx=1)

        self._settings_btn = ctk.CTkButton(icons_frame, text="⚙", command=self._toggle_settings, **ibtn)
        self._settings_btn.pack(side="left", padx=(1, 4))

    def _build_tabs(self):
        self._tabs_row = ctk.CTkFrame(self, height=40, corner_radius=0, fg_color=BG_SECTION)
        self._tabs_row.grid(row=1, column=0, sticky="ew")

        tab_names = ["All", "Apps", "Games", "Public", "Installed"]
        self._tab_btns = {}

        for name in tab_names:
            active = name == self.current_category
            btn = ctk.CTkButton(
                self._tabs_row, text=name, width=80, height=30, corner_radius=15,
                fg_color=self.config_db.get_accent_color() if active else "transparent",
                text_color=("#ffffff" if active else TEXT_SEC),
                font=ctk.CTkFont(size=13, weight="bold" if active else "normal"),
                command=lambda n=name: self._set_category(n)
            )
            btn.pack(side="left", padx=5, pady=5)
            if name == "All":
                btn.pack_configure(padx=(20, 5))
            self._tab_btns[name] = btn

    def _set_category(self, cat):
        self.current_category = cat
        accent = self.config_db.get_accent_color()
        for name, btn in self._tab_btns.items():
            if cat == name:
                btn.configure(fg_color=accent, text_color="#ffffff", font=ctk.CTkFont(size=13, weight="bold"))
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_SEC, font=ctk.CTkFont(size=13))
        if self._active_view != "home":
            self.show_home()
        else:
            self._apply_filter()

    def _update_appstore(self):
        conf = ctk.CTkToplevel(self)
        conf.title("Confirm Update")
        conf.geometry("380x200")
        conf.configure(fg_color=BG_MAIN)

        ctk.CTkLabel(conf, text="Update AppStore to the latest version?",
                     font=ctk.CTkFont(size=14)).pack(pady=40)

        btns = ctk.CTkFrame(conf, fg_color="transparent")
        btns.pack(fill="x", side="bottom", pady=20)

        def start_upd():
            conf.destroy()
            self._do_update_appstore()

        ctk.CTkButton(btns, text="Yes, Update", width=110, height=36,
                      fg_color=self.config_db.get_accent_color(), command=start_upd).pack(side="right", padx=15)
        ctk.CTkButton(btns, text="Cancel", width=90, height=36,
                      fg_color=BG_CARD2, command=conf.destroy).pack(side="right", padx=5)

    def _do_update_appstore(self):
        win = ctk.CTkToplevel(self)
        win.title("Updating AppStore")
        win.geometry("540x450")
        win.configure(fg_color=BG_MAIN)

        ctk.CTkLabel(win, text="Updating to latest version...",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(15, 5))

        log = ctk.CTkTextbox(win, fg_color=BG_DARK, border_width=1, border_color=BORDER,
                             font=ctk.CTkFont(family="monospace", size=11))
        log.pack(fill="both", expand=True, padx=15, pady=10)

        btn_frame = ctk.CTkFrame(win, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=(0, 15))

        close_btn = ctk.CTkButton(btn_frame, text="Close", state="disabled", width=100, command=win.destroy)
        close_btn.pack(side="right")

        def w(txt):
            if win.winfo_exists():
                self.after(0, lambda: (log.insert("end", txt), log.see("end")))

        def run():
            try:
                w("Cleaning temporary directory...\n")
                tmp = os.path.expanduser("~/.appstore_tmp_update")
                if os.path.exists(tmp):
                    subprocess.run(["rm", "-rf", tmp], check=True)

                w("Cloning latest version from GitHub...\n")
                proc_git = subprocess.Popen(
                    ["git", "clone", "--depth", "1", APPSTORE_REPO_URL, tmp],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                )
                for line in proc_git.stdout:
                    w(line)
                proc_git.wait()

                if proc_git.returncode != 0:
                    w("\nError: git clone failed.\n")
                    return

                script = os.path.join(tmp, "install.sh")
                if os.path.exists(script):
                    w("\nExecuting install.sh...\n")
                    proc = subprocess.Popen(
                        ["bash", "install.sh"], cwd=tmp,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                    )
                    for line in proc.stdout:
                        w(line)
                    proc.wait()
                    if proc.returncode == 0:
                        w("\n" + "=" * 40 + "\nUPDATE SUCCESSFUL!\n" + "=" * 40 + "\n")
                        w("Please restart the AppStore to apply changes.\n")
                    else:
                        w(f"\nUpdate script failed with code {proc.returncode}\n")
                else:
                    w("\nError: install.sh not found in the repository.\n")
            except subprocess.SubprocessError as e:
                w(f"\nSubprocess error: {e}\n")
            except OSError as e:
                w(f"\nOS error: {e}\n")
            except Exception as e:
                w(f"\nUnexpected error: {e}\n")
            finally:
                if win.winfo_exists():
                    self.after(0, lambda: close_btn.configure(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def _build_search_row(self):
        self._search_row = ctk.CTkFrame(self, height=46, corner_radius=0, fg_color=BG_SECTION)
        self._search_entry = ctk.CTkEntry(
            self._search_row,
            placeholder_text="Search apps by name, description or topic...",
            height=32, font=ctk.CTkFont(size=13),
            fg_color=BG_INPUT, border_color=BORDER, border_width=1,
        )
        self._search_entry.pack(fill="x", padx=14, pady=7)
        self._search_entry.bind("<KeyRelease>", self._on_search_key)

    def _build_body(self):
        self.body = ctk.CTkFrame(self, fg_color=BG_MAIN, corner_radius=0)
        self.body.grid(row=3, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.home_view = ctk.CTkScrollableFrame(self.body, fg_color=BG_MAIN, corner_radius=0)
        self.home_view.grid(row=0, column=0, sticky="nsew")

        self.detail_container = ctk.CTkFrame(self.body, fg_color=BG_MAIN, corner_radius=0)

        self.detail_top_bar = ctk.CTkFrame(self.detail_container, height=48, fg_color=BG_HEADER, corner_radius=0)
        self.detail_top_bar.pack(fill="x")

        self._back_btn = ctk.CTkButton(
            self.detail_top_bar, text="←  Back",
            width=80, height=30, corner_radius=15,
            fg_color="transparent", border_width=1, border_color=BORDER,
            font=ctk.CTkFont(size=12), text_color=TEXT_SEC,
            command=self.show_home
        )
        self._back_btn.pack(side="left", padx=16, pady=9)

        self.detail_view = ctk.CTkScrollableFrame(self.detail_container, fg_color=BG_MAIN, corner_radius=0)
        self.detail_view.pack(fill="both", expand=True)

        self.downloads_view = ctk.CTkScrollableFrame(self.body, fg_color=BG_MAIN, corner_radius=0)

        self._settings_view = ctk.CTkScrollableFrame(self.body, fg_color=BG_MAIN, corner_radius=0)

    def show_downloads(self):
        self._active_view = "downloads"
        self.title("Downloads - AppStore")
        self.home_view.grid_forget()
        self.detail_container.grid_forget()
        self._settings_view.grid_forget()
        self._tabs_row.grid_forget()
        if self._search_visible:
            self._search_row.grid_forget()
        self.downloads_view.grid(row=0, column=0, sticky="nsew")
        self._render_downloads_list()

    def _render_downloads_list(self):
        if self._active_view != "downloads":
            return

        for w in self.downloads_view.winfo_children():
            w.destroy()

        header_row = ctk.CTkFrame(self.downloads_view, fg_color="transparent")
        header_row.pack(fill="x", padx=20, pady=(20, 10))

        ctk.CTkLabel(header_row, text="Download History",
                     font=ctk.CTkFont(size=18, weight="bold"), text_color=TEXT_PRI).pack(side="left")

        with self._queue_lock:
            history = list(self._download_history)

        completed = [t for t in history if t.get("finished") or t.get("error") or t.get("cancelled")]
        if completed:
            def clear_done():
                with self._queue_lock:
                    self._download_history[:] = [
                        t for t in self._download_history
                        if not (t.get("finished") or t.get("error") or t.get("cancelled"))
                    ]
                self._render_downloads_list()

            ctk.CTkButton(header_row, text="Clear", width=70, height=28, corner_radius=14,
                          fg_color=BG_CARD2, hover_color=BG_HOVER, text_color=TEXT_SEC,
                          font=ctk.CTkFont(size=12), command=clear_done).pack(side="right")

        if not history:
            ctk.CTkLabel(self.downloads_view, text="No downloads yet.",
                         text_color=TEXT_MUTED, font=ctk.CTkFont(size=13)).pack(pady=40)
            return

        status_colors = {
            "Pending": "#9aa0a6",
            "Cloning": "#1a73e8",
            "Downloading": "#1a73e8",
            "Paused": "#ff9800",
            "Checking out": "#1a73e8",
            "Installing": "#ff9800",
            "Completed": "#34a853",
            "Failed": "#ff4b4b",
            "Cancelled": "#666",
        }

        compact = self.config_db.get_compact_downloads()

        for task in history:
            app = task["app"]
            status = task.get("status", "Pending")
            progress = task.get("progress", 0.0)
            is_done = task.get("finished") or task.get("error") or task.get("cancelled")
            paused = task.get("paused", False)

            card = ctk.CTkFrame(self.downloads_view, fg_color=BG_CARD, corner_radius=10)
            card.pack(fill="x", padx=20, pady=3 if compact else 4)

            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(8 if compact else 10, 4))

            ctk.CTkLabel(top, text=app.get("name", ""),
                         font=ctk.CTkFont(size=13, weight="bold"), text_color=TEXT_PRI).pack(side="left")

            sc = status_colors.get(status, "#9aa0a6")
            ctk.CTkLabel(top, text=status, font=ctk.CTkFont(size=11), text_color=sc).pack(side="right")

            if task.get("error_msg") and task.get("error"):
                ctk.CTkLabel(card, text=f"Error: {task['error_msg']}", font=ctk.CTkFont(size=10),
                             text_color="#ff6b6b", anchor="w").pack(fill="x", padx=12, pady=(0, 2))

            if not is_done:
                prog = ctk.CTkProgressBar(card, height=5, corner_radius=3,
                                          progress_color=self.config_db.get_accent_color())
                prog.pack(fill="x", padx=12, pady=(0, 5))
                prog.set(progress)

            if not compact:
                btn_row = ctk.CTkFrame(card, fg_color="transparent")
                btn_row.pack(fill="x", padx=12, pady=(0, 10))

                if status == "Pending":
                    ctk.CTkButton(btn_row, text="Cancel", width=80, height=26, corner_radius=13,
                                  fg_color="#2a0a0a", hover_color="#4a1010", text_color="#ff6b6b",
                                  border_width=1, border_color="#5a1a1a", font=ctk.CTkFont(size=11),
                                  command=lambda t=task: self._cancel_task(t)).pack(side="left", padx=(0, 6))

                elif status in ("Cloning", "Downloading", "Paused"):
                    if not paused:
                        ctk.CTkButton(btn_row, text="⏸ Pause", width=90, height=26, corner_radius=13,
                                      fg_color=BG_HOVER, hover_color=BG_CARD2, text_color="#7eb3ff",
                                      font=ctk.CTkFont(size=11),
                                      command=lambda t=task: self._pause_task(t)).pack(side="left", padx=(0, 6))
                    else:
                        ctk.CTkButton(btn_row, text="▶ Resume", width=90, height=26, corner_radius=13,
                                      fg_color=BG_HOVER, hover_color=BG_CARD2, text_color="#34a853",
                                      font=ctk.CTkFont(size=11),
                                      command=lambda t=task: self._resume_task(t)).pack(side="left", padx=(0, 6))
                    ctk.CTkButton(btn_row, text="Cancel", width=80, height=26, corner_radius=13,
                                  fg_color="#2a0a0a", hover_color="#4a1010", text_color="#ff6b6b",
                                  border_width=1, border_color="#5a1a1a", font=ctk.CTkFont(size=11),
                                  command=lambda t=task: self._cancel_task(t)).pack(side="left", padx=(0, 6))

                elif status == "Completed":
                    name = app.get("name", "")
                    install_path = os.path.join(INSTALL_BASE, name)
                    launch_script = os.path.join(install_path, "launch.sh")

                    if os.path.exists(launch_script):
                        ctk.CTkButton(btn_row, text="▶ Launch", width=90, height=26, corner_radius=13,
                                      fg_color="#1e7e34", hover_color="#155a24",
                                      font=ctk.CTkFont(size=11),
                                      command=lambda p=install_path: subprocess.Popen(
                                          ["bash", "launch.sh"], cwd=p)).pack(side="left", padx=(0, 6))

                    ctk.CTkButton(btn_row, text="Uninstall", width=90, height=26, corner_radius=13,
                                  fg_color="#2a0a0a", hover_color="#4a1010", text_color="#ff6b6b",
                                  border_width=1, border_color="#5a1a1a", font=ctk.CTkFont(size=11),
                                  command=lambda a=app, t=task: self._uninstall_from_history(a, t)).pack(
                        side="left", padx=(0, 6))

    def _pause_task(self, task):
        proc = task.get("proc")
        if proc and proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGSTOP)
                task["paused"] = True
                task["status"] = "Paused"
                self._render_downloads_list()
                if (self._active_view == "detail"
                        and getattr(self, "_current_viewing_fn", None) == task["full_name"]):
                    self._refresh_action_area(task["app"])
            except OSError:
                pass

    def _resume_task(self, task):
        proc = task.get("proc")
        if proc and proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGCONT)
                task["paused"] = False
                task["status"] = "Cloning" if task.get("phase") == "clone" else "Installing"
                self._render_downloads_list()
                if (self._active_view == "detail"
                        and getattr(self, "_current_viewing_fn", None) == task["full_name"]):
                    self._refresh_action_area(task["app"])
            except OSError:
                pass

    def _cancel_task(self, task):
        task["cancelled"] = True
        proc = task.get("proc")
        if proc and proc.poll() is None:
            try:
                if task.get("paused"):
                    os.kill(proc.pid, signal.SIGCONT)
                proc.terminate()
            except OSError:
                pass
        task["status"] = "Cancelled"
        task["error"] = True
        task["finished"] = False
        self._render_downloads_list()
        if (self._active_view == "detail"
                and getattr(self, "_current_viewing_fn", None) == task["full_name"]):
            self.after(500, lambda: self._refresh_action_area(task["app"]))

    def _uninstall_from_history(self, app, task):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        if not tk.messagebox.askyesno("Uninstall", f"Remove {name}?"):
            return
        try:
            install_path = os.path.join(INSTALL_BASE, name)
            un_script = os.path.join(install_path, "uninstall.sh")
            if os.path.exists(un_script):
                subprocess.run(["bash", "uninstall.sh"], cwd=install_path)
            subprocess.run(["rm", "-rf", install_path], check=True)
            self.db.remove(full_name)
            task["status"] = "Uninstalled"
            self._render_downloads_list()
            if (self._active_view == "detail"
                    and getattr(self, "_current_viewing_fn", None) == full_name):
                self._refresh_action_area(app)
        except (subprocess.SubprocessError, OSError) as e:
            tk.messagebox.showerror("Uninstall Error", str(e))

    def _show_notification(self, message, color="#1e7e34"):
        try:
            notif = ctk.CTkFrame(self, fg_color=color, corner_radius=8)
            notif.place(relx=0.5, y=58, anchor="n")
            ctk.CTkLabel(notif, text=message, font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#ffffff").pack(padx=16, pady=8)
            self.after(4000, lambda: notif.place_forget() if notif.winfo_exists() else None)
            self.after(4200, lambda: notif.destroy() if notif.winfo_exists() else None)
        except Exception:
            pass

    def _toggle_search(self):
        if self._search_visible:
            self._search_row.grid_forget()
            self._search_visible = False
        else:
            self._search_row.grid(row=2, column=0, sticky="ew")
            self._search_visible = True
            self._search_entry.focus()

    def _on_search_key(self, _=None):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(280, self._apply_filter)

    def _apply_filter(self, _=None):
        q = self._search_entry.get().strip().lower()

        if self.current_category == "Installed":
            filtered = self.db.get_all_installed()
        elif self.current_category == "All":
            filtered = list(self.loaded_apps)
        else:
            filtered = [a for a in self.loaded_apps if a.get("category") == self.current_category]

        if q:
            filtered = [
                a for a in filtered
                if q in a.get("name", "").lower()
                or q in (a.get("description") or "").lower()
            ]

        filtered.sort(key=lambda a: a.get("name", "").lower())
        self._render_home(filtered)

    def show_home(self):
        self._active_view = "home"
        self.title("Home - AppStore")
        self.detail_container.grid_forget()
        self.downloads_view.grid_forget()
        self._settings_view.grid_forget()
        self.home_view.grid(row=0, column=0, sticky="nsew")
        self._tabs_row.grid(row=1, column=0, sticky="ew")

        if self._apps_fetched:
            self._apply_filter()
            return

        cached_apps = self.cache_db.load()
        if cached_apps:
            self.loaded_apps = cached_apps
            self._apps_fetched = True
            self._apply_filter()
            threading.Thread(target=self._fetch_apps, daemon=True).start()
            return

        for w in self.home_view.winfo_children():
            w.destroy()
        self._tile_icon_labels.clear()
        self._show_loading_state()
        threading.Thread(target=self._fetch_apps, daemon=True).start()

    def _force_refresh_apps(self):
        self._apps_fetched = False
        self.cache_db.clear()
        self.loaded_apps = []
        self._icon_cache.clear()
        self.show_home()

    def _show_loading_state(self):
        f = ctk.CTkFrame(self.home_view, fg_color="transparent")
        f.pack(expand=True, pady=80)
        ctk.CTkLabel(f, text="Fetching apps from GitHub...",
                     text_color=TEXT_MUTED, font=ctk.CTkFont(size=14)).pack()

    def _fetch_apps(self):
        try:
            self.api.fetch_verified_repos()
        except Exception:
            pass

        cached = self.cache_db.load() or []

        try:
            apps, errors = self.api.search_apps()
        except Exception as e:
            apps, errors = [], [f"Critical fetch error: {str(e)[:60]}"]

        fetch_err_counts = self.config_db.get_fetch_errors()
        final_apps = list(apps)

        for repo in ["App-Store-tmx/Games", "App-Store-tmx/Apps"]:
            has_error = any(repo in e for e in errors)
            if has_error:
                count = fetch_err_counts.get(repo, 0) + 1
                if count < 3:
                    repo_cached = [a for a in cached if a.get("full_name", "").startswith(repo)]
                    seen_fns = {a["full_name"] for a in final_apps}
                    for r_a in repo_cached:
                        if r_a["full_name"] not in seen_fns:
                            final_apps.append(r_a)
                    fetch_err_counts[repo] = count
                else:
                    fetch_err_counts[repo] = 0
            else:
                fetch_err_counts[repo] = 0

        self.config_db.set_fetch_errors(fetch_err_counts)
        self.loaded_apps = final_apps
        self.cache_db.save(final_apps)
        self._apps_fetched = True

        self.after(0, self._apply_filter)
        for err in errors:
            self.after(0, lambda e=err: self._show_notification(e, color="#c62828"))

    def _render_home(self, apps):
        for w in self.home_view.winfo_children():
            w.destroy()
        self._tile_icon_labels.clear()

        if not apps:
            f = ctk.CTkFrame(self.home_view, fg_color="transparent")
            f.pack(expand=True, pady=80)

            if self.current_category == "Installed":
                ctk.CTkLabel(f, text="Your library is empty",
                             text_color=TEXT_MUTED, font=ctk.CTkFont(size=17, weight="bold")).pack()
                ctk.CTkLabel(f, text="You don't have any installed apps yet.",
                             text_color=TEXT_MUTED, font=ctk.CTkFont(size=12), justify="center").pack(pady=10)
                ctk.CTkButton(f, text="Browse Apps", width=120, height=32,
                              fg_color=self.config_db.get_accent_color(),
                              command=lambda: self._set_category("Apps")).pack(pady=12)
            else:
                ctk.CTkLabel(f, text="No apps available",
                             text_color=TEXT_MUTED, font=ctk.CTkFont(size=17, weight="bold")).pack()
                ctk.CTkLabel(f, text="No apps/games available right now.\nTry refreshing or check your connection.",
                             text_color=TEXT_MUTED, font=ctk.CTkFont(size=12), justify="center").pack(pady=10)
                ctk.CTkButton(f, text="↻  Retry", width=90, height=32,
                              fg_color=self.config_db.get_accent_color(),
                              command=self.show_home).pack(pady=12)
            return

        COLS = self.config_db.get_grid_cols()
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
        icon_url = app.get("icon_url")
        icon_path = app.get("icon_path")
        icon_size = self.config_db.get_icon_size()
        show_badge = self.config_db.get_show_category_badge()

        tile = ctk.CTkFrame(parent, fg_color="transparent", cursor="hand2")
        tile.grid(row=row, column=col, padx=6, pady=12, sticky="n")

        icon_bg = ctk.CTkFrame(
            tile,
            width=icon_size, height=icon_size,
            fg_color=BG_CARD,
            corner_radius=icon_size // 4
        )
        icon_bg.pack()
        icon_bg.pack_propagate(False)

        ph = _placeholder_icon(name, icon_size)
        ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(icon_size, icon_size))
        icon_lbl = ctk.CTkLabel(icon_bg, image=ctk_ph, text="", cursor="hand2",
                                width=icon_size, height=icon_size)
        icon_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self._tile_icon_labels[full_name] = icon_lbl
        self._ctk_img_refs[full_name + "_ph"] = ctk_ph

        display_name = name if len(name) <= 13 else name[:12] + "…"
        name_lbl = ctk.CTkLabel(
            tile, text=display_name,
            font=ctk.CTkFont(size=11), text_color=TEXT_SEC,
            cursor="hand2", wraplength=icon_size + 14
        )
        name_lbl.pack(pady=(4, 0))

        if show_badge and app.get("category") in ("Games", "Public"):
            badge_colors = {"Games": "#6a1a8a", "Public": "#1a5a3a"}
            bc = badge_colors.get(app.get("category"), "#333")
            badge = ctk.CTkFrame(tile, fg_color=bc, corner_radius=4)
            badge.pack(pady=(2, 0))
            ctk.CTkLabel(badge, text=app.get("category", ""), font=ctk.CTkFont(size=8),
                         text_color="#ffffff").pack(padx=4, pady=1)

        for widget in (tile, icon_bg, icon_lbl, name_lbl):
            widget.bind("<Button-1>", lambda e, a=app: self.show_detail(a))

        if icon_path and os.path.exists(icon_path):
            threading.Thread(
                target=self._load_tile_icon_local, args=(full_name, icon_path, icon_size), daemon=True
            ).start()
        elif icon_url:
            threading.Thread(
                target=self._load_tile_icon, args=(full_name, icon_url, icon_size), daemon=True
            ).start()

    def _load_tile_icon(self, full_name, url, icon_size):
        try:
            cache_key = f"{url}_{icon_size}"
            if cache_key in self._icon_cache:
                pil = self._icon_cache[cache_key]
            else:
                r = requests.get(url, timeout=5)
                r.raise_for_status()
                pil = _round_sq_crop(Image.open(BytesIO(r.content)), icon_size)
                self._icon_cache[cache_key] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(icon_size, icon_size))
            self._ctk_img_refs[full_name] = ctk_img
            self.after(0, lambda fn=full_name, ci=ctk_img: self._apply_tile_icon(fn, ci))
        except Exception:
            pass

    def _load_tile_icon_local(self, full_name, path, icon_size):
        try:
            cache_key = f"{path}_{icon_size}"
            if cache_key in self._icon_cache:
                pil = self._icon_cache[cache_key]
            else:
                pil = _round_sq_crop(Image.open(path), icon_size)
                self._icon_cache[cache_key] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(icon_size, icon_size))
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
        full_name = app.get("full_name", "")
        self._current_viewing_fn = full_name

        self._active_view = "detail"
        self.title(f"{name} - AppStore")
        self.home_view.grid_forget()
        self.downloads_view.grid_forget()
        self._settings_view.grid_forget()
        self._tabs_row.grid_forget()
        if self._search_visible:
            self._search_row.grid_forget()

        self.detail_container.grid(row=0, column=0, sticky="nsew")
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
        owner = app.get("owner", {})
        is_verified = self.api.is_verified(full_name)

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

        icon_size = self.config_db.get_icon_size()
        detail_icon_size = max(icon_size + 18, 82)
        ph = _placeholder_icon(name, detail_icon_size)
        ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(detail_icon_size, detail_icon_size))
        self._detail_icon_ref = ctk_ph

        icon_bg = ctk.CTkFrame(top_row, width=detail_icon_size, height=detail_icon_size,
                               fg_color=BG_CARD, corner_radius=detail_icon_size // 4)
        icon_bg.pack(side="left", padx=(0, 18))
        icon_bg.pack_propagate(False)

        self._detail_icon_lbl = ctk.CTkLabel(icon_bg, image=ctk_ph, text="",
                                             width=detail_icon_size, height=detail_icon_size)
        self._detail_icon_lbl.place(relx=0.5, rely=0.5, anchor="center")

        icon_url = app.get("icon_url")
        icon_path = app.get("icon_path")
        if icon_path and os.path.exists(icon_path):
            threading.Thread(
                target=self._load_detail_icon_local, args=(icon_path, detail_icon_size), daemon=True
            ).start()
        elif icon_url:
            threading.Thread(
                target=self._load_detail_icon, args=(icon_url, detail_icon_size), daemon=True
            ).start()

        info_col = ctk.CTkFrame(top_row, fg_color="transparent")
        info_col.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            info_col, text=name,
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=TEXT_PRI, anchor="w"
        ).pack(fill="x")

        publisher = owner.get("login", "")
        if is_verified:
            publisher += " ✓"

        self._ver_pub_lbl = ctk.CTkLabel(
            info_col, text=f"v... | {publisher}",
            font=ctk.CTkFont(size=12), text_color=self.config_db.get_accent_color(), anchor="w"
        )
        self._ver_pub_lbl.pack(fill="x", pady=(2, 6))

        def check_version():
            remote_v = None
            repo, sub = full_name.split(":", 1) if ":" in full_name else (full_name, "")
            try:
                r = requests.get(
                    f"{GITHUB_API_BASE}/repos/{repo}/contents/{sub}",
                    headers=self.api.headers, timeout=5
                )
                if r.status_code == 200:
                    for item in r.json():
                        if (isinstance(item, dict)
                                and item.get("type") == "file"
                                and re.match(r'^v?\d+\.\d+(\.\d+)?(-[\w.]+)?$', item.get("name", ""))):
                            remote_v = item.get("name")
                            break
            except requests.RequestException:
                pass

            local_v = None
            if self.db.is_installed(full_name):
                path = os.path.join(INSTALL_BASE, name)
                try:
                    for f in os.listdir(path):
                        if (os.path.isfile(os.path.join(path, f))
                                and re.match(r'^v?\d+\.\d+(\.\d+)?(-[\w.]+)?$', f)):
                            local_v = f
                            break
                except OSError:
                    pass

            def update_ui():
                if not self.detail_view.winfo_exists() or self._current_viewing_fn != full_name:
                    return
                v_str = f"v{remote_v}" if remote_v else "v?.?.?"
                self._ver_pub_lbl.configure(text=f"{v_str} | {publisher}")
                if local_v and remote_v and local_v != remote_v:
                    self._primary_btn.configure(
                        text=f"Update to {v_str}", fg_color="#e65c00", hover_color="#b34700"
                    )
                    self._primary_btn._action_text = "Update"

            self.after(0, update_ui)

        threading.Thread(target=check_version, daemon=True).start()

        meta_row = ctk.CTkFrame(info_col, fg_color="transparent")
        meta_row.pack(fill="x")
        for icon, val in [("★", f"{stars:,}"), ("⑂", str(forks)), ("◎", lang)]:
            chip = ctk.CTkFrame(meta_row, fg_color=BG_CARD, corner_radius=10)
            chip.pack(side="left", padx=(0, 6))
            ctk.CTkLabel(chip, text=f" {icon} {val} ",
                         font=ctk.CTkFont(size=10), text_color=TEXT_SEC).pack()

        ctk.CTkFrame(self.detail_view, height=1, fg_color=SEP).pack(fill="x", padx=16, pady=14)

        self._btn_and_prog_container = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        self._btn_and_prog_container.pack(fill="x", padx=16, pady=(0, 6))
        self._refresh_action_area(app)

        if desc:
            ctk.CTkLabel(
                self.detail_view, text=desc,
                wraplength=820, justify="left",
                font=ctk.CTkFont(size=13), text_color=TEXT_SEC, anchor="w"
            ).pack(fill="x", padx=16, pady=(10, 0))

        self._ss_sep_top = ctk.CTkFrame(self.detail_view, height=1, fg_color=SEP)
        self._ss_sep_top.pack(fill="x", padx=16, pady=14)

        self._screenshots_outer = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        self._screenshots_outer.pack(fill="x", padx=16)
        self._ss_loading_lbl = ctk.CTkLabel(
            self._screenshots_outer, text="Loading screenshots...",
            text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)
        )
        self._ss_loading_lbl.pack(anchor="w", pady=2)

        self._ss_sep_bottom = ctk.CTkFrame(self.detail_view, height=1, fg_color=SEP)
        self._ss_sep_bottom.pack(fill="x", padx=16, pady=(16, 0))

        threading.Thread(target=self._load_screenshots, args=(app,), daemon=True).start()

        self._about_btn = ctk.CTkButton(
            self.detail_view,
            text="  About this App    ▼",
            height=50, corner_radius=0,
            fg_color="transparent", hover_color=BG_HOVER,
            text_color=TEXT_PRI, font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
            command=lambda: self._toggle_readme(app)
        )
        self._about_btn.pack(fill="x", padx=4)

        self._readme_frame = ctk.CTkFrame(self.detail_view, fg_color=BG_DARK, corner_radius=10)

        self._reviews_outer = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        self._reviews_outer.pack(fill="x", padx=16, pady=(16, 20))

        ctk.CTkLabel(self._reviews_outer, text="Reviews",
                     font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_PRI).pack(anchor="w", pady=(0, 10))

        self._reviews_list = ctk.CTkFrame(self._reviews_outer, fg_color="transparent")
        self._reviews_list.pack(fill="x")

        self._reviews_loading = ctk.CTkLabel(self._reviews_list, text="Loading reviews...",
                                              text_color=TEXT_MUTED, font=ctk.CTkFont(size=11))
        self._reviews_loading.pack(pady=10)

        self._write_review_btn = ctk.CTkButton(
            self._reviews_outer, text="✎ Write a Review",
            width=140, height=34, corner_radius=17,
            fg_color=BG_HOVER, hover_color=BG_CARD2, text_color="#7eb3ff",
            font=ctk.CTkFont(size=12),
            command=lambda: self._show_write_review_dialog(app)
        )
        self._write_review_btn.pack(anchor="w", pady=(10, 0))

        threading.Thread(target=self._load_reviews, args=(app,), daemon=True).start()
        ctk.CTkFrame(self.detail_view, height=30, fg_color="transparent").pack()

    def _refresh_action_area(self, app):
        for w in self._btn_and_prog_container.winfo_children():
            w.destroy()

        full_name = app.get("full_name", "")
        is_appstore = full_name == "ilickft/AppStore"
        is_inst = self.db.is_installed(full_name) or is_appstore
        pushed_at = app.get("pushed_at", "")
        needs_upd = self.db.needs_update(full_name, pushed_at) if (is_inst and not is_appstore) else False
        active_task = self._get_task(full_name)
        is_active = active_task is not None
        accent = self.config_db.get_accent_color()

        btn_row = ctk.CTkFrame(self._btn_and_prog_container, fg_color="transparent")
        btn_row.pack(fill="x")

        if is_active:
            task_status = active_task.get("status", "Working")
            self._primary_btn = ctk.CTkButton(
                btn_row, text=f"{task_status}...",
                width=150, height=42, corner_radius=21,
                fg_color=BG_CARD2, hover_color=BG_HOVER, state="disabled",
                font=ctk.CTkFont(size=14, weight="bold")
            )
            self._primary_btn.pack(side="left", padx=(0, 10))

            ctk.CTkButton(
                btn_row, text="Cancel",
                width=110, height=42, corner_radius=21,
                fg_color="#2a0a0a", hover_color="#4a1010",
                text_color="#ff6b6b", border_width=1, border_color="#5a1a1a",
                font=ctk.CTkFont(size=13),
                command=lambda: self._cancel_task(active_task)
            ).pack(side="left", padx=(0, 10))
        else:
            if is_appstore:
                if self.remote_appstore_version and self.remote_appstore_version != APPSTORE_VERSION:
                    primary_text = f"Update to {self.remote_appstore_version}"
                    primary_fg, primary_hv = "#e65c00", "#b34700"
                else:
                    primary_text = "Check & Update"
                    primary_fg = accent
                    primary_hv = accent

                self._primary_btn = ctk.CTkButton(
                    btn_row, text=primary_text,
                    width=170, height=42, corner_radius=21,
                    fg_color=primary_fg, hover_color=primary_hv,
                    font=ctk.CTkFont(size=14, weight="bold"),
                    command=lambda: self._check_and_update_appstore_silent(app)
                )
                self._primary_btn.pack(side="left", padx=(0, 10))
            else:
                if not is_inst:
                    primary_text, primary_fg, primary_hv = "Install", accent, accent
                elif needs_upd:
                    primary_text, primary_fg, primary_hv = "Update", "#e65c00", "#b34700"
                else:
                    primary_text, primary_fg, primary_hv = "Launch", "#1e7e34", "#155a24"

                self._primary_btn = ctk.CTkButton(
                    btn_row, text=primary_text,
                    width=150, height=42, corner_radius=21,
                    fg_color=primary_fg, hover_color=primary_hv,
                    font=ctk.CTkFont(size=14, weight="bold"),
                    command=lambda: self._primary_action(app, self._primary_btn.cget("text"))
                )
                self._primary_btn.pack(side="left", padx=(0, 10))

            if is_inst and not is_appstore:
                ctk.CTkButton(
                    btn_row, text="Uninstall",
                    width=110, height=42, corner_radius=21,
                    fg_color="#2a0a0a", hover_color="#4a1010",
                    text_color="#ff6b6b", border_width=1, border_color="#5a1a1a",
                    font=ctk.CTkFont(size=13),
                    command=lambda: self._uninstall(app)
                ).pack(side="left", padx=(0, 10))

                if not needs_upd:
                    self._check_upd_btn = ctk.CTkButton(
                        btn_row, text="Check for updates",
                        width=140, height=42, corner_radius=21,
                        fg_color="transparent", border_width=1, border_color=BORDER,
                        text_color="#7eb3ff", font=ctk.CTkFont(size=13),
                        command=lambda: self._check_app_update(app)
                    )
                    self._check_upd_btn.pack(side="left")

        html_url = app.get("html_url", "")
        if html_url:
            ctk.CTkButton(
                btn_row, text="GitHub ↗",
                width=90, height=42, corner_radius=21,
                fg_color="transparent", border_width=1, border_color=BORDER,
                text_color="#7eb3ff", font=ctk.CTkFont(size=12),
                command=lambda: webbrowser.open(html_url)
            ).pack(side="right")

        self._install_progress_frame = ctk.CTkFrame(self._btn_and_prog_container, fg_color="transparent")

        self._install_status_lbl = ctk.CTkLabel(
            self._install_progress_frame,
            text=active_task.get("status", "") if is_active else "",
            font=ctk.CTkFont(size=12, slant="italic"), text_color=accent
        )
        self._install_status_lbl.pack(anchor="w", padx=2)

        self._install_progress = ctk.CTkProgressBar(
            self._install_progress_frame, height=8, corner_radius=4,
            progress_color=accent, fg_color=BG_HOVER
        )
        self._install_progress.pack(fill="x", pady=(2, 10))
        self._install_progress.set(active_task.get("progress", 0) if is_active else 0)

        if is_active:
            self._install_progress_frame.pack(fill="x", pady=(8, 0))

    def _check_and_update_appstore_silent(self, app):
        self._primary_btn.configure(state="disabled", text="Checking...")

        def run():
            try:
                remote_v = self.api.check_appstore_update()
                self.remote_appstore_version = remote_v
                if remote_v and remote_v != APPSTORE_VERSION:
                    self.after(0, self._do_update_appstore)
                else:
                    self.after(0, lambda: (
                        self._show_notification("AppStore is up to date", color="#1e7e34"),
                        self._primary_btn.configure(state="normal", text="Check & Update")
                        if self._primary_btn.winfo_exists() else None
                    ))
            except Exception:
                self.after(0, lambda: (
                    self._show_notification("Could not check for updates", color="#c62828"),
                    self._primary_btn.configure(state="normal", text="Check & Update")
                    if self._primary_btn.winfo_exists() else None
                ))

        threading.Thread(target=run, daemon=True).start()

    def _load_detail_icon(self, url, size):
        try:
            cache_key = f"{url}_{size}"
            if cache_key in self._icon_cache:
                pil = self._icon_cache[cache_key]
            else:
                r = requests.get(url, timeout=5)
                r.raise_for_status()
                pil = _round_sq_crop(Image.open(BytesIO(r.content)), size)
                self._icon_cache[cache_key] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
            self._detail_icon_ref = ctk_img
            self.after(0, lambda: (
                self._detail_icon_lbl.configure(image=ctk_img)
                if self._detail_icon_lbl.winfo_exists() else None
            ))
        except Exception:
            pass

    def _load_detail_icon_local(self, path, size):
        try:
            cache_key = f"{path}_{size}"
            if cache_key in self._icon_cache:
                pil = self._icon_cache[cache_key]
            else:
                pil = _round_sq_crop(Image.open(path), size)
                self._icon_cache[cache_key] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
            self._detail_icon_ref = ctk_img
            self.after(0, lambda: (
                self._detail_icon_lbl.configure(image=ctk_img)
                if self._detail_icon_lbl.winfo_exists() else None
            ))
        except Exception:
            pass

    def _load_screenshots(self, app):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        default_branch = app.get("default_branch", "main")

        all_urls = []
        try:
            all_urls.extend(self.api.get_readme_images(full_name, default_branch))
        except Exception:
            pass

        install_path = os.path.join(INSTALL_BASE, name)
        if os.path.exists(install_path):
            try:
                for f in os.listdir(install_path):
                    if f.lower().startswith("screenshot") and f.lower().endswith((".png", ".jpg", ".jpeg")):
                        all_urls.append(os.path.join(install_path, f))
            except OSError:
                pass

        try:
            if ":" in full_name:
                repo, subdir_path = full_name.split(":", 1)
                r = requests.get(
                    f"{GITHUB_API_BASE}/repos/{repo}/contents/{subdir_path}?ref={default_branch}",
                    headers=self.api.headers, timeout=5
                )
            else:
                r = requests.get(
                    f"{GITHUB_API_BASE}/repos/{full_name}/contents/?ref={default_branch}",
                    headers=self.api.headers, timeout=5
                )
            if r.status_code == 200:
                for item in r.json():
                    if not isinstance(item, dict):
                        continue
                    fname = item.get("name", "").lower()
                    if fname.startswith("screenshot") and fname.endswith((".png", ".jpg", ".jpeg")):
                        dl_url = item.get("download_url")
                        if dl_url and dl_url not in all_urls:
                            all_urls.append(dl_url)
        except requests.RequestException:
            pass

        seen = set()
        unique_urls = [u for u in all_urls if u not in seen and not seen.add(u)]
        self.after(0, lambda: self._render_screenshots(unique_urls))

    def _render_screenshots(self, urls):
        if self._ss_loading_lbl.winfo_exists():
            self._ss_loading_lbl.destroy()

        if not urls:
            self._hide_ss_section()
            return

        self._ss_scroll = ctk.CTkScrollableFrame(
            self._screenshots_outer, height=210,
            orientation="horizontal", fg_color="transparent"
        )
        self._ss_scroll.pack(fill="x", pady=4)

        self._loaded_ss_count = 0
        self._total_ss_attempted = len(urls[:8])

        for url in urls[:8]:
            threading.Thread(
                target=self._load_one_screenshot, args=(self._ss_scroll, url), daemon=True
            ).start()

    def _hide_ss_section(self):
        for attr in ("_screenshots_outer", "_ss_sep_top", "_ss_sep_bottom"):
            w = getattr(self, attr, None)
            if w and w.winfo_exists():
                w.pack_forget()

    def _load_one_screenshot(self, parent, url):
        try:
            if url.startswith("http"):
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                img = Image.open(BytesIO(r.content))
            else:
                img = Image.open(url)

            if img.width < 100 or img.height < 100:
                return

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
        frame = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=10)
        frame.pack(side="left", padx=4)
        ctk.CTkLabel(frame, image=ctk_img, text="", corner_radius=10).pack(padx=4, pady=4)

    def _load_reviews(self, app):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        repo = full_name.split(":")[0] if ":" in full_name else full_name

        url = f"{GITHUB_API_BASE}/repos/{repo}/issues?state=open&per_page=100"
        reviews = []
        try:
            r = requests.get(url, headers=self.api.headers, timeout=10)
            if r.status_code == 200:
                for issue in r.json():
                    if not isinstance(issue, dict):
                        continue
                    if issue.get("title") == name:
                        body = issue.get("body", "")
                        rating_match = re.search(r"Ratings:\s*([1-5])", body, re.IGNORECASE)
                        comment_match = re.search(r"Comment:\s*(.*)", body, re.IGNORECASE | re.DOTALL)
                        user_match = re.search(r"User:\s*(.*)", body, re.IGNORECASE)

                        rating = rating_match.group(1) if rating_match else "0"
                        comment = comment_match.group(1).strip() if comment_match else body.strip()
                        display_name = (user_match.group(1).strip() if user_match
                                        else issue.get("user", {}).get("login", "Unknown"))

                        reviews.append({
                            "id": issue.get("number"),
                            "login": issue.get("user", {}).get("login"),
                            "user": display_name,
                            "rating": int(rating),
                            "comment": comment,
                            "avatar": issue.get("user", {}).get("avatar_url", "")
                        })
            elif r.status_code == 403:
                pass
        except requests.RequestException:
            pass
        except (ValueError, KeyError):
            pass
        self.after(0, lambda: self._render_reviews(reviews, app))

    def _render_reviews(self, reviews, app):
        if not self.detail_view.winfo_exists():
            return
        if self._reviews_loading.winfo_exists():
            self._reviews_loading.destroy()

        for w in self._reviews_list.winfo_children():
            w.destroy()

        if not reviews:
            ctk.CTkLabel(self._reviews_list, text="No reviews yet. Be the first to review!",
                         text_color=TEXT_MUTED, font=ctk.CTkFont(size=12, slant="italic")).pack(pady=10)
            return

        my_login = self.config_db.get_username()
        for i, rev in enumerate(reviews):
            card = ctk.CTkFrame(self._reviews_list, fg_color=BG_CARD, corner_radius=8)
            card.pack(fill="x", pady=5)

            header = ctk.CTkFrame(card, fg_color="transparent")
            header.pack(fill="x", padx=10, pady=(10, 5))

            left = ctk.CTkFrame(header, fg_color="transparent")
            left.pack(side="left")

            ava_size = 28
            ph = _placeholder_icon(rev["user"], ava_size)
            ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(ava_size, ava_size))
            ava_lbl = ctk.CTkLabel(left, image=ctk_ph, text="")
            ava_lbl.pack(side="left", padx=(0, 8))

            ref_id = f"rev_ava_{i}"
            self._ctk_img_refs[ref_id] = ctk_ph

            ctk.CTkLabel(left, text=rev["user"], font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=TEXT_PRI).pack(side="left")

            right = ctk.CTkFrame(header, fg_color="transparent")
            right.pack(side="right")

            stars_text = "★" * rev["rating"] + "☆" * (5 - rev["rating"])
            ctk.CTkLabel(right, text=stars_text, font=ctk.CTkFont(size=14), text_color="#ffb400").pack(side="right", padx=(10, 0))

            tools = ctk.CTkFrame(right, fg_color="transparent")
            tools.pack(side="right")

            tbtn = dict(width=24, height=24, corner_radius=12, fg_color="transparent",
                        hover_color=BG_HOVER, font=ctk.CTkFont(size=14))

            ctk.CTkButton(tools, text="↩", command=lambda r=rev: self._show_reply_dialog(app, r), **tbtn).pack(side="left", padx=2)

            if rev["login"] == my_login:
                ctk.CTkButton(tools, text="✎",
                              command=lambda r=rev: self._show_write_review_dialog(app, edit_rev=r),
                              **tbtn).pack(side="left", padx=2)
                ctk.CTkButton(tools, text="🗑", text_color="#ff4b4b",
                              command=lambda r=rev: self._delete_review(app, r),
                              **tbtn).pack(side="left", padx=2)

            ctk.CTkLabel(card, text=rev["comment"], font=ctk.CTkFont(size=12), text_color=TEXT_SEC,
                         justify="left", wraplength=480, anchor="w").pack(fill="x", padx=10, pady=(0, 10))

            if rev.get("avatar"):
                threading.Thread(
                    target=self._load_review_avatar, args=(ava_lbl, rev["avatar"], ref_id), daemon=True
                ).start()

    def _delete_review(self, app, rev):
        if not tk.messagebox.askyesno("Delete Review", "Delete your review?"):
            return

        full_name = app.get("full_name", "")
        repo = full_name.split(":")[0] if ":" in full_name else full_name
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{rev['id']}"

        def run():
            try:
                r = requests.patch(url, headers=self.api.headers, json={"state": "closed"}, timeout=10)
                if r.status_code == 200:
                    self.after(0, lambda: (
                        tk.messagebox.showinfo("Deleted", "Review deleted successfully."),
                        self._load_reviews(app)
                    ))
                else:
                    self.after(0, lambda: tk.messagebox.showerror(
                        "Error", f"Failed to delete review (HTTP {r.status_code})")
                    )
            except requests.RequestException as e:
                self.after(0, lambda: tk.messagebox.showerror("Network Error", str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _show_reply_dialog(self, app, rev):
        if not self.api.token:
            tk.messagebox.showwarning("Login Required", "You must be logged in to reply.")
            self._login()
            return

        win = ctk.CTkToplevel(self)
        win.title(f"Reply to {rev['user']}")
        win.geometry("400x300")
        win.configure(fg_color=BG_MAIN)
        win.transient(self)

        ctk.CTkLabel(win, text=f"Reply to {rev['user']}",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_PRI).pack(pady=(20, 10))

        comment_box = ctk.CTkTextbox(win, height=120, fg_color=BG_INPUT, border_color=BORDER, border_width=1)
        comment_box.pack(fill="x", padx=20, pady=10)

        def submit():
            comment = comment_box.get("1.0", "end").strip()
            if not comment:
                return

            full_name = app.get("full_name", "")
            repo = full_name.split(":")[0] if ":" in full_name else full_name
            url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{rev['id']}/comments"

            sub_btn.configure(state="disabled", text="Replying...")

            def run():
                try:
                    r = requests.post(url, headers=self.api.headers, json={"body": comment}, timeout=10)
                    if r.status_code == 201:
                        self.after(0, lambda: (
                            tk.messagebox.showinfo("Success", "Reply posted successfully!"),
                            win.destroy()
                        ))
                    else:
                        self.after(0, lambda: (
                            tk.messagebox.showerror("Error", f"Failed to post reply (HTTP {r.status_code})"),
                            sub_btn.configure(state="normal", text="Post Reply")
                        ))
                except requests.RequestException as e:
                    self.after(0, lambda: (
                        tk.messagebox.showerror("Network Error", str(e)),
                        sub_btn.configure(state="normal", text="Post Reply")
                    ))

            threading.Thread(target=run, daemon=True).start()

        sub_btn = ctk.CTkButton(win, text="Post Reply",
                                fg_color=self.config_db.get_accent_color(), command=submit)
        sub_btn.pack(pady=20)

    def _load_review_avatar(self, label, url, ref_id):
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            pil = _round_sq_crop(Image.open(BytesIO(r.content)), 28)
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(28, 28))
            self._ctk_img_refs[ref_id] = ctk_img
            self.after(0, lambda: label.configure(image=ctk_img) if label.winfo_exists() else None)
        except Exception:
            pass

    def _show_write_review_dialog(self, app, edit_rev=None):
        if not self.api.token:
            tk.messagebox.showwarning("Login Required", "You must be logged in to post a review.")
            self._login()
            return

        win = ctk.CTkToplevel(self)
        title_text = "Edit Review" if edit_rev else "Write a Review"
        win.title(title_text)
        win.geometry("400x380")
        win.configure(fg_color=BG_MAIN)
        win.transient(self)

        ctk.CTkLabel(win, text=f"{title_text} for {app.get('name')}",
                     font=ctk.CTkFont(size=16, weight="bold"), text_color=TEXT_PRI).pack(pady=(20, 10))

        rating_frame = ctk.CTkFrame(win, fg_color="transparent")
        rating_frame.pack(pady=10)

        self._selected_rating = edit_rev["rating"] if edit_rev else 5
        stars_btns = []

        def set_rating(r):
            self._selected_rating = r
            for i, btn in enumerate(stars_btns):
                btn.configure(text="★" if i < r else "☆", text_color="#ffb400" if i < r else TEXT_MUTED)

        for i in range(1, 6):
            b = ctk.CTkButton(rating_frame, text="★", width=30, height=30, fg_color="transparent",
                              hover_color=BG_HOVER, font=ctk.CTkFont(size=24), text_color="#ffb400",
                              command=lambda idx=i: set_rating(idx))
            b.pack(side="left", padx=2)
            stars_btns.append(b)

        set_rating(self._selected_rating)

        comment_box = ctk.CTkTextbox(win, height=120, fg_color=BG_INPUT, border_color=BORDER, border_width=1)
        comment_box.pack(fill="x", padx=20, pady=10)
        if edit_rev:
            comment_box.insert("1.0", edit_rev["comment"])

        def submit():
            comment = comment_box.get("1.0", "end").strip()
            if not comment:
                tk.messagebox.showwarning("Validation", "Please write a comment.")
                return

            sub_btn.configure(state="disabled", text="Saving..." if edit_rev else "Submitting...")

            def run():
                success = self._post_review(app, self._selected_rating, comment,
                                            edit_id=edit_rev["id"] if edit_rev else None)
                if success:
                    self.after(0, lambda: (
                        tk.messagebox.showinfo("Success", "Review saved successfully!"),
                        win.destroy(),
                        self._load_reviews(app)
                    ))
                else:
                    self.after(0, lambda: (
                        tk.messagebox.showerror("Error", "Failed to save review. Check your connection."),
                        sub_btn.configure(state="normal", text="Submit Review")
                    ))

            threading.Thread(target=run, daemon=True).start()

        sub_btn = ctk.CTkButton(win, text="Save Review" if edit_rev else "Submit Review",
                                fg_color=self.config_db.get_accent_color(), command=submit)
        sub_btn.pack(pady=20)

    def _post_review(self, app, rating, comment, edit_id=None):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        repo = full_name.split(":")[0] if ":" in full_name else full_name
        display_name = self.config_db.get_display_name()

        body = f"User: {display_name}\nRatings: {rating}\nComment: {comment}"
        payload = {"title": name, "body": body}
        try:
            if edit_id:
                url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{edit_id}"
                r = requests.patch(url, headers=self.api.headers, json=payload, timeout=10)
                return r.status_code == 200
            else:
                url = f"{GITHUB_API_BASE}/repos/{repo}/issues"
                r = requests.post(url, headers=self.api.headers, json=payload, timeout=10)
                return r.status_code == 201
        except requests.RequestException:
            return False

    def _toggle_readme(self, app):
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
                    text_color=TEXT_MUTED, font=ctk.CTkFont(size=11)
                )
                lbl.pack(padx=12, pady=10)
                threading.Thread(target=self._fetch_readme, args=(app, lbl), daemon=True).start()

    def _fetch_readme(self, app, loading_lbl):
        full_name = app.get("full_name", "")
        readme_path = app.get("readme_path")
        text = None
        if readme_path and os.path.exists(readme_path):
            try:
                with open(readme_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                pass

        if not text:
            try:
                text = self.api.get_readme(full_name, app.get("default_branch", "main"))
            except Exception:
                text = None
            if not text:
                text = "No README found for this repository."
        self.after(0, lambda: self._show_readme(text, loading_lbl))

    def _show_readme(self, text, loading_lbl):
        if loading_lbl.winfo_exists():
            loading_lbl.destroy()

        tb = ctk.CTkTextbox(
            self._readme_frame, height=340,
            font=ctk.CTkFont(size=11, family="monospace"),
            fg_color=BG_DARK, wrap="word", border_width=0
        )
        tb.pack(fill="both", padx=10, pady=10)

        inner = tb._textbox
        inner.tag_configure("h1", font=("monospace", 17, "bold"), foreground="#e8eaed")
        inner.tag_configure("h2", font=("monospace", 14, "bold"), foreground="#c8cacd")
        inner.tag_configure("h3", font=("monospace", 12, "bold"), foreground="#a8aaad")
        inner.tag_configure("bold", font=("monospace", 11, "bold"), foreground="#d0d0e0")
        inner.tag_configure("italic", font=("monospace", 11, "italic"), foreground="#aabbcc")
        inner.tag_configure("code_inline", font=("Courier", 10), foreground="#7eb3ff", background="#0a0a14")
        inner.tag_configure("code_block", font=("Courier", 10), foreground="#7eb3ff", background="#080810")
        inner.tag_configure("bullet", foreground="#9aa0a6")
        inner.tag_configure("blockquote", font=("monospace", 11, "italic"), foreground="#7a8a9a")
        inner.tag_configure("normal", foreground="#8a9aaa")

        in_code_block = False
        for line in text.split("\n"):
            stripped = line.rstrip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                inner.insert("end", "\n")
                continue
            if in_code_block:
                inner.insert("end", stripped + "\n", "code_block")
                continue
            if stripped.startswith("### "):
                inner.insert("end", stripped[4:] + "\n", "h3")
            elif stripped.startswith("## "):
                inner.insert("end", stripped[3:] + "\n", "h2")
            elif stripped.startswith("# "):
                inner.insert("end", stripped[2:] + "\n", "h1")
            elif stripped.startswith("- ") or stripped.startswith("* "):
                inner.insert("end", "  • " + stripped[2:] + "\n", "bullet")
            elif stripped.startswith("> "):
                inner.insert("end", "  " + stripped[2:] + "\n", "blockquote")
            elif stripped in ("---", "***"):
                inner.insert("end", "─" * 40 + "\n", "normal")
            else:
                pattern = re.compile(r'(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`)')
                last = 0
                for m in pattern.finditer(stripped):
                    if m.start() > last:
                        inner.insert("end", stripped[last:m.start()], "normal")
                    val = m.group(0)
                    if val.startswith("**"):
                        inner.insert("end", val[2:-2], "bold")
                    elif val.startswith("*"):
                        inner.insert("end", val[1:-1], "italic")
                    elif val.startswith("`"):
                        inner.insert("end", val[1:-1], "code_inline")
                    last = m.end()
                if last < len(stripped):
                    inner.insert("end", stripped[last:], "normal")
                inner.insert("end", "\n")

        tb.configure(state="disabled")

    def _check_app_update(self, app):
        full_name = app.get("full_name", "")
        if ":" in full_name:
            repo, subdir = full_name.split(":", 1)
            url = f"{GITHUB_API_BASE}/repos/{repo}/commits?path={subdir}&per_page=1"
        else:
            url = f"{GITHUB_API_BASE}/repos/{full_name}/commits?per_page=1"

        self._check_upd_btn.configure(state="disabled", text="Checking...")

        def run():
            try:
                r = requests.get(url, headers=self.api.headers, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if data:
                        latest_pushed = data[0]["commit"]["committer"]["date"]
                        if self.db.needs_update(full_name, latest_pushed):
                            app["pushed_at"] = latest_pushed
                            self.after(0, lambda: (
                                tk.messagebox.showinfo("Update Available",
                                                       f"A new version of {app.get('name')} is available!"),
                                self.show_detail(app)
                            ))
                        else:
                            self.after(0, lambda: (
                                tk.messagebox.showinfo("No Updates", f"{app.get('name')} is already up to date."),
                                self._check_upd_btn.configure(state="normal", text="Check for updates")
                                if self._check_upd_btn.winfo_exists() else None
                            ))
                    else:
                        self.after(0, lambda: self._check_upd_btn.configure(state="normal", text="Check for updates")
                                   if self._check_upd_btn.winfo_exists() else None)
                elif r.status_code == 403:
                    self.after(0, lambda: (
                        self._show_notification("Rate limited by GitHub", color="#c62828"),
                        self._check_upd_btn.configure(state="normal", text="Check for updates")
                        if self._check_upd_btn.winfo_exists() else None
                    ))
                else:
                    self.after(0, lambda: self._check_upd_btn.configure(state="normal", text="Check for updates")
                               if self._check_upd_btn.winfo_exists() else None)
            except requests.RequestException:
                self.after(0, lambda: (
                    self._show_notification("Network error checking updates", color="#c62828"),
                    self._check_upd_btn.configure(state="normal", text="Check for updates")
                    if self._check_upd_btn.winfo_exists() else None
                ))

        threading.Thread(target=run, daemon=True).start()

    def _primary_action(self, app, action_text):
        if action_text == "Install":
            self._enqueue_install(app, is_update=False)
        elif action_text == "Update" or action_text.startswith("Update to"):
            self._enqueue_install(app, is_update=True)
        elif action_text == "Launch":
            self._launch(app)

    def _enqueue_install(self, app, is_update=False):
        full_name = app.get("full_name", "")

        with self._queue_lock:
            for t in self._download_history:
                if (t["full_name"] == full_name
                        and not t.get("finished")
                        and not t.get("error")
                        and not t.get("cancelled")):
                    return

        task = {
            "full_name": full_name,
            "app": app,
            "is_update": is_update,
            "status": "Pending",
            "progress": 0.0,
            "proc": None,
            "phase": None,
            "paused": False,
            "cancelled": False,
            "finished": False,
            "error": False,
            "error_msg": "",
        }

        with self._queue_lock:
            self._download_history.append(task)
            start_worker = not self._queue_worker_running
            if start_worker:
                self._queue_worker_running = True

        if (self._active_view == "detail"
                and getattr(self, "_current_viewing_fn", None) == full_name):
            self._refresh_action_area(app)

        if start_worker:
            threading.Thread(target=self._queue_worker, daemon=True).start()

    def _queue_worker(self):
        while True:
            task = None
            with self._queue_lock:
                for t in self._download_history:
                    if t["status"] == "Pending" and not t.get("cancelled"):
                        task = t
                        break

            if task is None:
                with self._queue_lock:
                    self._queue_worker_running = False
                break

            self._run_task(task)

    def _run_task(self, task):
        app = task["app"]
        full_name = task["full_name"]
        name = app.get("name", "")
        repo_url = app.get("html_url", "")
        subdir = app.get("subdir", "")
        pushed_at = app.get("pushed_at", "")
        install_path = os.path.join(INSTALL_BASE, name)

        def upd(status, progress, finished=False, error=False):
            task["status"] = status
            task["progress"] = progress
            task["finished"] = finished
            task["error"] = error
            if self._active_view == "downloads":
                self.after(0, self._render_downloads_list)
            if (self._active_view == "detail"
                    and getattr(self, "_current_viewing_fn", None) == full_name
                    and hasattr(self, "_install_progress")
                    and self._install_progress.winfo_exists()):
                self.after(0, lambda s=status, p=progress: (
                    self._install_progress.set(p),
                    self._install_status_lbl.configure(
                        text=s, text_color="#ff4b4b" if error else self.config_db.get_accent_color()
                    ),
                    self._primary_btn.configure(text=f"{s}...")
                    if hasattr(self, "_primary_btn") and self._primary_btn.winfo_exists() else None
                ))

        try:
            if task.get("cancelled"):
                return

            upd("Cloning", 0.1)
            task["phase"] = "clone"

            if os.path.exists(install_path):
                subprocess.run(["rm", "-rf", install_path], check=True)

            tmp_dir = os.path.expanduser(f"~/.appstore_tmp_{name}")
            if os.path.exists(tmp_dir):
                subprocess.run(["rm", "-rf", tmp_dir])
            os.makedirs(tmp_dir)

            if task.get("cancelled"):
                subprocess.run(["rm", "-rf", tmp_dir])
                return

            if subdir:
                clone_args = ["git", "clone", "--no-checkout", "--depth", "1", "--filter=blob:none", repo_url, tmp_dir]
            else:
                clone_args = ["git", "clone", "--depth", "1", repo_url, tmp_dir]

            clone_proc = subprocess.Popen(
                clone_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            task["proc"] = clone_proc

            def monitor_clone():
                while task.get("proc") == clone_proc and clone_proc.poll() is None:
                    if task.get("paused"):
                        time.sleep(0.5)
                        continue
                    if task["progress"] < 0.4:
                        self.after(0, lambda: upd("Downloading", task["progress"] + 0.02))
                    time.sleep(0.5)

            threading.Thread(target=monitor_clone, daemon=True).start()
            clone_proc.wait()
            task["proc"] = None

            if task.get("cancelled"):
                subprocess.run(["rm", "-rf", tmp_dir])
                return

            if clone_proc.returncode != 0:
                stderr_out = clone_proc.stderr.read().decode(errors="replace")[:80] if clone_proc.stderr else ""
                subprocess.run(["rm", "-rf", tmp_dir])
                raise Exception(f"git clone failed: {stderr_out}")

            if subdir:
                upd("Checking out", 0.45)
                subprocess.run(
                    ["git", "sparse-checkout", "set", subdir],
                    cwd=tmp_dir, check=True, capture_output=True
                )
                subprocess.run(
                    ["git", "checkout"], cwd=tmp_dir, check=True, capture_output=True
                )

            src = os.path.join(tmp_dir, subdir) if subdir else tmp_dir
            if not os.path.exists(src):
                raise Exception(f"Folder '{subdir}' not found in repo")

            upd("Installing", 0.6)
            task["phase"] = "install"

            subprocess.run(["mv", src, install_path], check=True)
            if os.path.exists(tmp_dir):
                subprocess.run(["rm", "-rf", tmp_dir])

            script = os.path.join(install_path, "install.sh")
            if os.path.exists(script):
                install_proc = subprocess.Popen(
                    ["bash", "install.sh"], cwd=install_path,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                task["proc"] = install_proc

                def monitor_install():
                    while task.get("proc") == install_proc and install_proc.poll() is None:
                        if task.get("paused"):
                            time.sleep(0.5)
                            continue
                        if task["progress"] < 0.95:
                            self.after(0, lambda: upd("Installing", task["progress"] + 0.01))
                        time.sleep(0.5)

                threading.Thread(target=monitor_install, daemon=True).start()
                install_proc.wait()
                task["proc"] = None

                if install_proc.returncode != 0:
                    err_out = install_proc.stderr.read().decode(errors="replace")[:80] if install_proc.stderr else ""
                    raise Exception(f"install.sh failed (code {install_proc.returncode}): {err_out}")

            if task.get("cancelled"):
                return

            self.db.add(full_name, name, install_path, pushed_at, app_data=app)
            upd("Completed", 1.0, finished=True)

            self.after(0, lambda: self._show_notification(f"✓  {name} installed successfully"))

            if (self._active_view == "detail"
                    and getattr(self, "_current_viewing_fn", None) == full_name):
                self.after(800, lambda: self._refresh_action_area(app))

        except subprocess.SubprocessError as e:
            err_msg = str(e)[:60]
            task["error_msg"] = err_msg
            upd("Failed", 0.0, error=True)
            self.after(0, lambda: self._show_notification(f"✗  {name}: subprocess error", color="#c62828"))
            if (self._active_view == "detail"
                    and getattr(self, "_current_viewing_fn", None) == full_name):
                self.after(2000, lambda: self._refresh_action_area(app))
        except OSError as e:
            err_msg = str(e)[:60]
            task["error_msg"] = err_msg
            upd("Failed", 0.0, error=True)
            self.after(0, lambda: self._show_notification(f"✗  {name}: {err_msg}", color="#c62828"))
            if (self._active_view == "detail"
                    and getattr(self, "_current_viewing_fn", None) == full_name):
                self.after(2000, lambda: self._refresh_action_area(app))
        except Exception as e:
            err_msg = str(e)[:60]
            task["error_msg"] = err_msg
            upd("Failed", 0.0, error=True)
            self.after(0, lambda: self._show_notification(f"✗  {name} failed", color="#c62828"))
            if (self._active_view == "detail"
                    and getattr(self, "_current_viewing_fn", None) == full_name):
                self.after(2000, lambda: self._refresh_action_area(app))

    def _launch(self, app):
        name = app.get("name", "")
        install_path = os.path.join(INSTALL_BASE, name)
        launch_script = os.path.join(install_path, "launch.sh")
        if os.path.exists(launch_script):
            try:
                subprocess.Popen(["bash", "launch.sh"], cwd=install_path)
            except OSError as e:
                tk.messagebox.showerror("Launch Error", f"Failed to launch: {e}")
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
            un_script = os.path.join(install_path, "uninstall.sh")
            if os.path.exists(un_script):
                subprocess.run(["bash", "uninstall.sh"], cwd=install_path,
                               capture_output=True, timeout=30)
            subprocess.run(["rm", "-rf", install_path], check=True)
            self.db.remove(full_name)
            self.show_detail(app)
        except subprocess.TimeoutExpired:
            tk.messagebox.showerror("Error", "Uninstall script timed out")
        except (subprocess.SubprocessError, OSError) as e:
            tk.messagebox.showerror("Uninstall Error", str(e))

    def _toggle_settings(self):
        if self._active_view == "settings":
            self.show_home()
        else:
            self.show_settings()

    def show_settings(self):
        self._active_view = "settings"
        self.title("Settings - AppStore")
        self.home_view.grid_forget()
        self.detail_container.grid_forget()
        self.downloads_view.grid_forget()
        self._tabs_row.grid_forget()
        if self._search_visible:
            self._search_row.grid_forget()

        self._settings_view.grid(row=0, column=0, sticky="nsew")

        for w in self._settings_view.winfo_children():
            w.destroy()
        self._populate_settings()

    def _section_card(self, parent, title):
        outer = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        outer.pack(fill="x", padx=20, pady=(0, 10))
        if title:
            ctk.CTkLabel(outer, text=title,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=TEXT_SEC).pack(anchor="w", padx=16, pady=(10, 2))
            ctk.CTkFrame(outer, height=1, fg_color=BORDER).pack(fill="x", padx=16, pady=(0, 4))
        return outer

    def _setting_row(self, parent, label_text, label_width=200):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=7)
        ctk.CTkLabel(row, text=label_text, font=ctk.CTkFont(size=13),
                     text_color=TEXT_PRI, width=label_width, anchor="w").pack(side="left")
        return row

    def _populate_settings(self):
        sv = self._settings_view
        accent = self.config_db.get_accent_color()

        ctk.CTkLabel(sv, text="Settings",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=TEXT_PRI).pack(anchor="w", padx=20, pady=(20, 16))

        profile_card = self._section_card(sv, "GitHub Account")
        self._settings_profile_frame = ctk.CTkFrame(profile_card, fg_color="transparent")
        self._settings_profile_frame.pack(fill="x", padx=16, pady=(8, 12))
        self._render_settings_profile()

        appearance_card = self._section_card(sv, "Appearance")

        row = self._setting_row(appearance_card, "Theme")
        theme_var = tk.StringVar(value=self.config_db.get_theme())
        theme_menu = ctk.CTkOptionMenu(
            row, values=["dark", "light", "system"],
            variable=theme_var,
            command=self._on_theme_change,
            width=130
        )
        theme_menu.pack(side="right")

        row2 = self._setting_row(appearance_card, "Accent Color")
        accent_frame = ctk.CTkFrame(row2, fg_color="transparent")
        accent_frame.pack(side="right")
        current_accent = self.config_db.get_accent_color()
        for cname, cval in ACCENT_COLORS.items():
            is_sel = cval == current_accent
            cb = ctk.CTkButton(
                accent_frame, text="✓" if is_sel else "", width=24, height=24,
                corner_radius=12, fg_color=cval, hover_color=cval,
                border_width=2 if is_sel else 0, border_color="#ffffff",
                font=ctk.CTkFont(size=10),
                command=lambda v=cval: self._on_accent_change(v)
            )
            cb.pack(side="left", padx=2)

        ctk.CTkFrame(appearance_card, height=8, fg_color="transparent").pack()

        display_card = self._section_card(sv, "Display")

        row3 = self._setting_row(display_card, "Grid Columns")
        cols_frame = ctk.CTkFrame(row3, fg_color="transparent")
        cols_frame.pack(side="right")
        current_cols = self.config_db.get_grid_cols()
        for c in [3, 4, 5]:
            ctk.CTkButton(
                cols_frame, text=str(c), width=36, height=30, corner_radius=8,
                fg_color=accent if current_cols == c else BG_HOVER,
                hover_color=accent,
                font=ctk.CTkFont(size=12, weight="bold" if current_cols == c else "normal"),
                command=lambda v=c: self._on_grid_cols_change(v)
            ).pack(side="left", padx=2)

        row4 = self._setting_row(display_card, "Icon Size")
        size_frame = ctk.CTkFrame(row4, fg_color="transparent")
        size_frame.pack(side="right")
        current_size = self.config_db.get_icon_size()
        for s, label in [(48, "S"), (64, "M"), (80, "L")]:
            ctk.CTkButton(
                size_frame, text=label, width=36, height=30, corner_radius=8,
                fg_color=accent if current_size == s else BG_HOVER,
                hover_color=accent,
                font=ctk.CTkFont(size=12, weight="bold" if current_size == s else "normal"),
                command=lambda v=s: self._on_icon_size_change(v)
            ).pack(side="left", padx=2)

        row5 = self._setting_row(display_card, "Show Category Badges")
        badge_var = tk.BooleanVar(value=self.config_db.get_show_category_badge())
        ctk.CTkSwitch(row5, text="", variable=badge_var,
                      command=lambda: self.config_db.set_show_category_badge(badge_var.get()),
                      progress_color=accent).pack(side="right")

        row6 = self._setting_row(display_card, "Compact Downloads View")
        compact_var = tk.BooleanVar(value=self.config_db.get_compact_downloads())
        ctk.CTkSwitch(row6, text="", variable=compact_var,
                      command=lambda: self.config_db.set_compact_downloads(compact_var.get()),
                      progress_color=accent).pack(side="right")

        ctk.CTkFrame(display_card, height=8, fg_color="transparent").pack()

        updates_card = self._section_card(sv, "Updates")

        row7 = self._setting_row(updates_card, "Auto-Update AppStore")
        au_var = tk.BooleanVar(value=self.config_db.get_auto_update())
        ctk.CTkSwitch(row7, text="", variable=au_var,
                      command=lambda: self.config_db.set_auto_update(au_var.get()),
                      progress_color=accent).pack(side="right")

        ctk.CTkLabel(updates_card, text="Auto-update applies on next restart.",
                     font=ctk.CTkFont(size=10), text_color=TEXT_MUTED).pack(anchor="w", padx=16, pady=(0, 4))

        row8 = self._setting_row(updates_card, f"App Cache  ({self.cache_db.size_str()})")
        ctk.CTkButton(row8, text="Clear Cache", width=110, height=28, corner_radius=8,
                      fg_color=BG_HOVER, hover_color="#4a1010", text_color="#ff6b6b",
                      font=ctk.CTkFont(size=12),
                      command=self._clear_cache_action).pack(side="right")

        row9 = self._setting_row(updates_card, "Icon Cache")
        ctk.CTkButton(row9, text="Clear Icons", width=110, height=28, corner_radius=8,
                      fg_color=BG_HOVER, hover_color=BG_CARD2, text_color=TEXT_SEC,
                      font=ctk.CTkFont(size=12),
                      command=self._clear_icon_cache).pack(side="right")

        ctk.CTkFrame(updates_card, height=8, fg_color="transparent").pack()

        github_card = self._section_card(sv, "GitHub OAuth")

        cid_row = ctk.CTkFrame(github_card, fg_color="transparent")
        cid_row.pack(fill="x", padx=16, pady=8)
        ctk.CTkLabel(cid_row, text="Client ID:", font=ctk.CTkFont(size=13),
                     text_color=TEXT_PRI, width=90, anchor="w").pack(side="left")
        self._cid_entry = ctk.CTkEntry(cid_row, placeholder_text="Paste Client ID...",
                                       height=30, font=ctk.CTkFont(size=12),
                                       fg_color=BG_INPUT, border_color=BORDER)
        self._cid_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        existing_cid = self.config_db.get_client_id()
        if existing_cid:
            self._cid_entry.insert(0, existing_cid)
        ctk.CTkButton(cid_row, text="Save", width=60, height=30, corner_radius=8,
                      fg_color=accent, command=self._save_client_id).pack(side="right")

        ctk.CTkLabel(github_card,
                     text="GitHub → Settings → Developer → OAuth Apps → Create App → enable Device Flow",
                     font=ctk.CTkFont(size=10), text_color=TEXT_MUTED, wraplength=560).pack(
            anchor="w", padx=16, pady=(0, 10))

        about_card = self._section_card(sv, "About")
        ctk.CTkLabel(about_card, text=f"AppStore  v{APPSTORE_VERSION}",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_PRI).pack(
            anchor="w", padx=16, pady=(8, 2))
        ctk.CTkLabel(about_card, text="A Termux Desktop application store.",
                     font=ctk.CTkFont(size=11), text_color=TEXT_SEC).pack(anchor="w", padx=16)

        gh_row = ctk.CTkFrame(about_card, fg_color="transparent")
        gh_row.pack(fill="x", padx=16, pady=(8, 12))
        ctk.CTkButton(gh_row, text="GitHub ↗", width=100, height=30, corner_radius=8,
                      fg_color="transparent", border_width=1, border_color=BORDER,
                      text_color="#7eb3ff", font=ctk.CTkFont(size=12),
                      command=lambda: webbrowser.open(APPSTORE_REPO_URL)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(gh_row, text="Check Update", width=120, height=30, corner_radius=8,
                      fg_color=accent,
                      font=ctk.CTkFont(size=12),
                      command=self._update_appstore).pack(side="left")

        ctk.CTkFrame(sv, height=30, fg_color="transparent").pack()

    def _render_settings_profile(self):
        for w in self._settings_profile_frame.winfo_children():
            w.destroy()

        if self.config_db.get_token():
            username = self.config_db.get_display_name() or "User"
            user_icon = _placeholder_icon(username, 42)
            ctk_icon = ctk.CTkImage(light_image=user_icon, dark_image=user_icon, size=(42, 42))
            self._settings_profile_icon_ref = ctk_icon

            info_row = ctk.CTkFrame(self._settings_profile_frame, fg_color="transparent")
            info_row.pack(fill="x")

            icon_bg = ctk.CTkFrame(info_row, width=42, height=42, fg_color=BG_HOVER, corner_radius=21)
            icon_bg.pack(side="left", padx=(0, 12))
            icon_bg.pack_propagate(False)
            ctk.CTkLabel(icon_bg, image=ctk_icon, text="").place(relx=0.5, rely=0.5, anchor="center")

            text_col = ctk.CTkFrame(info_row, fg_color="transparent")
            text_col.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(text_col, text=username,
                         font=ctk.CTkFont(size=15, weight="bold"),
                         text_color=TEXT_PRI, anchor="w").pack(fill="x")
            ctk.CTkLabel(text_col, text="Logged in via GitHub",
                         font=ctk.CTkFont(size=11), text_color=TEXT_SEC, anchor="w").pack(fill="x")

            ctk.CTkButton(info_row, text="Logout", width=80, height=30, corner_radius=8,
                          fg_color="#2a0a0a", hover_color="#4a1010", text_color="#ff6b6b",
                          font=ctk.CTkFont(size=12),
                          command=self._logout).pack(side="right")
        else:
            no_login = ctk.CTkFrame(self._settings_profile_frame, fg_color="transparent")
            no_login.pack(fill="x")
            ctk.CTkLabel(no_login, text="Not logged in",
                         font=ctk.CTkFont(size=13), text_color=TEXT_MUTED).pack(side="left")
            ctk.CTkButton(no_login, text="Sign in with GitHub", width=160, height=32,
                          corner_radius=8,
                          fg_color=self.config_db.get_accent_color(),
                          font=ctk.CTkFont(size=12),
                          command=self._login).pack(side="right")

    def _on_theme_change(self, theme):
        self.config_db.set_theme(theme)
        ctk.set_appearance_mode(theme)
        self.configure(fg_color=BG_MAIN)

    def _on_accent_change(self, color):
        self.config_db.set_accent_color(color)
        self._show_notification("Accent color updated. Refresh home to apply.", color=color)
        self.show_settings()

    def _on_grid_cols_change(self, cols):
        self.config_db.set_grid_cols(cols)
        self._show_notification(f"Grid columns set to {cols}")
        self.show_settings()

    def _on_icon_size_change(self, size):
        self.config_db.set_icon_size(size)
        self._icon_cache.clear()
        self._show_notification("Icon size updated. Refresh home to apply.")
        self.show_settings()

    def _clear_cache_action(self):
        self.cache_db.clear()
        self._apps_fetched = False
        self.loaded_apps = []
        self._show_notification("Cache cleared", color="#1e7e34")
        self.show_settings()

    def _clear_icon_cache(self):
        self._icon_cache.clear()
        self._ctk_img_refs.clear()
        self._show_notification("Icon cache cleared", color="#1e7e34")

    def _save_client_id(self):
        cid = self._cid_entry.get().strip()
        if cid:
            self.config_db.set_client_id(cid)
            self._show_notification("Client ID saved", color="#1e7e34")
        else:
            self._show_notification("Client ID cannot be empty", color="#c62828")

    def _logout(self):
        if not tk.messagebox.askyesno("Logout", "Log out of GitHub?"):
            return
        self.api.set_token(None)
        self.config_db.clear_token()
        self._show_notification("Logged out", color="#1e7e34")
        if self._active_view == "settings":
            self._render_settings_profile()

    def _login(self):
        if self.api.token:
            if self._active_view == "settings":
                pass
            else:
                self.show_settings()
        else:
            client_id = self.config_db.get_client_id()
            if not client_id:
                self._show_client_id_dialog()
            else:
                self._show_login_dialog(client_id)

    def _show_client_id_dialog(self):
        win = ctk.CTkToplevel(self)
        win.title("GitHub Setup")
        win.geometry("420x300")
        win.configure(fg_color=BG_MAIN)
        win.transient(self)

        ctk.CTkLabel(win, text="Enter your GitHub OAuth Client ID",
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=TEXT_PRI).pack(pady=(25, 10))

        desc = ("To log in and post reviews, you need a GitHub OAuth Client ID.\n\n"
                "1. Go to GitHub Settings → Developer Settings → OAuth Apps.\n"
                "2. Create a new app and enable 'Device Flow'.\n"
                "3. Copy the Client ID and paste it below.")
        ctk.CTkLabel(win, text=desc, font=ctk.CTkFont(size=11), text_color=TEXT_SEC,
                     justify="center", wraplength=360).pack(pady=(0, 20))

        entry = ctk.CTkEntry(win, width=280, placeholder_text="Paste Client ID here...",
                             fg_color=BG_INPUT, border_color=BORDER)
        entry.pack(pady=5)

        def save():
            cid = entry.get().strip()
            if cid:
                self.config_db.set_client_id(cid)
                win.destroy()
                self._show_login_dialog(cid)
            else:
                tk.messagebox.showerror("Validation Error", "Client ID cannot be empty.")

        ctk.CTkButton(win, text="Save & Continue", width=160, height=36,
                      corner_radius=18, fg_color=self.config_db.get_accent_color(),
                      command=save).pack(pady=20)

    def _show_login_dialog(self, client_id):
        data = self.api.start_device_flow(client_id)
        if not data:
            tk.messagebox.showerror(
                "Connection Error",
                "Could not connect to GitHub.\nCheck your Client ID and ensure 'Device Flow' is enabled."
            )
            self.config_db.set_client_id(None)
            return

        user_code = data.get("user_code", "")
        verification_uri = data.get("verification_uri", "")
        device_code = data.get("device_code", "")
        interval = data.get("interval", 5)

        win = ctk.CTkToplevel(self)
        win.title("GitHub Login")
        win.geometry("460x380")
        win.configure(fg_color=BG_MAIN)
        win.transient(self)

        ctk.CTkLabel(win, text="Link your GitHub Account",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=TEXT_PRI).pack(pady=(25, 15))

        inst = ("1. Click the button below to open GitHub \nor go to https://github.com/login/device.\n"
                "2. Sign in if required.\n"
                "3. Enter the 8-digit code shown below.")
        ctk.CTkLabel(win, text=inst, font=ctk.CTkFont(size=13), justify="left",
                     text_color=TEXT_SEC).pack(pady=10)

        code_frame = ctk.CTkFrame(win, fg_color=BG_CARD, corner_radius=10)
        code_frame.pack(pady=20, padx=40, fill="x")

        ctk.CTkLabel(code_frame, text=user_code,
                     font=ctk.CTkFont(size=36, weight="bold", family="monospace"),
                     text_color="#7eb3ff").pack(pady=15)

        status_lbl = ctk.CTkLabel(win, text="Waiting for authorization...",
                                  font=ctk.CTkFont(size=12, slant="italic"), text_color=TEXT_MUTED)
        status_lbl.pack()

        ctk.CTkButton(win, text="Open Browser", width=200, height=40, corner_radius=20,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      fg_color=self.config_db.get_accent_color(),
                      command=lambda: webbrowser.open(verification_uri)).pack(pady=20)

        def wait():
            token = self.api.poll_for_token(client_id, device_code, interval)
            if token:
                self.api.set_token(token)
                self.config_db.set_token(token)
                try:
                    user = self.api.get_current_user()
                    if user:
                        self.config_db.set_username(user.get("login"))
                        self.config_db.set_display_name(user.get("name") or user.get("login"))
                except Exception:
                    pass

                self.after(0, lambda: (
                    win.destroy(),
                    self._show_notification(
                        f"Welcome, {self.config_db.get_display_name()}!", color="#1e7e34"
                    ),
                    self._render_settings_profile() if self._active_view == "settings" else None
                ))
            else:
                if win.winfo_exists():
                    self.after(0, lambda: status_lbl.configure(
                        text="Authorization failed or timed out.", text_color="#ff6b6b"
                    ))

        threading.Thread(target=wait, daemon=True).start()


if __name__ == "__main__":
    app = AppStoreApp()
    app.mainloop()
