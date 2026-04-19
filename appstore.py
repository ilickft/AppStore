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
VERIFIED_REPOS_URL = "https://raw.githubusercontent.com/ilickft/AppStore/refs/heads/main/repos.txt"
APPSTORE_REPO_URL = "https://github.com/ilickft/AppStore/"
APPSTORE_VERSION = "1.1.0"
INSTALL_BASE = os.path.expanduser("~/.appstore/apps")
INSTALL_DB_PATH = os.path.expanduser("~/.appstore/installed.json")
CONFIG_PATH = os.path.expanduser("~/.config/appstore/config.json")

os.makedirs(INSTALL_BASE, exist_ok=True)
os.makedirs(os.path.dirname(INSTALL_DB_PATH), exist_ok=True)
os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)


def _round_sq_crop(img, size):
    radius = size // 5
    img = img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


def _placeholder_icon(name, size):
    palette = ["#1a3a6a", "#2d1a6a", "#1a4a3a", "#4a2d1a", "#3a1a4a", "#1a4a4a", "#4a1a2e"]
    color = palette[sum(ord(c) for c in (name or "?")) % len(palette)]
    radius = size // 5
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=color)
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
            
        for name in os.listdir(INSTALL_BASE):
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
                    "category": "Installed"
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
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
            os.chmod(self._path, 0o600)
        except Exception:
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
        except Exception:
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
        except Exception:
            pass
        return self.verified_repos

    def is_verified(self, full_name):
        if full_name.startswith("App-Store-tmx/"):
            return True
        return full_name in self.verified_repos

    def search_apps(self, query=""):
        apps = []
        repos = ["App-Store-tmx/Games", "App-Store-tmx/Apps"]
        
        for repo_full_name in repos:
            url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents"
            try:
                r = requests.get(url, headers=self.headers, timeout=12)
                if r.status_code == 200:
                    items = r.json()
                    for item in items:
                        if item.get("type") == "dir":
                            if item["name"].startswith("."):
                                continue
                            category = "Games" if "Games" in repo_full_name else "Apps"
                            app = {
                                "full_name": f"{repo_full_name}:{item['name']}",
                                "name": item["name"],
                                "description": f"An app from the {category} store.",
                                "stargazers_count": 0,
                                "forks_count": 0,
                                "language": "Bash",
                                "pushed_at": "",
                                "html_url": f"https://github.com/{repo_full_name}",
                                "owner": {"login": repo_full_name.split('/')[0], "avatar_url": ""},
                                "subdir": item["name"],
                                "repo_name": repo_full_name.split('/')[1],
                                "category": category,
                                "icon_url": f"https://raw.githubusercontent.com/{repo_full_name}/main/{item['name']}/icon.png"
                            }
                            apps.append(app)
                elif r.status_code == 403:
                    print(f"GitHub API rate limit exceeded for {repo_full_name}.")
                else:
                    print(f"GitHub API returned status: {r.status_code} for {repo_full_name}")
            except Exception as e:
                print(f"Search error for {repo_full_name}: {e}")
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
            if ":" in full_name:
                repo, subdir = full_name.split(":", 1)
                url = f"{GITHUB_API_BASE}/repos/{repo}/readme/{subdir}"
            else:
                url = f"{GITHUB_API_BASE}/repos/{full_name}/readme"
            r = requests.get(url, headers=self.headers, timeout=5)
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

    def check_appstore_update(self):
        try:
            raw_url = "https://raw.githubusercontent.com/ilickft/AppStore/main/appstore.py"
            r = requests.get(raw_url, timeout=5)
            if r.status_code == 200:
                match = re.search(r'APPSTORE_VERSION\s*=\s*"([^"]+)"', r.text)
                if match:
                    return match.group(1)
        except Exception:
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
            else:
                print(f"GitHub Auth Error: {r.status_code} {r.text}")
        except Exception as e:
            print(f"Auth request exception: {e}")
        return None

    def poll_for_token(self, client_id, device_code, interval):
        data = {
            "client_id": client_id,
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
        self.geometry("600x400")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color="#0f0f1a")

        # Set window icon
        icon_path = os.path.join(os.path.dirname(__file__), "AppStore.png")
        if os.path.exists(icon_path):
            try:
                img = tk.PhotoImage(file=icon_path)
                self.wm_iconphoto(True, img)
            except Exception:
                pass

        self.api = GitHubAPI()
        self.config_db = ConfigDB()
        self.db = InstalledDB()
        
        # Load saved token
        saved_token = self.config_db.get_token()
        if saved_token:
            self.api.set_token(saved_token)
            
        self.loaded_apps = []
        self._apps_fetched = False
        self._active_installs = {}
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

        self.current_category = "Apps"
        self._build_header()
        self._build_tabs()
        self._build_search_row()
        self._build_body()
        self.show_home()
        threading.Thread(target=self._check_updates_silent, daemon=True).start()

        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)
        self.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        # Determine active scrollable frame
        # On Windows, we check if home_view is visible
        if self.home_view.winfo_viewable():
            sf = self.home_view
        else:
            sf = self.detail_view
            
        try:
            # CTkScrollableFrame internal canvas
            canvas = getattr(sf, "_parent_canvas", getattr(sf, "_canvas", None))
            if not canvas:
                return

            if event.num == 4: # Linux/X11 Up
                canvas.yview_scroll(-3, "units")
            elif event.num == 5: # Linux/X11 Down
                canvas.yview_scroll(3, "units")
            elif hasattr(event, "delta") and event.delta != 0: # Windows/macOS
                # Windows delta is 120 per notch. Scroll 3-4 lines per notch.
                amount = int(-1 * (event.delta / 30))
                canvas.yview_scroll(amount, "units")
        except:
            pass

    def _check_updates_silent(self):
        remote_v = self.api.check_appstore_update()
        if remote_v and remote_v != APPSTORE_VERSION:
            self.after(0, lambda: self._update_btn.configure(text="⤒ ●", text_color="#ff9800"))
        elif remote_v == APPSTORE_VERSION:
            self.after(0, lambda: self._update_btn.configure(text="⤒ ●", text_color="#34a853"))

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
        icons_frame.grid(row=0, column=2, padx=10, sticky="e")

        # Professional header button style
        ibtn = dict(width=34, height=34, corner_radius=17,
                    fg_color="transparent", hover_color="#1e1e40",
                    font=ctk.CTkFont(size=17))

        ctk.CTkButton(icons_frame, text="⌕", command=self._toggle_search, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons_frame, text="⌂", command=self.show_home, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons_frame, text="⬇", command=self.show_downloads, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons_frame, text="↻", command=self._force_refresh_apps, **ibtn).pack(side="left", padx=1)
        
        self._update_btn = ctk.CTkButton(icons_frame, text="⤒", command=self._update_appstore, **ibtn)
        self._update_btn.pack(side="left", padx=1)
        
        self._profile_btn = ctk.CTkButton(icons_frame, text="◉", command=self._login, **ibtn)
        self._profile_btn.pack(side="left", padx=(1, 4))

    def _build_tabs(self):
        self._tabs_row = ctk.CTkFrame(self, height=40, corner_radius=0, fg_color="#0f0f1a")
        self._tabs_row.grid(row=1, column=0, sticky="ew")
        
        self._apps_tab = ctk.CTkButton(
            self._tabs_row, text="Apps", width=80, height=30, corner_radius=15,
            fg_color="#1a73e8", text_color="#ffffff", font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self._set_category("Apps")
        )
        self._apps_tab.pack(side="left", padx=(20, 10), pady=5)
        
        self._games_tab = ctk.CTkButton(
            self._tabs_row, text="Games", width=80, height=30, corner_radius=15,
            fg_color="transparent", text_color="#9aa0a6", font=ctk.CTkFont(size=13),
            command=lambda: self._set_category("Games")
        )
        self._games_tab.pack(side="left", padx=10, pady=5)

        self._installed_tab = ctk.CTkButton(
            self._tabs_row, text="Installed", width=80, height=30, corner_radius=15,
            fg_color="transparent", text_color="#9aa0a6", font=ctk.CTkFont(size=13),
            command=lambda: self._set_category("Installed")
        )
        self._installed_tab.pack(side="left", padx=10, pady=5)

    def _set_category(self, cat):
        self.current_category = cat
        for btn, name in [(self._apps_tab, "Apps"), (self._games_tab, "Games"), (self._installed_tab, "Installed")]:
            if cat == name:
                btn.configure(fg_color="#1a73e8", text_color="#ffffff", font=ctk.CTkFont(size=13, weight="bold"))
            else:
                btn.configure(fg_color="transparent", text_color="#9aa0a6", font=ctk.CTkFont(size=13))
        self._apply_filter()

    def _update_appstore(self):
        conf = ctk.CTkToplevel(self)
        conf.title("Confirm Update")
        conf.geometry("380x200")
        conf.configure(fg_color="#0f0f1a")
        
        ctk.CTkLabel(conf, text="Update AppStore to the latest version?", 
                     font=ctk.CTkFont(size=14)).pack(pady=40)
        
        btns = ctk.CTkFrame(conf, fg_color="transparent")
        btns.pack(fill="x", side="bottom", pady=20)
        
        def start_upd():
            conf.destroy()
            self._do_update_appstore()
            
        ctk.CTkButton(btns, text="Yes, Update", width=110, height=36, command=start_upd).pack(side="right", padx=15)
        ctk.CTkButton(btns, text="Cancel", width=90, height=36, fg_color="#333", command=conf.destroy).pack(side="right", padx=5)

    def _do_update_appstore(self):
        win = ctk.CTkToplevel(self)
        win.title("Updating AppStore")
        win.geometry("540x450")
        win.configure(fg_color="#0f0f1a")
        
        lbl = ctk.CTkLabel(win, text="Updating to latest version...", font=ctk.CTkFont(size=14, weight="bold"))
        lbl.pack(pady=(15, 5))
        
        log = ctk.CTkTextbox(win, fg_color="#0a0a14", border_width=1, border_color="#1e1e40", 
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
                        w("\n" + "="*40 + "\nUPDATE SUCCESSFUL!\n" + "="*40 + "\n")
                        w("Please restart the AppStore to apply changes.\n")
                    else:
                        w(f"\nUpdate script failed with code {proc.returncode}\n")
                else:
                    w("\nError: install.sh not found in the repository.\n")
            except Exception as e:
                w(f"\nCritical Error: {e}\n")
            finally:
                if win.winfo_exists():
                    self.after(0, lambda: close_btn.configure(state="normal"))
        
        threading.Thread(target=run, daemon=True).start()

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
        self.body.grid(row=3, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)

        self.home_view = ctk.CTkScrollableFrame(self.body, fg_color="#0f0f1a", corner_radius=0)
        self.home_view.grid(row=0, column=0, sticky="nsew")

        self.detail_container = ctk.CTkFrame(self.body, fg_color="#0f0f1a", corner_radius=0)
        
        self.detail_top_bar = ctk.CTkFrame(self.detail_container, height=48, fg_color="#12122a", corner_radius=0)
        self.detail_top_bar.pack(fill="x")
        
        self._back_btn = ctk.CTkButton(
            self.detail_top_bar, text="←  Back",
            width=80, height=30, corner_radius=15,
            fg_color="transparent", border_width=1, border_color="#2a2a50",
            font=ctk.CTkFont(size=12), text_color="#9aa0a6",
            command=self.show_home
        )
        self._back_btn.pack(side="left", padx=16, pady=9)

        self.detail_view = ctk.CTkScrollableFrame(self.detail_container, fg_color="#0f0f1a", corner_radius=0)
        self.detail_view.pack(fill="both", expand=True)

        self.downloads_view = ctk.CTkScrollableFrame(self.body, fg_color="#0f0f1a", corner_radius=0)

    def show_downloads(self):
        self.title("Downloads - AppStore")
        self.home_view.grid_forget()
        self.detail_container.grid_forget()
        self._tabs_row.grid_forget()
        if self._search_visible:
            self._search_row.grid_forget()
        
        self.downloads_view.grid(row=0, column=0, sticky="nsew")
        self._render_downloads_list()

    def _render_downloads_list(self):
        for w in self.downloads_view.winfo_children():
            w.destroy()
            
        ctk.CTkLabel(self.downloads_view, text="Active Downloads", 
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 10), padx=20, anchor="w")
        
        if not self._active_installs:
            ctk.CTkLabel(self.downloads_view, text="No active downloads.", 
                         text_color="#555", font=ctk.CTkFont(size=13)).pack(pady=40)
            return

        for full_name, data in list(self._active_installs.items()):
            app = data["app"]
            card = ctk.CTkFrame(self.downloads_view, fg_color="#12122a", corner_radius=10)
            card.pack(fill="x", padx=20, pady=5)
            
            lbl_row = ctk.CTkFrame(card, fg_color="transparent")
            lbl_row.pack(fill="x", padx=15, pady=(10, 5))
            
            ctk.CTkLabel(lbl_row, text=app.get("name"), font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
            status_lbl = ctk.CTkLabel(lbl_row, text=data["status"], font=ctk.CTkFont(size=12), text_color="#1a73e8")
            status_lbl.pack(side="right")
            
            prog = ctk.CTkProgressBar(card, height=8, corner_radius=4, progress_color="#1a73e8")
            prog.pack(fill="x", padx=15, pady=(0, 15))
            prog.set(data["progress"])
            
            # Store refs to update them later
            data["ui_prog"] = prog
            data["ui_status"] = status_lbl

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
        else:
            # Filter by category first
            filtered = [a for a in self.loaded_apps if a.get("category") == self.current_category]
        
        # Then filter by query
        if q:
            filtered = [
                a for a in filtered
                if q in a.get("name", "").lower()
                or q in (a.get("description") or "").lower()
            ]
        self._render_home(filtered)

    def show_home(self):
        self.title("Home - AppStore")
        self.detail_container.grid_forget()
        self.downloads_view.grid_forget()
        self.home_view.grid(row=0, column=0, sticky="nsew")
        self._tabs_row.grid(row=1, column=0, sticky="ew")
        
        if self._apps_fetched:
            self._apply_filter()
            return

        for w in self.home_view.winfo_children():
            w.destroy()
        self._tile_icon_labels.clear()
        self._show_loading_state()
        threading.Thread(target=self._fetch_apps, daemon=True).start()

    def _force_refresh_apps(self):
        self._apps_fetched = False
        self.show_home()

    def _show_loading_state(self):
        f = ctk.CTkFrame(self.home_view, fg_color="transparent")
        f.pack(expand=True, pady=80)
        ctk.CTkLabel(f, text="Fetching apps from GitHub...",
                     text_color="#555", font=ctk.CTkFont(size=14)).pack()

    def _fetch_apps(self):
        self.api.fetch_verified_repos()
        apps = self.api.search_apps()
        self.loaded_apps = apps
        self._apps_fetched = True
        self.after(0, lambda: self._apply_filter())

    def _render_home(self, apps):
        for w in self.home_view.winfo_children():
            w.destroy()
        self._tile_icon_labels.clear()

        if not apps:
            f = ctk.CTkFrame(self.home_view, fg_color="transparent")
            f.pack(expand=True, pady=80)
            
            if self.current_category == "Installed":
                ctk.CTkLabel(f, text="Your library is empty",
                             text_color="#555", font=ctk.CTkFont(size=17, weight="bold")).pack()
                ctk.CTkLabel(f, text="You don't have any installed apps yet.",
                             text_color="#444", font=ctk.CTkFont(size=12), justify="center").pack(pady=10)
                ctk.CTkButton(f, text="Browse Apps", width=120, height=32,
                              command=lambda: self._set_category("Apps")).pack(pady=12)
            else:
                ctk.CTkLabel(f, text="No apps/games available",
                             text_color="#555", font=ctk.CTkFont(size=17, weight="bold")).pack()
                ctk.CTkLabel(f, text="There is no app/games available right now.\nYou can add your own public apps by contacting the dev.",
                             text_color="#444", font=ctk.CTkFont(size=12), justify="center").pack(pady=10)
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
        icon_url = app.get("icon_url")
        icon_path = app.get("icon_path")

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

        if icon_path and os.path.exists(icon_path):
            threading.Thread(
                target=self._load_tile_icon_local, args=(full_name, icon_path), daemon=True
            ).start()
        elif icon_url:
            threading.Thread(
                target=self._load_tile_icon, args=(full_name, icon_url), daemon=True
            ).start()

    def _load_tile_icon(self, full_name, url):
        try:
            if url in self._icon_cache:
                pil = self._icon_cache[url]
            else:
                r = requests.get(url, timeout=5)
                pil = _round_sq_crop(Image.open(BytesIO(r.content)), 64)
                self._icon_cache[url] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(64, 64))
            self._ctk_img_refs[full_name] = ctk_img
            self.after(0, lambda fn=full_name, ci=ctk_img: self._apply_tile_icon(fn, ci))
        except Exception:
            pass

    def _load_tile_icon_local(self, full_name, path):
        try:
            if path in self._icon_cache:
                pil = self._icon_cache[path]
            else:
                pil = _round_sq_crop(Image.open(path), 64)
                self._icon_cache[path] = pil
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
        full_name = app.get("full_name", "")
        self._current_viewing_fn = full_name
        
        self.title(f"{name} - AppStore")
        self.home_view.grid_forget()
        self.downloads_view.grid_forget()
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
        pushed_at = app.get("pushed_at", "")
        owner = app.get("owner", {})
        avatar_url = owner.get("avatar_url")
        html_url = app.get("html_url", "")
        is_verified = self.api.is_verified(full_name)
        is_inst = self.db.is_installed(full_name)
        needs_upd = self.db.needs_update(full_name, pushed_at) if is_inst else False

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

        icon_url = app.get("icon_url")
        icon_path = app.get("icon_path")
        if icon_path and os.path.exists(icon_path):
            threading.Thread(
                target=self._load_detail_icon_local, args=(icon_path,), daemon=True
            ).start()
        elif icon_url:
            threading.Thread(
                target=self._load_detail_icon, args=(icon_url,), daemon=True
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
            ).pack(side="left", padx=(0, 10))

            if not needs_upd:
                self._check_upd_btn = ctk.CTkButton(
                    btn_row, text="Check for updates",
                    width=140, height=42, corner_radius=21,
                    fg_color="transparent", border_width=1, border_color="#2a2a50",
                    text_color="#7eb3ff", font=ctk.CTkFont(size=13),
                    command=lambda: self._check_app_update(app)
                )
                self._check_upd_btn.pack(side="left")

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

        self._install_progress_frame = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        
        self._install_status_lbl = ctk.CTkLabel(
            self._install_progress_frame, text="Installing...",
            font=ctk.CTkFont(size=12, slant="italic"), text_color="#1a73e8"
        )
        self._install_status_lbl.pack(anchor="w", padx=2)
        
        self._install_progress = ctk.CTkProgressBar(
            self._install_progress_frame, height=8, corner_radius=4,
            progress_color="#1a73e8", fg_color="#1e1e40"
        )
        self._install_progress.pack(fill="x", pady=(2, 10))
        self._install_progress.set(0)

        # Re-attach progress UI if active
        if full_name in self._active_installs:
            data = self._active_installs[full_name]
            self._install_progress_frame.pack(fill="x", padx=16, after=self._primary_btn)
            self._primary_btn.configure(state="disabled", text="Installing...")
            self._install_status_lbl.configure(text=data["status"])
            self._install_progress.set(data["progress"])

        self._ss_sep_top = ctk.CTkFrame(self.detail_view, height=1, fg_color="#1e1e40")
        self._ss_sep_top.pack(fill="x", padx=16, pady=14)

        self._screenshots_outer = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        self._screenshots_outer.pack(fill="x", padx=16)
        self._ss_loading_lbl = ctk.CTkLabel(
            self._screenshots_outer, text="Loading screenshots...",
            text_color="#3a3a5a", font=ctk.CTkFont(size=11)
        )
        self._ss_loading_lbl.pack(anchor="w", pady=2)
        
        self._ss_sep_bottom = ctk.CTkFrame(self.detail_view, height=1, fg_color="#1e1e40")
        self._ss_sep_bottom.pack(fill="x", padx=16, pady=(16, 0))

        threading.Thread(
            target=self._load_screenshots, args=(app,), daemon=True
        ).start()

        self._about_btn = ctk.CTkButton(
            self.detail_view,
            text="  About this App    ▼",
            height=50, corner_radius=0,
            fg_color="transparent", hover_color="#141428",
            text_color="#e8eaed", font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
            command=lambda: self._toggle_readme(app)
        )
        self._about_btn.pack(fill="x", padx=4)

        self._readme_frame = ctk.CTkFrame(self.detail_view, fg_color="#0b0b1a", corner_radius=10)

        # Reviews Section
        self._reviews_outer = ctk.CTkFrame(self.detail_view, fg_color="transparent")
        self._reviews_outer.pack(fill="x", padx=16, pady=(16, 20))
        
        ctk.CTkLabel(self._reviews_outer, text="Reviews", 
                     font=ctk.CTkFont(size=16, weight="bold"), text_color="#e8eaed").pack(anchor="w", pady=(0, 10))
        
        self._reviews_list = ctk.CTkFrame(self._reviews_outer, fg_color="transparent")
        self._reviews_list.pack(fill="x")
        
        self._reviews_loading = ctk.CTkLabel(self._reviews_list, text="Loading reviews...", 
                                              text_color="#3a3a5a", font=ctk.CTkFont(size=11))
        self._reviews_loading.pack(pady=10)
        
        self._write_review_btn = ctk.CTkButton(
            self._reviews_outer, text="✎ Write a Review", 
            width=140, height=34, corner_radius=17,
            fg_color="#1e1e40", hover_color="#2a2a50", text_color="#7eb3ff",
            font=ctk.CTkFont(size=12),
            command=lambda: self._show_write_review_dialog(app)
        )
        self._write_review_btn.pack(anchor="w", pady=(10, 0))

        threading.Thread(target=self._load_reviews, args=(app,), daemon=True).start()

        ctk.CTkFrame(self.detail_view, height=30, fg_color="transparent").pack()

    def _load_detail_icon(self, url):
        try:
            if url in self._icon_cache:
                pil = self._icon_cache[url]
            else:
                r = requests.get(url, timeout=5)
                pil = _round_sq_crop(Image.open(BytesIO(r.content)), 82)
                self._icon_cache[url] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(82, 82))
            self._detail_icon_ref = ctk_img
            self.after(0, lambda: (
                self._detail_icon_lbl.configure(image=ctk_img)
                if self._detail_icon_lbl.winfo_exists() else None
            ))
        except Exception:
            pass

    def _load_detail_icon_local(self, path):
        try:
            if path in self._icon_cache:
                pil = self._icon_cache[path]
            else:
                pil = _round_sq_crop(Image.open(path), 82)
                self._icon_cache[path] = pil
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(82, 82))
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
        subdir = app.get("subdir", "")
        
        all_urls = []
        
        # 1. Check README images
        all_urls.extend(self.api.get_readme_images(full_name))
        
        # 2. Check local root folder if installed
        install_path = os.path.join(INSTALL_BASE, name)
        if os.path.exists(install_path):
            try:
                for f in os.listdir(install_path):
                    if f.lower().startswith("screenshot") and f.lower().endswith((".png", ".jpg", ".jpeg")):
                        all_urls.append(os.path.join(install_path, f))
            except: pass
            
        # 3. Check GitHub root folder contents
        if ":" in full_name:
            repo, _ = full_name.split(":", 1)
            try:
                r = requests.get(f"{GITHUB_API_BASE}/repos/{repo}/contents/{subdir}", 
                                 headers=self.api.headers, timeout=5)
                if r.status_code == 200:
                    for item in r.json():
                        fname = item.get("name", "").lower()
                        if fname.startswith("screenshot") and fname.endswith((".png", ".jpg", ".jpeg")):
                            dl_url = item.get("download_url")
                            if dl_url not in all_urls:
                                all_urls.append(dl_url)
            except: pass

        # Filter duplicates and limit
        seen = set()
        unique_urls = []
        for u in all_urls:
            if u not in seen:
                unique_urls.append(u)
                seen.add(u)
        
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
        if self._screenshots_outer.winfo_exists():
            self._screenshots_outer.pack_forget()
        if self._ss_sep_top.winfo_exists():
            self._ss_sep_top.pack_forget()
        if self._ss_sep_bottom.winfo_exists():
            self._ss_sep_bottom.pack_forget()

    def _load_one_screenshot(self, parent, url):
        try:
            if url.startswith("http"):
                r = requests.get(url, timeout=10)
                img = Image.open(BytesIO(r.content))
            else:
                img = Image.open(url)
            
            # Basic validation: must be reasonably sized to be a screenshot
            if img.width < 100 or img.height < 100:
                raise Exception("Too small")

            h = 190
            w = int(img.width * h / img.height)
            img = img.resize((w, h), Image.Resampling.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))
            self.after(0, lambda ci=ctk_img, p=parent: self._place_screenshot(ci, p))
        except:
            self.after(0, self._on_ss_fail)

    def _on_ss_fail(self):
        self._loaded_ss_count = getattr(self, "_loaded_ss_count", 0)
        self._total_ss_attempted = getattr(self, "_total_ss_attempted", 0)
        # If all attempted images failed, hide the section
        if hasattr(self, "_ss_scroll") and not self._ss_scroll.winfo_children():
            # We check again after some time or on last fail
            pass

    def _place_screenshot(self, ctk_img, parent):
        if not parent.winfo_exists():
            return
        self._ss_refs.append(ctk_img)
        self._loaded_ss_count += 1
        frame = ctk.CTkFrame(parent, fg_color="#1a1a2e", corner_radius=10)
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
                    if issue.get("title") == name:
                        body = issue.get("body", "")
                        rating_match = re.search(r"Ratings:\s*([1-5])", body, re.IGNORECASE)
                        comment_match = re.search(r"Comment:\s*(.*)", body, re.IGNORECASE | re.DOTALL)
                        user_match = re.search(r"User:\s*(.*)", body, re.IGNORECASE)
                        
                        rating = rating_match.group(1) if rating_match else "0"
                        comment = comment_match.group(1).strip() if comment_match else body.strip()
                        display_name = user_match.group(1).strip() if user_match else issue.get("user", {}).get("login", "Unknown")
                        
                        reviews.append({
                            "id": issue.get("number"),
                            "login": issue.get("user", {}).get("login"),
                            "user": display_name,
                            "rating": int(rating),
                            "comment": comment,
                            "avatar": issue.get("user", {}).get("avatar_url", "")
                        })
        except:
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
                         text_color="#555", font=ctk.CTkFont(size=12, slant="italic")).pack(pady=10)
            return
            
        my_login = self.config_db.get_username()
        for i, rev in enumerate(reviews):
            card = ctk.CTkFrame(self._reviews_list, fg_color="#12122a", corner_radius=8)
            card.pack(fill="x", pady=5)
            
            header = ctk.CTkFrame(card, fg_color="transparent")
            header.pack(fill="x", padx=10, pady=(10, 5))
            
            # Left: Avatar + Name
            left = ctk.CTkFrame(header, fg_color="transparent")
            left.pack(side="left")
            
            ava_size = 28
            ph = _placeholder_icon(rev["user"], ava_size)
            ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(ava_size, ava_size))
            ava_lbl = ctk.CTkLabel(left, image=ctk_ph, text="")
            ava_lbl.pack(side="left", padx=(0, 8))
            
            ref_id = f"rev_ava_{i}"
            self._ctk_img_refs[ref_id] = ctk_ph
            
            ctk.CTkLabel(left, text=rev["user"], font=ctk.CTkFont(size=13, weight="bold"), text_color="#e8eaed").pack(side="left")
            
            # Right: Tools (Edit, Delete, Reply) + Stars
            right = ctk.CTkFrame(header, fg_color="transparent")
            right.pack(side="right")
            
            stars_text = "★" * rev["rating"] + "☆" * (5 - rev["rating"])
            ctk.CTkLabel(right, text=stars_text, font=ctk.CTkFont(size=14), text_color="#ffb400").pack(side="right", padx=(10, 0))
            
            tools = ctk.CTkFrame(right, fg_color="transparent")
            tools.pack(side="right")
            
            tbtn = dict(width=24, height=24, corner_radius=12, fg_color="transparent", 
                        hover_color="#1a1a30", font=ctk.CTkFont(size=14))
            
            # Reply is for everyone
            ctk.CTkButton(tools, text="↩", command=lambda r=rev: self._show_reply_dialog(app, r), **tbtn).pack(side="left", padx=2)
            
            # Edit/Delete only for owner
            if rev["login"] == my_login:
                ctk.CTkButton(tools, text="✎", command=lambda r=rev: self._show_write_review_dialog(app, edit_rev=r), **tbtn).pack(side="left", padx=2)
                ctk.CTkButton(tools, text="🗑", text_color="#ff4b4b", command=lambda r=rev: self._delete_review(app, r), **tbtn).pack(side="left", padx=2)

            ctk.CTkLabel(card, text=rev["comment"], font=ctk.CTkFont(size=12), text_color="#9aa0a6", 
                         justify="left", wraplength=480, anchor="w").pack(fill="x", padx=10, pady=(0, 10))
            
            if rev.get("avatar"):
                threading.Thread(target=self._load_review_avatar, args=(ava_lbl, rev["avatar"], ref_id), daemon=True).start()

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
                    self.after(0, lambda: tk.messagebox.showerror("Error", f"Failed to delete: {r.status_code}"))
            except:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _show_reply_dialog(self, app, rev):
        if not self.api.token:
            tk.messagebox.showwarning("Login Required", "You must be logged in to reply.")
            self._login()
            return
            
        win = ctk.CTkToplevel(self)
        win.title(f"Reply to {rev['user']}")
        win.geometry("400x300")
        win.configure(fg_color="#0f0f1a")
        win.transient(self)
        
        ctk.CTkLabel(win, text=f"Reply to {rev['user']}", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(20, 10))
        
        comment_box = ctk.CTkTextbox(win, height=120, fg_color="#1a1a30", border_color="#2a2a50", border_width=1)
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
                            tk.messagebox.showerror("Error", "Failed to post reply."),
                            sub_btn.configure(state="normal", text="Post Reply")
                        ))
                except:
                    pass
            threading.Thread(target=run, daemon=True).start()
            
        sub_btn = ctk.CTkButton(win, text="Post Reply", command=submit)
        sub_btn.pack(pady=20)

    def _load_review_avatar(self, label, url, ref_id):
        try:
            r = requests.get(url, timeout=5)
            pil = _round_sq_crop(Image.open(BytesIO(r.content)), 28)
            ctk_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(28, 28))
            self._ctk_img_refs[ref_id] = ctk_img
            self.after(0, lambda: label.configure(image=ctk_img) if label.winfo_exists() else None)
        except:
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
        win.configure(fg_color="#0f0f1a")
        win.transient(self)
        
        ctk.CTkLabel(win, text=f"{title_text} for {app.get('name')}", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))
        
        rating_frame = ctk.CTkFrame(win, fg_color="transparent")
        rating_frame.pack(pady=10)
        
        self._selected_rating = edit_rev["rating"] if edit_rev else 5
        stars_btns = []
        def set_rating(r):
            self._selected_rating = r
            for i, btn in enumerate(stars_btns):
                btn.configure(text="★" if i < r else "☆", text_color="#ffb400" if i < r else "#555")
                
        for i in range(1, 6):
            b = ctk.CTkButton(rating_frame, text="★", width=30, height=30, fg_color="transparent", 
                              hover_color="#1a1a30", font=ctk.CTkFont(size=24), text_color="#ffb400",
                              command=lambda idx=i: set_rating(idx))
            b.pack(side="left", padx=2)
            stars_btns.append(b)
        
        set_rating(self._selected_rating)
            
        comment_box = ctk.CTkTextbox(win, height=120, fg_color="#1a1a30", border_color="#2a2a50", border_width=1)
        comment_box.pack(fill="x", padx=20, pady=10)
        if edit_rev:
            comment_box.insert("1.0", edit_rev["comment"])
        
        def submit():
            comment = comment_box.get("1.0", "end").strip()
            if not comment:
                tk.messagebox.showwarning("Error", "Please write a comment.")
                return
                
            sub_btn.configure(state="disabled", text="Saving..." if edit_rev else "Submitting...")
            def run():
                success = self._post_review(app, self._selected_rating, comment, edit_id=edit_rev["id"] if edit_rev else None)
                if success:
                    self.after(0, lambda: (
                        tk.messagebox.showinfo("Success", "Review saved successfully!"),
                        win.destroy(),
                        self._load_reviews(app)
                    ))
                else:
                    self.after(0, lambda: (
                        tk.messagebox.showerror("Error", "Failed to save review."),
                        sub_btn.configure(state="normal", text="Submit Review")
                    ))
            threading.Thread(target=run, daemon=True).start()
            
        sub_btn = ctk.CTkButton(win, text="Save Review" if edit_rev else "Submit Review", command=submit)
        sub_btn.pack(pady=20)

    def _post_review(self, app, rating, comment, edit_id=None):
        full_name = app.get("full_name", "")
        name = app.get("name", "")
        repo = full_name.split(":")[0] if ":" in full_name else full_name
        username = self.config_db.get_username() or "User"
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
        except:
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
                    text_color="#3a3a5a", font=ctk.CTkFont(size=11)
                )
                lbl.pack(padx=12, pady=10)
                threading.Thread(
                    target=self._fetch_readme, args=(app, lbl), daemon=True
                ).start()

    def _fetch_readme(self, app, loading_lbl):
        full_name = app.get("full_name", "")
        readme_path = app.get("readme_path")
        text = None
        if readme_path and os.path.exists(readme_path):
            try:
                with open(readme_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except:
                pass
        
        if not text:
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

    def _check_app_update(self, app):
        full_name = app.get("full_name", "")
        if ":" not in full_name:
            return
            
        repo, subdir = full_name.split(":", 1)
        url = f"{GITHUB_API_BASE}/repos/{repo}/commits?path={subdir}&per_page=1"
        
        self._check_upd_btn.configure(state="disabled", text="Checking...")
        
        def run():
            try:
                r = requests.get(url, headers=self.api.headers, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if data:
                        latest_pushed = data[0]["commit"]["committer"]["date"]
                        if self.db.needs_update(full_name, latest_pushed):
                            app["pushed_at"] = latest_pushed # Update object for refresh
                            self.after(0, lambda: (
                                tk.messagebox.showinfo("Update Available", f"A new version of {app.get('name')} is available!"),
                                self.show_detail(app)
                            ))
                        else:
                            self.after(0, lambda: (
                                tk.messagebox.showinfo("No Updates", f"{app.get('name')} is already up to date."),
                                self._check_upd_btn.configure(state="normal", text="Check for updates")
                                if self._check_upd_btn.winfo_exists() else None
                            ))
                    else:
                        self.after(0, lambda: self._check_upd_btn.configure(state="normal", text="Check for updates"))
                else:
                    self.after(0, lambda: self._check_upd_btn.configure(state="normal", text="Check for updates"))
            except:
                self.after(0, lambda: self._check_upd_btn.configure(state="normal", text="Check for updates") 
                           if self._check_upd_btn.winfo_exists() else None)
        
        threading.Thread(target=run, daemon=True).start()

    def _update_install_ui(self, full_name, status, progress, finished=False, error=False):
        if full_name not in self._active_installs:
            return
        
        data = self._active_installs[full_name]
        data["status"] = status
        data["progress"] = progress
        
        # 1. Update Downloads View if visible
        if self.downloads_view.winfo_ismapped():
            if "ui_prog" in data and data["ui_prog"].winfo_exists():
                self.after(0, lambda: (
                    data["ui_prog"].set(progress),
                    data["ui_status"].configure(text=status, text_color="#ff4b4b" if error else "#1a73e8")
                ))

        # 2. Update Detail View if visible and matches app
        if self.detail_container.winfo_ismapped() and hasattr(self, "_current_viewing_fn") and self._current_viewing_fn == full_name:
            if hasattr(self, "_install_progress") and self._install_progress.winfo_exists():
                self.after(0, lambda: (
                    self._install_progress.set(progress),
                    self._install_status_lbl.configure(text=status, text_color="#ff4b4b" if error else "#1a73e8")
                ))

        if finished or error:
            # We keep it in list for a few seconds if finished
            def cleanup():
                if full_name in self._active_installs:
                    del self._active_installs[full_name]
                    if self.downloads_view.winfo_ismapped():
                        self._render_downloads_list()
            self.after(5000, cleanup)

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
        subdir = app.get("subdir", "")
        pushed_at = app.get("pushed_at", "")
        install_path = os.path.join(INSTALL_BASE, name)

        if full_name in self._active_installs:
            return # Already installing

        action_label = "Updating" if is_update else "Installing"
        self._active_installs[full_name] = {"status": "Starting...", "progress": 0, "app": app}
        
        # Update current UI if it's the right app
        if hasattr(self, "_current_viewing_fn") and self._current_viewing_fn == full_name:
            self._install_progress_frame.pack(fill="x", padx=16, after=self._primary_btn)
            self._primary_btn.configure(state="disabled", text=action_label + "...")

        def run():
            try:
                self._update_install_ui(full_name, f"Cleaning {name} folder...", 0.1)
                if os.path.exists(install_path):
                    subprocess.run(["rm", "-rf", install_path], check=True)
                
                tmp_dir = os.path.expanduser(f"~/.appstore_tmp_{name}")
                if os.path.exists(tmp_dir):
                    subprocess.run(["rm", "-rf", tmp_dir])
                os.makedirs(tmp_dir)
                
                self._update_install_ui(full_name, "Cloning repository...", 0.3)
                subprocess.run(
                    ["git", "clone", "--no-checkout", "--depth", "1", "--filter=blob:none", repo_url, tmp_dir],
                    check=True, capture_output=True
                )
                
                self._update_install_ui(full_name, "Checking out files...", 0.5)
                subprocess.run(
                    ["git", "sparse-checkout", "set", subdir],
                    cwd=tmp_dir, check=True, capture_output=True
                )
                subprocess.run(
                    ["git", "checkout"], cwd=tmp_dir, check=True, capture_output=True
                )
                
                src = os.path.join(tmp_dir, subdir)
                if not os.path.exists(src):
                    raise Exception("Folder not found in repo")
                
                self._update_install_ui(full_name, "Moving to apps folder...", 0.7)
                subprocess.run(["mv", src, install_path], check=True)
                subprocess.run(["rm", "-rf", tmp_dir])
                    
                script = os.path.join(install_path, "install.sh")
                if os.path.exists(script):
                    self._update_install_ui(full_name, "Running install.sh...", 0.85)
                    subprocess.run(["bash", "install.sh"], cwd=install_path, check=True, capture_output=True)
                
                self.db.add(full_name, name, install_path, pushed_at, app_data=app)
                self._update_install_ui(full_name, "Success!", 1.0, finished=True)
                
                if hasattr(self, "_current_viewing_fn") and self._current_viewing_fn == full_name:
                    self.after(1000, lambda: self.show_detail(app))
            except Exception as e:
                err_msg = str(e)[:30] + "..."
                self._update_install_ui(full_name, f"Error: {err_msg}", 0, error=True)
                if hasattr(self, "_current_viewing_fn") and self._current_viewing_fn == full_name:
                    self.after(3000, lambda: self._primary_btn.configure(state="normal", text=action_label))

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
            
            # Run uninstall.sh if it exists
            un_script = os.path.join(install_path, "uninstall.sh")
            if os.path.exists(un_script):
                subprocess.run(["bash", "uninstall.sh"], cwd=install_path)
                
            subprocess.run(["rm", "-rf", install_path], check=True)
            self.db.remove(full_name)
            self.show_detail(app)
        except Exception as e:
            tk.messagebox.showerror("Error", str(e))

    def _login(self):
        if self.api.token:
            self._show_profile_dialog()
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
        win.configure(fg_color="#0f0f1a")
        win.transient(self)

        ctk.CTkLabel(win, text="Enter your GitHub OAuth Client ID", 
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(25, 10))
        
        desc = ("To log in and post reviews, you need a GitHub OAuth Client ID.\n\n"
                "1. Go to GitHub Settings -> Developer Settings -> OAuth Apps.\n"
                "2. Create a new app and enable 'Device Flow'.\n"
                "3. Copy the Client ID and paste it below.")
        ctk.CTkLabel(win, text=desc, font=ctk.CTkFont(size=11), text_color="#9aa0a6", 
                     justify="center", wraplength=360).pack(pady=(0, 20))

        entry = ctk.CTkEntry(win, width=280, placeholder_text="Paste Client ID here...")
        entry.pack(pady=5)

        def save():
            cid = entry.get().strip()
            if cid:
                self.config_db.set_client_id(cid)
                win.destroy()
                self._show_login_dialog(cid)
            else:
                tk.messagebox.showerror("Error", "Client ID cannot be empty.")

        ctk.CTkButton(win, text="Save & Continue", width=160, height=36, 
                      corner_radius=18, command=save).pack(pady=20)

    def _show_profile_dialog(self):
        win = ctk.CTkToplevel(self)
        win.title("GitHub Profile")
        win.geometry("300x200")
        win.configure(fg_color="#0f0f1a")
        win.transient(self)

        username = self.config_db.get_username() or "User"
        ctk.CTkLabel(win, text=f"Logged in as:", font=ctk.CTkFont(size=12), text_color="#9aa0a6").pack(pady=(30, 0))
        ctk.CTkLabel(win, text=username, font=ctk.CTkFont(size=18, weight="bold"), text_color="#e8eaed").pack(pady=(0, 20))

        def logout():
            self.api.set_token(None)
            self.config_db.clear_token()
            self._profile_btn.configure(text_color="#e8eaed")
            win.destroy()
            tk.messagebox.showinfo("Logout", "You have been logged out.")

        ctk.CTkButton(win, text="Logout", fg_color="#4a1a1a", hover_color="#6a1a1a", command=logout).pack(pady=10)

    def _show_login_dialog(self, client_id):
        data = self.api.start_device_flow(client_id)
        if not data:
            tk.messagebox.showerror("Error", "Could not connect to GitHub. Is your Client ID valid and 'Device Flow' enabled?")
            self.config_db.set_client_id(None) # Allow retry
            return

        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        device_code = data["device_code"]
        interval = data["interval"]

        win = ctk.CTkToplevel(self)
        win.title("GitHub Login")
        win.geometry("460x380")
        win.configure(fg_color="#0f0f1a")
        win.transient(self)

        ctk.CTkLabel(win, text="Link your GitHub Account", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(25, 15))
        
        inst = ("1. Click the button below to open GitHub.\n"
                "2. Sign in if required.\n"
                "3. Enter the 8-digit code shown below.")
        ctk.CTkLabel(win, text=inst, font=ctk.CTkFont(size=13), justify="left", text_color="#9aa0a6").pack(pady=10)

        code_frame = ctk.CTkFrame(win, fg_color="#1a1a30", corner_radius=10)
        code_frame.pack(pady=20, padx=40, fill="x")
        
        ctk.CTkLabel(code_frame, text=user_code, font=ctk.CTkFont(size=36, weight="bold", family="monospace"), 
                     text_color="#7eb3ff").pack(pady=15)

        status_lbl = ctk.CTkLabel(win, text="Waiting for authorization...", font=ctk.CTkFont(size=12, slant="italic"), text_color="#555")
        status_lbl.pack()

        def open_browser():
            webbrowser.open(verification_uri)

        ctk.CTkButton(win, text="Open Browser", width=200, height=40, corner_radius=20, 
                      font=ctk.CTkFont(size=14, weight="bold"), command=open_browser).pack(pady=20)

        def wait():
            token = self.api.poll_for_token(client_id, device_code, interval)
            if token:
                self.api.set_token(token)
                self.config_db.set_token(token)
                user = self.api.get_current_user()
                if user:
                    self.config_db.set_username(user.get("login"))
                    self.config_db.set_display_name(user.get("name") or user.get("login"))
                
                self.after(0, lambda: (
                    win.destroy(),
                    self._profile_btn.configure(text_color="#34a853"),
                    tk.messagebox.showinfo("Login Success", f"Welcome, {self.config_db.get_display_name()}!")
                ))

        threading.Thread(target=wait, daemon=True).start()


if __name__ == "__main__":
    app = AppStoreApp()
    app.mainloop()