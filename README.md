# Termux AppStore

A modern, dark-themed AppStore for the Termux XFCE4 desktop environment. Built with Python and CustomTkinter.

## Features
- **Dynamic Discovery:** Searches GitHub for repositories with the topic `termux-appstore-ready`.
- **Rich Metadata:** Displays descriptions and extracts screenshots directly from project READMEs.
- **User Reviews:** Integrated with GitHub Issues; read and write reviews using your GitHub account.
- **Easy Installation:** One-click installation for apps that follow the standard `install.sh` pattern.

## Installation
Run the following commands in Termux:
```bash
chmod +x install.sh
./install.sh
```

## How to add your App
To make your open-source project appear in this AppStore:
1. Add the topic `termux-appstore-ready` to your GitHub repository.
2. Ensure you have a clear description and at least one screenshot in your `README.md` (using markdown image syntax `![alt](url)`).
3. Include an `install.sh` script in the root of your repository that handles the installation process for Termux.

## Developer Note
The AppStore uses GitHub Device Flow for authentication. You can browse and install apps without logging in, but you will need to log in to post reviews.
