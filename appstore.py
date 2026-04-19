import tkinter as tk
import customtkinter as ctk
import requests
import json
import threading
import webbrowser
import time
import re
import os
import subprocess
from PIL import Image, ImageTk
from io import BytesIO

# --- Configuration ---
SEARCH_QUERY = "topic:termux-desktop+OR+topic:termux-x11"
GITHUB_API_BASE = "https://api.github.com"
CLIENT_ID = "Iv1.b08f870e6c6c180a" # Placeholder
VERIFIED_REPOS_URL = "https://raw.githubusercontent.com/ilickft/AppStore/refs/heads/main/repos.txt"
APPSTORE_REPO_URL = "https://github.com/ilickft/AppStore/"

class GitHubAPI:
    def __init__(self):
        self.token = None
        self.verified_repos = []
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Termux-AppStore"
        }

    def set_token(self, token):
        self.token = token
        self.headers["Authorization"] = f"token {token}"

    def fetch_verified_repos(self):
        try:
            response = requests.get(VERIFIED_REPOS_URL, timeout=5)
            if response.status_code == 200:
                self.verified_repos = [line.strip() for line in response.text.splitlines() if line.strip()]
            return self.verified_repos
        except:
            return []

    def search_apps(self, query=SEARCH_QUERY):
        apps = []
        # 1. Get apps from verified list first to ensure they are present
        for repo_name in self.verified_repos:
            details = self.get_repo_details(repo_name)
            if details:
                apps.append(details)

        # 2. Search by topic
        url = f"{GITHUB_API_BASE}/search/repositories?q={query}"
        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                items = response.json().get("items", [])
                # Avoid duplicates
                existing_names = [a["full_name"] for a in apps]
                for item in items:
                    if item["full_name"] not in existing_names:
                        apps.append(item)
        except:
            pass
        return apps

    def get_repo_details(self, full_name):
        url = f"{GITHUB_API_BASE}/repos/{full_name}"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            return response.json()
        return None

    def get_readme_images(self, full_name):
        url = f"{GITHUB_API_BASE}/repos/{full_name}/readme"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            readme_data = response.json()
            readme_url = readme_data.get("download_url")
            if readme_url:
                readme_text = requests.get(readme_url).text
                # Simple regex to find markdown images: ![alt](url)
                images = re.findall(r'!\[.*?\]\((.*?)\)', readme_text)
                return images
        return []

    def get_reviews(self, full_name):
        # We use issues as reviews
        url = f"{GITHUB_API_BASE}/repos/{full_name}/issues?state=all"
        response = requests.get(url, headers=self.headers)
        if response.status_code == 200:
            return response.json()
        return []

    def create_review(self, full_name, title, body):
        if not self.token:
            return False
        url = f"{GITHUB_API_BASE}/repos/{full_name}/issues"
        data = {"title": title, "body": body}
        response = requests.post(url, headers=self.headers, json=data)
        return response.status_code == 201

    def start_device_flow(self):
        url = "https://github.com/login/device/code"
        data = {"client_id": CLIENT_ID, "scope": "public_repo"}
        response = requests.post(url, data=data, headers={"Accept": "application/json"})
        if response.status_code == 200:
            return response.json()
        return None

    def poll_for_token(self, device_code, interval):
        url = "https://github.com/login/oauth/access_token"
        data = {
            "client_id": CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
        }
        while True:
            response = requests.post(url, data=data, headers={"Accept": "application/json"})
            res_data = response.json()
            if "access_token" in res_data:
                return res_data["access_token"]
            if "error" in res_data:
                if res_data["error"] != "authorization_pending":
                    return None
            time.sleep(interval)

