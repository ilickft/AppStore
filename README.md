# Termux AppStore

A modern, professional application hub for the **Termux XFCE4** desktop environment. Built with Python and CustomTkinter, the AppStore provides a seamless way to browse, install, and manage open-source tools and games.

![Screenshot1](https://github.com/ilickft/AppStore/raw/main/screenshots/screenshot1.png)
![Screenshot2](https://github.com/ilickft/AppStore/raw/main/screenshots/screenshot2.png)
![Screenshot1](https://github.com/ilickft/AppStore/raw/main/screenshots/screenshot3.png)

## 🚀 Key Features

- **Curated Repositories**: Directly integrates with the [Games](https://github.com/App-Store-tmx/Games) and [Apps](https://github.com/App-Store-tmx/Apps) stores.
- **Efficient Installation**: Uses `git sparse-checkout` to download only the necessary application files, saving time and bandwidth.
- **Persistent Background Tasks**: Download and install apps silently in the background while you continue browsing.
- **Downloads Manager**: A dedicated view (⬇) to track all active installations and progress in real-time.
- **Local Library**: View your "Installed" apps instantly with local metadata loading (icons and READMEs) for offline access.
- **Community Reviews**: Read and write reviews with a 5-star rating system, powered by GitHub Issues. Edit or delete your feedback at any time.
- **Modern UI**: Dark-themed design with rounded square icons and a fluid, optimized layout.
- **Update Checks**: Easily check for updates for individual applications with a single click.

## 📦 Installation

To install the AppStore in your Termux environment, run:

```bash
pkg install git
git clone https://github.com/ilickft/AppStore.git
cd AppStore
chmod +x install.sh
./install.sh
```
NOTE: if the installation crashes, try installing the requirements then
```
chmod +x install.sh
./install.sh
```
After installation, you can launch the **AppStore** from your XFCE4 application menu under **System** or **Settings**.

## 🛠️ How to list your App/Game

There are two ways to list your application in the AppStore:

Before that make sure you have the **Required Files** in your repo's root dir.
*   `readme.md`: Descriptive information and screenshots.
*   `icon.png`: A high-quality image (will be automatically cropped to a rounded square).
*   `1.0.0`: An empty file as version in file name
*   `install.sh`: A script to handle the installation logic.
*   `launch.sh`: A script to launch the application.
*   `uninstall.sh` (Optional): A script to clean up files during removal.

**Method 1: Tagging your repository (Recommended)**
Simply add one of the following topics/tags to your GitHub repository:
- `termux-desk-app` (for Apps)
- `termux-desk-game` (for Games)
- `termux-desk` or `termux-desk-tool` (for Public tools)

**Method 2: Submitting to the main store repositories**
The AppStore is also subdirectory-based. To add your project to the store manually:

1.  **Fork** the [Games](https://github.com/App-Store-tmx/Games) or [Apps](https://github.com/App-Store-tmx/Apps) repository.
2.  **Create a Folder** with your application's name.
3.  **Required Files** in your folder.
4.  **Submit a Pull Request** to the main store repositories.

## 🔑 Authentication

The AppStore uses **GitHub Device Flow** for secure authentication. You can browse and install apps freely, but logging in allows you to:
*   Write, edit, and delete reviews.
*   Reply to other users' feedback.

## 👤 Author

-   **Name:** KARIO
-   **GitHub:** [@ilickft](https://github.com/ilickft)


## 📝 Developer Note

This project is specifically optimized for Termux X11 and XFCE4. It prioritizes lightweight Python dependencies and native Termux paths to ensure stability and performance.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
