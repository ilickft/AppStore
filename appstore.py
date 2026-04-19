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
from PIL import Image, ImageDraw
from io import BytesIO

GITHUB_API_BASE = "https://api.github.com"
VERIFIED_REPOS_URL = "https://raw.githubusercontent.com/ilickft/AppStore/refs/heads/main/repos.txt"
APPSTORE_REPO_URL = "https://github.com/ilickft/AppStore/"
APPSTORE_VERSION = "1.2.0"
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
            except: self._db = {}
    def _save(self):
        with open(INSTALL_DB_PATH, "w") as f:
            json.dump(self._db, f, indent=2)
    def is_installed(self, full_name):
        return full_name in self._db and os.path.isdir(self._db[full_name].get("path", ""))
    def get(self, full_name): return self._db.get(full_name)
    def add(self, full_name, name, path, pushed_at, app_data=None):
        self._db[full_name] = {"name": name, "path": path, "pushed_at": pushed_at, "app_data": app_data}
        self._save()
    def get_all_installed(self):
        apps = []
        if not os.path.exists(INSTALL_BASE): return apps
        for name in os.listdir(INSTALL_BASE):
            path = os.path.join(INSTALL_BASE, name)
            if not os.path.isdir(path): continue
            db_entry = next((v for v in self._db.values() if v.get("name") == name), None)
            app_data = db_entry.get("app_data") if db_entry else None
            if app_data: app_data = app_data.copy()
            else:
                full_name = next((k for k, v in self._db.items() if v.get("name") == name), f"local/{name}")
                app_data = {
                    "full_name": full_name, "name": name, "description": "Locally installed application.",
                    "stargazers_count": 0, "forks_count": 0, "language": "Bash",
                    "pushed_at": db_entry.get("pushed_at", "") if db_entry else "",
                    "html_url": "", "owner": {"login": "local", "avatar_url": ""},
                    "subdir": name, "repo_name": "local", "category": "Installed"
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
                with open(self._path) as f: self._data = json.load(f)
            except: self._data = {}
    def _save(self):
        try:
            with open(self._path, "w") as f: json.dump(self._data, f, indent=2)
            os.chmod(self._path, 0o600)
        except: pass
    def get_token(self): return self._data.get("token")
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
    def get_username(self): return self._data.get("username")
    def set_display_name(self, name):
        self._data["display_name"] = name
        self._save()
    def get_display_name(self): return self._data.get("display_name") or self.get_username()
    def get_client_id(self): return self._data.get("client_id")
    def set_client_id(self, client_id):
        self._data["client_id"] = client_id
        self._save()

class GitHubAPI:
    def __init__(self):
        self.token = None
        self.verified_repos = set()
        self.headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Termux-AppStore"}
    def set_token(self, token):
        self.token = token
        if token: self.headers["Authorization"] = f"token {token}"
        elif "Authorization" in self.headers: del self.headers["Authorization"]
    def get_current_user(self):
        if not self.token: return None
        try:
            r = requests.get(f"{GITHUB_API_BASE}/user", headers=self.headers, timeout=8)
            if r.status_code == 200: return r.json()
        except: pass
        return None
    def fetch_verified_repos(self):
        try:
            r = requests.get(VERIFIED_REPOS_URL, timeout=5)
            if r.status_code == 200:
                self.verified_repos = {line.strip() for line in r.text.splitlines() if line.strip() and "/" in line}
        except: pass
        return self.verified_repos
    def is_verified(self, full_name):
        return full_name.startswith("App-Store-tmx/") or full_name in self.verified_repos
    def search_by_topic(self, topic):
        apps = []
        url = f"{GITHUB_API_BASE}/search/repositories?q=topic:{topic}"
        try:
            r = requests.get(url, headers=self.headers, timeout=12)
            if r.status_code == 200:
                items = r.json().get("items", [])
                for item in items:
                    apps.append({
                        "full_name": item["full_name"], "name": item["name"], "description": item["description"],
                        "stargazers_count": item["stargazers_count"], "forks_count": item["forks_count"],
                        "language": item["language"], "pushed_at": item["pushed_at"], "html_url": item["html_url"],
                        "owner": item["owner"], "subdir": "", "repo_name": item["name"],
                        "icon_url": f"https://raw.githubusercontent.com/{item['full_name']}/main/icon.png"
                    })
        except: pass
        return apps
    def search_apps(self):
        all_apps = []
        repos = ["App-Store-tmx/Games", "App-Store-tmx/Apps"]
        for repo_full_name in repos:
            try:
                r = requests.get(f"{GITHUB_API_BASE}/repos/{repo_full_name}/contents", headers=self.headers, timeout=12)
                if r.status_code == 200:
                    for item in r.json():
                        if item.get("type") == "dir" and not item["name"].startswith("."):
                            cat = "Games" if "Games" in repo_full_name else "Apps"
                            all_apps.append({
                                "full_name": f"{repo_full_name}:{item['name']}", "name": item["name"],
                                "description": f"An app from the {cat} store.", "stargazers_count": 0, "forks_count": 0,
                                "language": "Bash", "pushed_at": "", "html_url": f"https://github.com/{repo_full_name}",
                                "owner": {"login": repo_full_name.split('/')[0], "avatar_url": ""},
                                "subdir": item["name"], "repo_name": repo_full_name.split('/')[1], "category": cat,
                                "icon_url": f"https://raw.githubusercontent.com/{repo_full_name}/main/{item['name']}/icon.png"
                            })
            except: pass
        for a in self.search_by_topic("termux-desk-app"): a["category"] = "Apps"; all_apps.append(a)
        for a in self.search_by_topic("termux-desk-game"): a["category"] = "Games"; all_apps.append(a)
        for tag in ["termux-desk", "termux-desk-tool"]:
            for a in self.search_by_topic(tag): a["category"] = "Public"; all_apps.append(a)
        unique, seen = [], set()
        for a in all_apps:
            key = (a["full_name"], a["category"])
            if key not in seen: unique.append(a); seen.add(key)
        return unique
    def get_readme(self, full_name):
        try:
            if ":" in full_name:
                repo, subdir = full_name.split(":", 1)
                url = f"{GITHUB_API_BASE}/repos/{repo}/readme/{subdir}"
            else: url = f"{GITHUB_API_BASE}/repos/{full_name}/readme"
            r = requests.get(url, headers=self.headers, timeout=5)
            if r.status_code == 200:
                dl = r.json().get("download_url")
                if dl: return requests.get(dl, timeout=8).text
        except: pass
        return None
    def get_readme_images(self, full_name):
        text = self.get_readme(full_name)
        return re.findall(r'!\[.*?\]\((.*?)\)', text) if text else []
    def check_appstore_update(self):
        try:
            r = requests.get("https://raw.githubusercontent.com/ilickft/AppStore/main/appstore.py", timeout=5)
            if r.status_code == 200:
                m = re.search(r'APPSTORE_VERSION\s*=\s*"([^"]+)"', r.text)
                if m: return m.group(1)
        except: pass
        return None
    def start_device_flow(self, client_id):
        try:
            r = requests.post("https://github.com/login/device/code", data={"client_id": client_id, "scope": "public_repo"}, headers={"Accept": "application/json"}, timeout=10)
            if r.status_code == 200: return r.json()
        except: pass
        return None
    def poll_for_token(self, client_id, device_code, interval):
        while True:
            try:
                r = requests.post("https://github.com/login/oauth/access_token", data={"client_id": client_id, "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, headers={"Accept": "application/json"}, timeout=10)
                d = r.json()
                if "access_token" in d: return d["access_token"]
                if d.get("error") not in ("authorization_pending", "slow_down"): return None
            except: return None
            time.sleep(interval)

class AppStoreApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Termux AppStore")
        self.geometry("600x400")
        ctk.set_appearance_mode("dark")
        self.configure(fg_color="#0f0f1a")
        icon_path = os.path.join(os.path.dirname(__file__), "AppStore.png")
        if os.path.exists(icon_path):
            try: self.wm_iconphoto(True, tk.PhotoImage(file=icon_path))
            except: pass
        self.api, self.config_db, self.db = GitHubAPI(), ConfigDB(), InstalledDB()
        if self.config_db.get_token(): self.api.set_token(self.config_db.get_token())
        self.loaded_apps, self._apps_fetched = [], False
        self._download_queue, self._download_history = [], []
        self._queue_worker_running, self._queue_lock = False, threading.Lock()
        self._search_after, self._search_visible = None, False
        self._icon_cache, self._ctk_img_refs, self._tile_icon_labels = {}, {}, {}
        self._ss_refs, self._detail_readme_visible, self._readme_loaded = [], False, False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self.current_category = "All"
        self._build_header()
        self._build_tabs()
        self._build_search_row()
        self._build_body()
        self.show_home()
        threading.Thread(target=self._check_updates_silent, daemon=True).start()
        self.bind_all("<Button-4>", self._on_mousewheel); self.bind_all("<Button-5>", self._on_mousewheel); self.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        sf = self.home_view if self.home_view.winfo_viewable() else (self.downloads_view if self.downloads_view.winfo_viewable() else self.detail_view)
        try:
            c = getattr(sf, "_parent_canvas", getattr(sf, "_canvas", None))
            if not c: return
            m = 8
            if event.num == 4: c.yview_scroll(-m, "units")
            elif event.num == 5: c.yview_scroll(m, "units")
            elif hasattr(event, "delta") and event.delta != 0: c.yview_scroll(int(-1 * (event.delta / 25)) * m, "units")
        except: pass

    def _check_updates_silent(self):
        v = self.api.check_appstore_update()
        if v and v != APPSTORE_VERSION: self.after(0, lambda: self._update_btn.configure(text="⤒ ●", text_color="#ff9800"))
        elif v == APPSTORE_VERSION: self.after(0, lambda: self._update_btn.configure(text="⤒ ●", text_color="#34a853"))

    def _build_header(self):
        hdr = ctk.CTkFrame(self, height=52, corner_radius=0, fg_color="#12122a")
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1); hdr.grid_propagate(False)
        ctk.CTkLabel(hdr, text="  ◈  AppStore", font=ctk.CTkFont(size=17, weight="bold"), text_color="#7eb3ff").grid(row=0, column=0, padx=12, pady=10, sticky="w")
        icons = ctk.CTkFrame(hdr, fg_color="transparent"); icons.grid(row=0, column=2, padx=10, sticky="e")
        ibtn = dict(width=34, height=34, corner_radius=17, fg_color="transparent", hover_color="#1e1e40", font=ctk.CTkFont(size=17))
        ctk.CTkButton(icons, text="⌕", command=self._toggle_search, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons, text="⌂", command=self.show_home, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons, text="⬇", command=self.show_downloads, **ibtn).pack(side="left", padx=1)
        ctk.CTkButton(icons, text="↻", command=self._force_refresh_apps, **ibtn).pack(side="left", padx=1)
        self._update_btn = ctk.CTkButton(icons, text="⤒", command=self._update_appstore, **ibtn); self._update_btn.pack(side="left", padx=1)
        self._profile_btn = ctk.CTkButton(icons, text="◉", command=self._login, **ibtn); self._profile_btn.pack(side="left", padx=(1, 4))

    def _build_tabs(self):
        self._tabs_row = ctk.CTkFrame(self, height=40, corner_radius=0, fg_color="#0f0f1a"); self._tabs_row.grid(row=1, column=0, sticky="ew")
        self._tab_btns = {}
        for name in ["All", "Apps", "Games", "Public", "Installed"]:
            btn = ctk.CTkButton(self._tabs_row, text=name, width=80, height=30, corner_radius=15, fg_color="#1a73e8" if name == self.current_category else "transparent", text_color="#ffffff" if name == self.current_category else "#9aa0a6", font=ctk.CTkFont(size=13, weight="bold" if name == self.current_category else "normal"), command=lambda n=name: self._set_category(n))
            btn.pack(side="left", padx=5, pady=5); self._tab_btns[name] = btn
        self._tab_btns["All"].pack_configure(padx=(20, 5))

    def _set_category(self, cat):
        self.current_category = cat
        for n, b in self._tab_btns.items():
            if n == cat: b.configure(fg_color="#1a73e8", text_color="#ffffff", font=ctk.CTkFont(size=13, weight="bold"))
            else: b.configure(fg_color="transparent", text_color="#9aa0a6", font=ctk.CTkFont(size=13))
        self._apply_filter()

    def _update_appstore(self):
        conf = ctk.CTkToplevel(self); conf.title("Confirm Update"); conf.geometry("380x200"); conf.configure(fg_color="#0f0f1a")
        ctk.CTkLabel(conf, text="Update AppStore to the latest version?", font=ctk.CTkFont(size=14)).pack(pady=40)
        btns = ctk.CTkFrame(conf, fg_color="transparent"); btns.pack(fill="x", side="bottom", pady=20)
        ctk.CTkButton(btns, text="Yes, Update", width=110, height=36, command=lambda: (conf.destroy(), self._do_update_appstore())).pack(side="right", padx=15)
        ctk.CTkButton(btns, text="Cancel", width=90, height=36, fg_color="#333", command=conf.destroy).pack(side="right", padx=5)

    def _do_update_appstore(self):
        win = ctk.CTkToplevel(self); win.title("Updating AppStore"); win.geometry("540x450"); win.configure(fg_color="#0f0f1a")
        log = ctk.CTkTextbox(win, fg_color="#0a0a14", border_width=1, font=ctk.CTkFont(family="monospace", size=11)); log.pack(fill="both", expand=True, padx=15, pady=10)
        btn = ctk.CTkButton(win, text="Close", state="disabled", command=win.destroy); btn.pack(pady=10)
        def run():
            tmp = os.path.expanduser("~/.appstore_tmp_update")
            if os.path.exists(tmp): subprocess.run(["rm", "-rf", tmp])
            p = subprocess.Popen(["git", "clone", "--depth", "1", APPSTORE_REPO_URL, tmp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for l in p.stdout: self.after(0, lambda t=l: (log.insert("end", t), log.see("end")))
            p.wait()
            if p.returncode == 0 and os.path.exists(os.path.join(tmp, "install.sh")):
                p2 = subprocess.Popen(["bash", "install.sh"], cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for l in p2.stdout: self.after(0, lambda t=l: (log.insert("end", t), log.see("end")))
                p2.wait()
            self.after(0, lambda: btn.configure(state="normal"))
        threading.Thread(target=run, daemon=True).start()

    def _build_search_row(self):
        self._search_row = ctk.CTkFrame(self, height=46, corner_radius=0, fg_color="#0d0d20")
        self._search_entry = ctk.CTkEntry(self._search_row, placeholder_text="Search...", height=32, font=ctk.CTkFont(size=13), fg_color="#1a1a30", border_color="#2a2a50")
        self._search_entry.pack(fill="x", padx=14, pady=7); self._search_entry.bind("<KeyRelease>", self._on_search_key)

    def _build_body(self):
        self.body = ctk.CTkFrame(self, fg_color="#0f0f1a", corner_radius=0); self.body.grid(row=3, column=0, sticky="nsew"); self.body.grid_columnconfigure(0, weight=1); self.body.grid_rowconfigure(0, weight=1)
        self.home_view = ctk.CTkScrollableFrame(self.body, fg_color="#0f0f1a", corner_radius=0); self.home_view.grid(row=0, column=0, sticky="nsew")
        self.detail_container = ctk.CTkFrame(self.body, fg_color="#0f0f1a", corner_radius=0)
        bar = ctk.CTkFrame(self.detail_container, height=48, fg_color="#12122a", corner_radius=0); bar.pack(fill="x")
        ctk.CTkButton(bar, text="←  Back", width=80, height=30, corner_radius=15, fg_color="transparent", border_width=1, font=ctk.CTkFont(size=12), command=self.show_home).pack(side="left", padx=16, pady=9)
        self.detail_view = ctk.CTkScrollableFrame(self.detail_container, fg_color="#0f0f1a", corner_radius=0); self.detail_view.pack(fill="both", expand=True)
        self.downloads_view = ctk.CTkScrollableFrame(self.body, fg_color="#0f0f1a", corner_radius=0)

    def show_downloads(self):
        self.title("Downloads - AppStore"); self.home_view.grid_forget(); self.detail_container.grid_forget(); self._tabs_row.grid_forget()
        if self._search_visible: self._search_row.grid(row=2, column=0, sticky="ew")
        self.downloads_view.grid(row=0, column=0, sticky="nsew"); self._apply_filter()

    def _render_downloads_list(self, history=None):
        for w in self.downloads_view.winfo_children(): w.destroy()
        row = ctk.CTkFrame(self.downloads_view, fg_color="transparent"); row.pack(fill="x", padx=20, pady=(20, 10))
        ctk.CTkLabel(row, text="Download History", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        if any(h["status"] in ["completed", "error"] for h in self._download_history): ctk.CTkButton(row, text="Clear Completed", width=120, height=28, fg_color="#333", command=self._clear_history).pack(side="right")
        items = history if history is not None else self._download_history
        if not items: ctk.CTkLabel(self.downloads_view, text="No download history.", text_color="#555").pack(pady=40); return
        for data in reversed(items):
            app, fn = data["app"], data["full_name"]
            card = ctk.CTkFrame(self.downloads_view, fg_color="#12122a", corner_radius=10); card.pack(fill="x", padx=20, pady=5)
            main = ctk.CTkFrame(card, fg_color="transparent"); main.pack(fill="x", padx=15, pady=10)
            info = ctk.CTkFrame(main, fg_color="transparent"); info.pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(info, text=app.get("name"), font=ctk.CTkFont(size=14, weight="bold"), anchor="w").pack(fill="x")
            st_color = {"completed": "#34a853", "error": "#ea4335", "pending": "#9aa0a6"}.get(data["status"], "#1a73e8")
            ctk.CTkLabel(info, text=data["status"].upper(), font=ctk.CTkFont(size=11, weight="bold"), text_color=st_color, anchor="w").pack(fill="x")
            ctrls = ctk.CTkFrame(main, fg_color="transparent"); ctrls.pack(side="right")
            if data["status"] in ["pending", "downloading"]:
                ctk.CTkButton(ctrls, text="Resume" if data.get("paused") else "Pause", width=70, height=28, corner_radius=14, command=lambda d=data: self._toggle_pause(d)).pack(side="left", padx=2)
                ctk.CTkButton(ctrls, text="Cancel", width=70, height=28, corner_radius=14, fg_color="#421", command=lambda d=data: self._cancel_queued(d)).pack(side="left", padx=2)
            elif data["status"] == "completed": ctk.CTkButton(ctrls, text="Launch", width=70, height=28, corner_radius=14, fg_color="#1e7e34", command=lambda a=app: self._launch(a)).pack(side="left", padx=2)
            if data["status"] in ["downloading", "installing"]:
                p = ctk.CTkProgressBar(card, height=6, corner_radius=3, progress_color="#1a73e8"); p.pack(fill="x", padx=15, pady=(0, 10)); p.set(data["progress"]); data["ui_prog"] = p

    def _clear_history(self): self._download_history = [h for h in self._download_history if h["status"] not in ["completed", "error"]]; self._apply_filter()
    def _toggle_pause(self, data):
        data["paused"] = not data.get("paused", False)
        if data["paused"]:
            data["status"] = "paused"
            if data.get("proc"): 
                try: os.kill(data["proc"].pid, signal.SIGSTOP)
                except: pass
        else:
            data["status"] = "downloading" if data == getattr(self, "_current_task", None) else "pending"
            if data.get("proc"): 
                try: os.kill(data["proc"].pid, signal.SIGCONT)
                except: pass
        self._apply_filter()
    def _cancel_queued(self, data):
        data["cancelled"] = True; data["status"] = "error"
        if data.get("proc"):
            try: data["proc"].terminate()
            except: pass
        if data in self._download_queue: self._download_queue.remove(data)
        self._apply_filter()

    def _toggle_search(self):
        if self._search_visible: self._search_row.grid_forget(); self._search_visible = False
        else: self._search_row.grid(row=2, column=0, sticky="ew"); self._search_visible = True; self._search_entry.focus()
    def _on_search_key(self, _=None):
        if self._search_after: self.after_cancel(self._search_after)
        self._search_after = self.after(280, self._apply_filter)
    def _apply_filter(self, _=None):
        q = self._search_entry.get().strip().lower()
        if self.downloads_view.winfo_viewable():
            res = [h for h in self._download_history]
            if q: res = [h for h in res if q in h["app"].get("name", "").lower()]
            self._render_downloads_list(res); return
        if self.current_category == "Installed": res = self.db.get_all_installed()
        elif self.current_category == "All": res = list(self.loaded_apps)
        else: res = [a for a in self.loaded_apps if a.get("category") == self.current_category]
        res.sort(key=lambda a: a.get("name", "").lower())
        if q: res = [a for a in res if q in a.get("name", "").lower() or q in (a.get("description") or "").lower()]
        self._render_home(res)

    def show_home(self):
        self.title("Home - AppStore"); self.detail_container.grid_forget(); self.downloads_view.grid_forget(); self.home_view.grid(row=0, column=0, sticky="nsew"); self._tabs_row.grid(row=1, column=0, sticky="ew")
        if self._apps_fetched: self._apply_filter(); return
        for w in self.home_view.winfo_children(): w.destroy()
        self._tile_icon_labels.clear(); f = ctk.CTkFrame(self.home_view, fg_color="transparent"); f.pack(expand=True, pady=80); ctk.CTkLabel(f, text="Fetching...", text_color="#555").pack()
        threading.Thread(target=self._fetch_apps, daemon=True).start()
    def _force_refresh_apps(self): self._apps_fetched = False; self.show_home()
    def _fetch_apps(self): self.api.fetch_verified_repos(); self.loaded_apps = self.api.search_apps(); self._apps_fetched = True; self.after(0, self._apply_filter)
    def _render_home(self, apps):
        for w in self.home_view.winfo_children(): w.destroy()
        self._tile_icon_labels.clear()
        if not apps:
            f = ctk.CTkFrame(self.home_view, fg_color="transparent"); f.pack(expand=True, pady=80)
            ctk.CTkLabel(f, text="Nothing found", font=ctk.CTkFont(size=17, weight="bold")).pack()
            ctk.CTkButton(f, text="↻  Retry", command=self.show_home).pack(pady=12); return
        COLS, grid = 5, ctk.CTkFrame(self.home_view, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=14, pady=14)
        for c in range(COLS): grid.grid_columnconfigure(c, weight=1)
        for i, app in enumerate(apps):
            r, c = divmod(i, COLS); self._make_tile(grid, app, r, c)
    def _make_tile(self, parent, app, row, col):
        fn, name = app.get("full_name"), app.get("name")
        tile = ctk.CTkFrame(parent, fg_color="transparent", cursor="hand2"); tile.grid(row=row, column=col, padx=8, pady=14, sticky="n")
        ph = _placeholder_icon(name, 64); ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(64, 64))
        lbl = ctk.CTkLabel(tile, image=ctk_ph, text="", cursor="hand2"); lbl.pack(); self._tile_icon_labels[fn] = lbl; self._ctk_img_refs[fn+"_ph"] = ctk_ph
        ctk.CTkLabel(tile, text=(name if len(name) <= 13 else name[:12] + "…"), font=ctk.CTkFont(size=11), text_color="#c8cacd", wraplength=76).pack(pady=(5,0))
        for w in (tile, lbl): w.bind("<Button-1>", lambda e, a=app: self.show_detail(a))
        u = app.get("icon_url") or app.get("icon_path")
        if u: threading.Thread(target=self._load_tile_icon, args=(fn, u), daemon=True).start()
    def _load_tile_icon(self, fn, u):
        try:
            if u in self._icon_cache: p = self._icon_cache[u]
            elif u.startswith("http"): r = requests.get(u, timeout=5); p = _round_sq_crop(Image.open(BytesIO(r.content)), 64); self._icon_cache[u] = p
            else: p = _round_sq_crop(Image.open(u), 64); self._icon_cache[u] = p
            img = ctk.CTkImage(light_image=p, dark_image=p, size=(64, 64)); self._ctk_img_refs[fn] = img
            self.after(0, lambda: self._tile_icon_labels[fn].configure(image=img) if fn in self._tile_icon_labels and self._tile_icon_labels[fn].winfo_exists() else None)
        except: pass

    def show_detail(self, app):
        fn, name = app.get("full_name"), app.get("name")
        self._current_viewing_fn = fn; self.title(f"{name} - AppStore"); self.home_view.grid_forget(); self.downloads_view.grid_forget(); self._tabs_row.grid_forget()
        if self._search_visible: self._search_row.grid_forget()
        self.detail_container.grid(row=0, column=0, sticky="nsew")
        for w in self.detail_view.winfo_children(): w.destroy()
        self._ss_refs, self._detail_readme_visible, self._readme_loaded = [], False, False
        self._build_detail(app)

    def _build_detail(self, app):
        fn, name, desc = app.get("full_name"), app.get("name"), app.get("description") or ""
        stars, forks, lang = app.get("stargazers_count", 0), app.get("forks_count", 0), app.get("language") or "N/A"
        owner, html = app.get("owner", {}), app.get("html_url", "")
        is_inst = self.db.is_installed(fn); needs_upd = self.db.needs_update(fn, app.get("pushed_at", "")) if is_inst else False
        top = ctk.CTkFrame(self.detail_view, fg_color="transparent"); top.pack(fill="x", padx=16, pady=(0, 6))
        ph = _placeholder_icon(name, 82); ctk_ph = ctk.CTkImage(light_image=ph, dark_image=ph, size=(82, 82))
        self._detail_icon_lbl = ctk.CTkLabel(top, image=ctk_ph, text=""); self._detail_icon_lbl.pack(side="left", padx=(0, 18))
        u = app.get("icon_url") or app.get("icon_path")
        if u: threading.Thread(target=self._load_detail_icon, args=(u,), daemon=True).start()
        col = ctk.CTkFrame(top, fg_color="transparent"); col.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(col, text=name, font=ctk.CTkFont(size=22, weight="bold"), text_color="#e8eaed", anchor="w").pack(fill="x")
        ctk.CTkLabel(col, text=owner.get("login", ""), font=ctk.CTkFont(size=12), text_color="#1a73e8", anchor="w").pack(fill="x", pady=(2, 6))
        meta = ctk.CTkFrame(col, fg_color="transparent"); meta.pack(fill="x")
        for i, v in [("★", f"{stars:,}"), ("⑂", str(forks)), ("◎", lang)]:
            chip = ctk.CTkFrame(meta, fg_color="#1a1a30", corner_radius=10); chip.pack(side="left", padx=(0, 6))
            ctk.CTkLabel(chip, text=f" {i} {v} ", font=ctk.CTkFont(size=10), text_color="#9aa0a6").pack()
        ctk.CTkFrame(self.detail_view, height=1, fg_color="#1e1e40").pack(fill="x", padx=16, pady=14)
        btn_row = ctk.CTkFrame(self.detail_view, fg_color="transparent"); btn_row.pack(fill="x", padx=16, pady=(0, 6))
        txt, fg, hv = ("Launch", "#1e7e34", "#155a24") if (is_inst and not needs_upd) else (("Update", "#e65c00", "#b34700") if needs_upd else ("Install", "#1a73e8", "#1256b4"))
        self._primary_btn = ctk.CTkButton(btn_row, text=txt, width=150, height=42, corner_radius=21, fg_color=fg, hover_color=hv, font=ctk.CTkFont(size=14, weight="bold"), command=lambda: self._primary_action(app, txt))
        self._primary_btn.pack(side="left", padx=(0, 10))
        if is_inst: ctk.CTkButton(btn_row, text="Uninstall", width=110, height=42, corner_radius=21, fg_color="#2a0a0a", text_color="#ff6b6b", command=lambda: self._uninstall(app)).pack(side="left", padx=(0, 10))
        if html: ctk.CTkButton(btn_row, text="GitHub ↗", width=90, height=42, corner_radius=21, fg_color="transparent", border_width=1, text_color="#7eb3ff", command=lambda: webbrowser.open(html)).pack(side="right")
        if desc: ctk.CTkLabel(self.detail_view, text=desc, wraplength=820, justify="left", font=ctk.CTkFont(size=13), text_color="#9aa0a6", anchor="w").pack(fill="x", padx=16, pady=(10, 0))
        self._ss_sep_top = ctk.CTkFrame(self.detail_view, height=1, fg_color="#1e1e40"); self._ss_sep_top.pack(fill="x", padx=16, pady=14)
        self._screenshots_outer = ctk.CTkFrame(self.detail_view, fg_color="transparent"); self._screenshots_outer.pack(fill="x", padx=16)
        threading.Thread(target=self._load_screenshots, args=(app,), daemon=True).start()
        self._about_btn = ctk.CTkButton(self.detail_view, text="  About this App    ▼", height=50, corner_radius=0, fg_color="transparent", font=ctk.CTkFont(size=14, weight="bold"), anchor="w", command=lambda: self._toggle_readme(app))
        self._about_btn.pack(fill="x", padx=4); self._readme_frame = ctk.CTkFrame(self.detail_view, fg_color="#0b0b1a", corner_radius=10)
        self._reviews_outer = ctk.CTkFrame(self.detail_view, fg_color="transparent"); self._reviews_outer.pack(fill="x", padx=16, pady=(16, 20))
        ctk.CTkLabel(self._reviews_outer, text="Reviews", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
        self._reviews_list = ctk.CTkFrame(self._reviews_outer, fg_color="transparent"); self._reviews_list.pack(fill="x")
        ctk.CTkButton(self._reviews_outer, text="✎ Write a Review", command=lambda: self._show_write_review_dialog(app)).pack(anchor="w", pady=(10, 0))
        threading.Thread(target=self._load_reviews, args=(app,), daemon=True).start()

    def _load_detail_icon(self, u):
        try:
            if u in self._icon_cache: p = self._icon_cache[u]
            elif u.startswith("http"): r = requests.get(u, timeout=5); p = _round_sq_crop(Image.open(BytesIO(r.content)), 82); self._icon_cache[u] = p
            else: p = _round_sq_crop(Image.open(u), 82); self._icon_cache[u] = p
            img = ctk.CTkImage(light_image=p, dark_image=p, size=(82, 82)); self._ctk_img_refs[u] = img
            self.after(0, lambda: self._detail_icon_lbl.configure(image=img) if self._detail_icon_lbl.winfo_exists() else None)
        except: pass
    def _load_screenshots(self, app):
        fn, name, subdir = app.get("full_name"), app.get("name"), app.get("subdir")
        urls = self.api.get_readme_images(fn)
        path = os.path.join(INSTALL_BASE, name)
        if os.path.exists(path):
            try:
                for f in os.listdir(path):
                    if f.lower().startswith("screenshot") and f.lower().endswith((".png", ".jpg", ".jpeg")): urls.append(os.path.join(path, f))
            except: pass
        if ":" in fn:
            try:
                r = requests.get(f"{GITHUB_API_BASE}/repos/{fn.split(':')[0]}/contents/{subdir}", headers=self.api.headers, timeout=5)
                if r.status_code == 200:
                    for i in r.json():
                        if i.get("name").lower().startswith("screenshot"): urls.append(i.get("download_url"))
            except: pass
        seen, unique = set(), []
        for u in urls:
            if u not in seen: unique.append(u); seen.add(u)
        self.after(0, lambda: self._render_screenshots(unique))
    def _render_screenshots(self, urls):
        if not urls: self._screenshots_outer.pack_forget(); self._ss_sep_top.pack_forget(); return
        scroll = ctk.CTkScrollableFrame(self._screenshots_outer, height=210, orientation="horizontal", fg_color="transparent"); scroll.pack(fill="x", pady=4)
        for u in urls[:8]: threading.Thread(target=self._load_one_screenshot, args=(scroll, u), daemon=True).start()
    def _load_one_screenshot(self, parent, u):
        try:
            if u.startswith("http"): r = requests.get(u, timeout=10); img = Image.open(BytesIO(r.content))
            else: img = Image.open(u)
            h = 190; w = int(img.width * h / img.height); img = img.resize((w, h), Image.Resampling.LANCZOS); ci = ctk.CTkImage(light_image=img, dark_image=img, size=(w, h))
            self.after(0, lambda: (self._ss_refs.append(ci), ctk.CTkLabel(ctk.CTkFrame(parent, fg_color="#1a1a2e", corner_radius=10), image=ci, text="").pack(padx=4, pady=4)))
        except: pass

    def _load_reviews(self, app):
        fn, name = app.get("full_name"), app.get("name"); repo = fn.split(":")[0] if ":" in fn else fn
        try:
            r = requests.get(f"{GITHUB_API_BASE}/repos/{repo}/issues?state=open&per_page=100", headers=self.api.headers, timeout=10)
            if r.status_code == 200:
                revs = []
                for i in r.json():
                    if i.get("title") == name:
                        b = i.get("body", ""); rm = re.search(r"Ratings:\s*([1-5])", b, re.I); cm = re.search(r"Comment:\s*(.*)", b, re.I|re.S)
                        revs.append({"id": i["number"], "login": i["user"]["login"], "user": re.search(r"User:\s*(.*)", b, re.I).group(1).strip() if re.search(r"User:\s*(.*)", b, re.I) else i["user"]["login"], "rating": int(rm.group(1)) if rm else 0, "comment": cm.group(1).strip() if cm else b.strip(), "avatar": i["user"]["avatar_url"]})
                self.after(0, lambda: self._render_reviews(revs, app))
        except: pass
    def _render_reviews(self, revs, app):
        for w in self._reviews_list.winfo_children(): w.destroy()
        if not revs: ctk.CTkLabel(self._reviews_list, text="No reviews yet.", text_color="#555").pack(pady=10); return
        my = self.config_db.get_username()
        for r in revs:
            card = ctk.CTkFrame(self._reviews_list, fg_color="#12122a", corner_radius=8); card.pack(fill="x", pady=5)
            h = ctk.CTkFrame(card, fg_color="transparent"); h.pack(fill="x", padx=10, pady=(10, 5))
            ctk.CTkLabel(h, text=r["user"], font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
            ctk.CTkLabel(h, text="★"*r["rating"]+"☆"*(5-r["rating"]), text_color="#ffb400").pack(side="right")
            ctk.CTkLabel(card, text=r["comment"], font=ctk.CTkFont(size=12), text_color="#9aa0a6", justify="left", wraplength=480).pack(fill="x", padx=10, pady=(0,10))

    def _toggle_readme(self, app):
        if self._detail_readme_visible: self._readme_frame.pack_forget(); self._about_btn.configure(text="  About this App    ▼"); self._detail_readme_visible = False
        else:
            self._about_btn.configure(text="  About this App    ▲"); self._readme_frame.pack(fill="x", padx=16, pady=(0, 20), after=self._about_btn); self._detail_readme_visible = True
            if not self._readme_loaded: self._readme_loaded = True; threading.Thread(target=self._fetch_readme, args=(app,), daemon=True).start()
    def _fetch_readme(self, app):
        p, txt = app.get("readme_path"), None
        if p and os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f: txt = f.read()
            except: pass
        if not txt: txt = self.api.get_readme(app.get("full_name")) or "No README found."
        self.after(0, lambda: self._show_readme(txt))
    def _show_readme(self, text):
        for w in self._readme_frame.winfo_children(): w.destroy()
        tb = ctk.CTkTextbox(self._readme_frame, height=340, font=ctk.CTkFont(size=11), fg_color="#0b0b1a", wrap="word", border_width=0); tb.pack(fill="both", padx=10, pady=10)
        tb.tag_config("h1", font=ctk.CTkFont(size=18, weight="bold"), foreground="#7eb3ff")
        tb.tag_config("h2", font=ctk.CTkFont(size=16, weight="bold"), foreground="#7eb3ff")
        tb.tag_config("bold", font=ctk.CTkFont(weight="bold"))
        lines = text.split("\n")
        for l in lines:
            if l.startswith("# "): tb.insert("end", l[2:]+"\n", "h1")
            elif l.startswith("## "): tb.insert("end", l[3:]+"\n", "h2")
            else:
                parts = re.split(r"(\*\*.*?\*\*)", l)
                for p in parts:
                    if p.startswith("**") and p.endswith("**"): tb.insert("end", p[2:-2], "bold")
                    else: tb.insert("end", p)
                tb.insert("end", "\n")
        tb.configure(state="disabled")

    def _show_notification(self, title, message):
        note = ctk.CTkFrame(self, fg_color="#1a73e8", corner_radius=10); note.place(relx=0.5, rely=0.1, anchor="n")
        ctk.CTkLabel(note, text=f"◈ {title}: {message}", font=ctk.CTkFont(size=13, weight="bold"), text_color="white").pack(padx=20, pady=10)
        self.after(4000, lambda: note.destroy() if note.winfo_exists() else None)
    def _primary_action(self, app, action):
        if action == "Install": self._install(app)
        elif action == "Update": self._install(app, is_update=True)
        elif action == "Launch": self._launch(app)
    def _install(self, app, is_update=False):
        fn = app.get("full_name")
        if any(h["full_name"] == fn and h["status"] in ["pending", "downloading", "installing"] for h in self._download_history): return
        task = {"full_name": fn, "app": app, "status": "pending", "progress": 0, "cancelled": False, "paused": False, "proc": None}
        self._download_history.append(task); self._download_queue.append(task); self.show_downloads()
        with self._queue_lock:
            if not self._queue_worker_running: self._queue_worker_running = True; threading.Thread(target=self._queue_worker, daemon=True).start()
    def _queue_worker(self):
        self._current_task = None
        while True:
            with self._queue_lock:
                if not self._download_queue: self._queue_worker_running = False; break
                self._current_task = self._download_queue.pop(0)
            if self._current_task["cancelled"]: continue
            self._run_task(self._current_task); self.after(0, self._apply_filter)
    def _run_task(self, task):
        app, fn = task["app"], task["full_name"]; name, url, sub, pushed = app.get("name"), app.get("html_url"), app.get("subdir"), app.get("pushed_at")
        path = os.path.join(INSTALL_BASE, name)
        try:
            task["status"] = "downloading"; self.after(0, self._apply_filter)
            if os.path.exists(path): subprocess.run(["rm", "-rf", path])
            tmp = os.path.expanduser(f"~/.appstore_tmp_{name}")
            if os.path.exists(tmp): subprocess.run(["rm", "-rf", tmp])
            os.makedirs(tmp)
            task["proc"] = subprocess.Popen(["git", "clone", "--no-checkout", "--depth", "1", "--filter=blob:none", url, tmp], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            task["proc"].wait()
            if task["cancelled"]: raise Exception("Cancelled")
            task["progress"] = 0.5; self.after(0, self._apply_filter)
            subprocess.run(["git", "sparse-checkout", "set", sub], cwd=tmp, check=True)
            subprocess.run(["git", "checkout"], cwd=tmp, check=True)
            src = os.path.join(tmp, sub)
            task["status"] = "installing"; task["progress"] = 0.8; self.after(0, self._apply_filter)
            subprocess.run(["mv", src, path], check=True); subprocess.run(["rm", "-rf", tmp])
            if os.path.exists(os.path.join(path, "install.sh")): subprocess.run(["bash", "install.sh"], cwd=path, check=True)
            self.db.add(fn, name, path, pushed, app_data=app); task["status"] = "completed"; task["progress"] = 1.0
            self.after(0, lambda: self._show_notification("Installed", f"{name} is ready!"))
        except Exception as e: task["status"] = "error"; task["error_msg"] = str(e); self.after(0, self._apply_filter)

    def _launch(self, app):
        p = os.path.join(INSTALL_BASE, app.get("name"), "launch.sh")
        if os.path.exists(p): subprocess.Popen(["bash", "launch.sh"], cwd=os.path.dirname(p))
        else: tk.messagebox.showinfo("Launch", f"No launch.sh found in {os.path.dirname(p)}")
    def _uninstall(self, app):
        fn, name = app.get("full_name"), app.get("name")
        if not tk.messagebox.askyesno("Uninstall", f"Remove {name}?"): return
        try:
            p = os.path.join(INSTALL_BASE, name)
            if os.path.exists(os.path.join(p, "uninstall.sh")): subprocess.run(["bash", "uninstall.sh"], cwd=p)
            subprocess.run(["rm", "-rf", p], check=True); self.db.remove(fn); self.show_detail(app)
        except Exception as e: tk.messagebox.showerror("Error", str(e))
    def _login(self):
        if self.api.token: self._show_profile_dialog()
        else:
            cid = self.config_db.get_client_id()
            if not cid: self._show_client_id_dialog()
            else: self._show_login_dialog(cid)
    def _show_client_id_dialog(self):
        win = ctk.CTkToplevel(self); win.title("GitHub Setup"); win.geometry("420x300"); win.configure(fg_color="#0f0f1a"); win.transient(self)
        ctk.CTkLabel(win, text="Enter your GitHub OAuth Client ID", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(25, 10))
        entry = ctk.CTkEntry(win, width=280, placeholder_text="Paste Client ID here..."); entry.pack(pady=5)
        def save():
            c = entry.get().strip()
            if c: self.config_db.set_client_id(c); win.destroy(); self._show_login_dialog(c)
        ctk.CTkButton(win, text="Save & Continue", width=160, height=36, corner_radius=18, command=save).pack(pady=20)
    def _show_profile_dialog(self):
        win = ctk.CTkToplevel(self); win.title("GitHub Profile"); win.geometry("300x200"); win.configure(fg_color="#0f0f1a"); win.transient(self)
        ctk.CTkLabel(win, text=f"Logged in as:", text_color="#9aa0a6").pack(pady=(30, 0))
        ctk.CTkLabel(win, text=self.config_db.get_username(), font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 20))
        def logout(): self.api.set_token(None); self.config_db.clear_token(); self._profile_btn.configure(text_color="#e8eaed"); win.destroy()
        ctk.CTkButton(win, text="Logout", fg_color="#4a1a1a", command=logout).pack(pady=10)
    def _show_login_dialog(self, cid):
        d = self.api.start_device_flow(cid)
        if not d: tk.messagebox.showerror("Error", "Check Client ID"); return
        win = ctk.CTkToplevel(self); win.title("GitHub Login"); win.geometry("460x380"); win.configure(fg_color="#0f0f1a"); win.transient(self)
        ctk.CTkLabel(win, text=d["user_code"], font=ctk.CTkFont(size=36, weight="bold"), text_color="#7eb3ff").pack(pady=40)
        ctk.CTkButton(win, text="Open Browser", command=lambda: webbrowser.open(d["verification_uri"])).pack(pady=20)
        def wait():
            t = self.api.poll_for_token(cid, d["device_code"], d["interval"])
            if t:
                self.api.set_token(t); self.config_db.set_token(t); u = self.api.get_current_user()
                if u: self.config_db.set_username(u.get("login")); self.config_db.set_display_name(u.get("name") or u.get("login"))
                self.after(0, lambda: (win.destroy(), self._profile_btn.configure(text_color="#34a853")))
        threading.Thread(target=wait, daemon=True).start()

if __name__ == "__main__":
    app = AppStoreApp()
    app.mainloop()