class Installer:
    def __init__(self, main_app):
        self.main_app = main_app

    def install(self, app_data):
        repo_url = app_data["html_url"]
        app_name = app_data["name"]
        
        # Create a progress window
        progress_win = ctk.CTkToplevel(self.main_app)
        progress_win.title(f"Installing {app_name}")
        progress_win.geometry("600x400")
        
        log_box = ctk.CTkTextbox(progress_win, width=580, height=340)
        log_box.pack(padx=10, pady=10)

        def run_install():
            try:
                log_box.insert("end", f"Cloning {repo_url}...\n")
                tmp_dir = f"/data/data/com.termux/files/home/.gemini/tmp/appstore_install_{app_name}"
                subprocess.run(["rm", "-rf", tmp_dir], check=True)
                
                # Clone the repo
                result = subprocess.run(["git", "clone", "--depth", "1", repo_url, tmp_dir], capture_output=True, text=True)
                log_box.insert("end", result.stdout + result.stderr + "\n")
                
                if result.returncode != 0:
                    log_box.insert("end", "Error: Failed to clone repository.\n")
                    return

                install_script = os.path.join(tmp_dir, "install.sh")
                if os.path.exists(install_script):
                    log_box.insert("end", "Running install.sh...\n")
                    # Run install script
                    process = subprocess.Popen(["bash", "install.sh"], cwd=tmp_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    for line in process.stdout:
                        log_box.insert("end", line)
                        log_box.see("end")
                    process.wait()
                    if process.returncode == 0:
                        log_box.insert("end", "\nInstallation successful!\n")
                    else:
                        log_box.insert("end", f"\nInstallation failed with exit code {process.returncode}\n")
                else:
                    log_box.insert("end", "Error: No install.sh found in repository root.\n")
            except Exception as e:
                log_box.insert("end", f"Error: {str(e)}\n")

        threading.Thread(target=run_install, daemon=True).start()

class SelfUpdater:
    def __init__(self, main_app):
        self.main_app = main_app

    def update(self):
        progress_win = ctk.CTkToplevel(self.main_app)
        progress_win.title("Updating AppStore")
        progress_win.geometry("500x300")
        
        label = ctk.CTkLabel(progress_win, text="Updating AppStore...\nPlease wait.", font=ctk.CTkFont(size=16))
        label.pack(pady=40)

        def run_update():
            try:
                tmp_dir = "/data/data/com.termux/files/home/.gemini/tmp/appstore_self_update"
                subprocess.run(["rm", "-rf", tmp_dir], check=True)
                subprocess.run(["git", "clone", "--depth", "1", APPSTORE_REPO_URL, tmp_dir], check=True)
                
                install_script = os.path.join(tmp_dir, "install.sh")
                if os.path.exists(install_script):
                    subprocess.run(["bash", "install.sh"], cwd=tmp_dir, check=True)
                    self.main_app.after(0, lambda: self.finish_update(progress_win, True))
                else:
                    self.main_app.after(0, lambda: self.finish_update(progress_win, False, "No install.sh found"))
            except Exception as e:
                self.main_app.after(0, lambda: self.finish_update(progress_win, False, str(e)))

        threading.Thread(target=run_update, daemon=True).start()

    def finish_update(self, win, success, msg=""):
        win.destroy()
        if success:
            tk.messagebox.showinfo("Update Complete", "AppStore updated successfully! Please restart.")
        else:
            tk.messagebox.showerror("Update Failed", f"Update failed: {msg}")

class AppStoreApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Termux AppStore")
        self.geometry("800x600")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.api = GitHubAPI()
        self.installer = Installer(self)
        self.updater = SelfUpdater(self)
        self.loaded_apps = [] # To store the original list for filtering

        # UI Setup
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=160, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        
        self.logo_label = ctk.CTkLabel(self.sidebar, text="AppStore", font=ctk.CTkFont(size=18, weight="bold"))
        self.logo_label.pack(padx=10, pady=(20, 10))

        self.home_btn = ctk.CTkButton(self.sidebar, text="Home", height=32, command=self.show_home)
        self.home_btn.pack(padx=10, pady=5)

        self.login_btn = ctk.CTkButton(self.sidebar, text="Login", height=32, command=self.login_github)
        self.login_btn.pack(padx=10, pady=5)

        self.update_btn = ctk.CTkButton(self.sidebar, text="Update", height=32, command=self.updater.update, fg_color="green", hover_color="darkgreen")
        self.update_btn.pack(padx=10, pady=5)

        # Main Content Area
        self.content_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content_frame.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(1, weight=1)

        # Search Bar
        self.search_frame = ctk.CTkFrame(self.content_frame, fg_color="transparent")
        self.search_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(0, 5))
        
        self.search_entry = ctk.CTkEntry(self.search_frame, placeholder_text="Search...", height=30)
        self.search_entry.pack(fill="x", side="left", expand=True)
        self.search_entry.bind("<KeyRelease>", self.filter_apps)

        self.main_frame = ctk.CTkScrollableFrame(self.content_frame, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=1, column=0, sticky="nsew")

        self.show_home()

    def show_home(self):
        self.main_frame.grid(row=1, column=0, sticky="nsew")
        self.search_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=(0, 5)) # Re-show search
        if hasattr(self, 'details_frame'):
            self.details_frame.grid_forget()

        for widget in self.main_frame.winfo_children():
            widget.destroy()

        self.show_skeleton()
        threading.Thread(target=self.load_apps, daemon=True).start()

    def show_skeleton(self):
        # Create skeleton cards
        for i in range(9):
            row, col = divmod(i, 3)
            card = ctk.CTkFrame(self.main_frame, width=190, height=220, fg_color="#2b2b2b")
            card.grid(row=row, column=col, padx=5, pady=5)
            card.grid_propagate(False)

    def load_apps(self):
        self.api.fetch_verified_repos()
        apps = self.api.search_apps()
        self.loaded_apps = apps
        self.after(0, lambda: self.render_apps(apps))

    def filter_apps(self, event=None):
        query = self.search_entry.get().lower()
        filtered = [a for a in self.loaded_apps if query in a["name"].lower() or (a["description"] and query in a["description"].lower())]
        self.render_apps(filtered)

    def render_apps(self, apps):
        # Clear main frame again to be sure (in case of race conditions)
        for widget in self.main_frame.winfo_children():
            widget.destroy()

        row, col = 0, 0
        for app in apps:
            card = ctk.CTkFrame(self.main_frame, width=190, height=220)
            card.grid(row=row, column=col, padx=5, pady=5)
            card.grid_propagate(False)

            name_label = ctk.CTkLabel(card, text=app["name"], font=ctk.CTkFont(size=13, weight="bold"))
            name_label.pack(pady=(8, 4))

            desc = app["description"] if app["description"] else "No description"
            if len(desc) > 70: desc = desc[:67] + "..."
            desc_label = ctk.CTkLabel(card, text=desc, wraplength=170, font=ctk.CTkFont(size=11))
            desc_label.pack(padx=8, pady=4)

            view_btn = ctk.CTkButton(card, text="Details", height=28, command=lambda a=app: self.show_details(a))
            view_btn.pack(side="bottom", pady=8)

            col += 1
            if col > 2:
                col = 0
                row += 1

    def show_details(self, app):
        self.main_frame.grid_forget()
        self.search_frame.grid_forget() # Hide search in details
        
        if not hasattr(self, 'details_frame'):
            self.details_frame = ctk.CTkScrollableFrame(self.content_frame, corner_radius=0, fg_color="transparent")
        
        self.details_frame.grid(row=0, column=0, rowspan=2, sticky="nsew")
        for widget in self.details_frame.winfo_children():
            widget.destroy()

        # Unverified Warning
        if app["full_name"] not in self.api.verified_repos:
            warning_label = ctk.CTkLabel(self.details_frame, text="⚠ THIS IS NOT VERIFIED BY APPSTORE INSTALL AT YOUR OWN RISK", 
                                        text_color="white", fg_color="red", font=ctk.CTkFont(size=14, weight="bold"), height=40)
            warning_label.pack(fill="x", padx=20, pady=(10, 0))

        # Header
        header = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=20)
        
        name_label = ctk.CTkLabel(header, text=app["name"], font=ctk.CTkFont(size=24, weight="bold"))
        name_label.pack(side="left")
        
        install_btn = ctk.CTkButton(header, text="Install", command=lambda: self.installer.install(app))
        install_btn.pack(side="right")

        # Description
        desc_label = ctk.CTkLabel(self.details_frame, text=app["description"] or "No description", 
                                 wraplength=700, justify="left", font=ctk.CTkFont(size=14))
        desc_label.pack(padx=20, pady=10, anchor="w")

        # Screenshots Placeholder
        screenshot_label = ctk.CTkLabel(self.details_frame, text="Screenshots", font=ctk.CTkFont(size=18, weight="bold"))
        screenshot_label.pack(padx=20, pady=(20, 10), anchor="w")
        
        self.screenshot_frame = ctk.CTkFrame(self.details_frame, height=200, fg_color="transparent")
        self.screenshot_frame.pack(fill="x", padx=20)
        
        threading.Thread(target=self.load_screenshots, args=(app["full_name"],), daemon=True).start()

        # Reviews Section
        review_label = ctk.CTkLabel(self.details_frame, text="Reviews (Issues)", font=ctk.CTkFont(size=18, weight="bold"))
        review_label.pack(padx=20, pady=(30, 10), anchor="w")
        
        if self.api.token:
            self.add_review_ui(app["full_name"])
        else:
            login_hint = ctk.CTkLabel(self.details_frame, text="Login with GitHub to post a review.", font=ctk.CTkFont(slant="italic"))
            login_hint.pack(padx=20, pady=5, anchor="w")

        self.reviews_container = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        self.reviews_container.pack(fill="x", padx=20)
        
        threading.Thread(target=self.load_reviews, args=(app["full_name"],), daemon=True).start()

    def add_review_ui(self, full_name):
        post_frame = ctk.CTkFrame(self.details_frame)
        post_frame.pack(fill="x", padx=20, pady=10)
        
        ctk.CTkLabel(post_frame, text="Write a Review", font=ctk.CTkFont(weight="bold")).pack(padx=10, pady=5, anchor="w")
        
        title_entry = ctk.CTkEntry(post_frame, placeholder_text="Review Title")
        title_entry.pack(fill="x", padx=10, pady=5)
        
        body_entry = ctk.CTkTextbox(post_frame, height=100)
        body_entry.pack(fill="x", padx=10, pady=5)
        
        def submit():
            title = title_entry.get()
            body = body_entry.get("1.0", "end")
            if title and body.strip():
                success = self.api.create_review(full_name, title, body)
                if success:
                    print("Review posted!")
                    self.show_details(self.api.get_repo_details(full_name)) # Refresh
                else:
                    print("Failed to post review.")
        
        ctk.CTkButton(post_frame, text="Post Review", command=submit).pack(padx=10, pady=10, anchor="e")

    def load_screenshots(self, full_name):
        images = self.api.get_readme_images(full_name)
        self.after(0, lambda: self.render_screenshots(images))

    def render_screenshots(self, image_urls):
        if not image_urls:
            ctk.CTkLabel(self.screenshot_frame, text="No screenshots found in README").pack()
            return

        # Load first 3 images for now
        for url in image_urls[:3]:
            try:
                response = requests.get(url, timeout=5)
                img_data = response.content
                img = Image.open(BytesIO(img_data))
                # Resize keeping aspect ratio
                base_height = 180
                w_percent = (base_height / float(img.size[1]))
                w_size = int((float(img.size[0]) * float(w_percent)))
                img = img.resize((w_size, base_height), Image.Resampling.LANCZOS)
                
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(w_size, base_height))
                img_label = ctk.CTkLabel(self.screenshot_frame, image=ctk_img, text="")
                img_label.pack(side="left", padx=5)
            except Exception as e:
                print(f"Error loading image {url}: {e}")

    def load_reviews(self, full_name):
        issues = self.api.get_reviews(full_name)
        self.after(0, lambda: self.render_reviews(issues))

    def render_reviews(self, issues):
        if not issues:
            ctk.CTkLabel(self.reviews_container, text="No reviews yet.").pack(anchor="w")
            return
            
        for issue in issues[:10]: # Limit to 10
            rev_card = ctk.CTkFrame(self.reviews_container)
            rev_card.pack(fill="x", pady=5)
            
            user = issue["user"]["login"]
            title = issue["title"]
            body = issue["body"] if issue["body"] else ""
            if len(body) > 200: body = body[:197] + "..."
            
            title_label = ctk.CTkLabel(rev_card, text=f"{user}: {title}", font=ctk.CTkFont(size=13, weight="bold"))
            title_label.pack(padx=10, pady=(5, 0), anchor="w")
            
            body_label = ctk.CTkLabel(rev_card, text=body, wraplength=650, justify="left", font=ctk.CTkFont(size=12))
            body_label.pack(padx=10, pady=(0, 5), anchor="w")


    def login_github(self):
        data = self.api.start_device_flow()
        if data:
            user_code = data["user_code"]
            verification_uri = data["verification_uri"]
            interval = data["interval"]
            device_code = data["device_code"]

            # Show code to user
            login_win = ctk.CTkToplevel(self)
            login_win.title("GitHub Login")
            login_win.geometry("400x300")
            
            label = ctk.CTkLabel(login_win, text=f"Go to {verification_uri}\nand enter the code:", font=ctk.CTkFont(size=14))
            label.pack(pady=20)
            
            code_label = ctk.CTkLabel(login_win, text=user_code, font=ctk.CTkFont(size=24, weight="bold"))
            code_label.pack(pady=10)

            copy_btn = ctk.CTkButton(login_win, text="Open Browser", command=lambda: webbrowser.open(verification_uri))
            copy_btn.pack(pady=20)

            def wait_for_login():
                token = self.api.poll_for_token(device_code, interval)
                if token:
                    self.api.set_token(token)
                    self.after(0, lambda: self.on_login_success(login_win))
            
            threading.Thread(target=wait_for_login, daemon=True).start()

    def on_login_success(self, win):
        win.destroy()
        self.login_btn.configure(text="Logged In", state="disabled")
        print("Successfully logged in to GitHub")

if __name__ == "__main__":
    app = AppStoreApp()
    app.mainloop()
