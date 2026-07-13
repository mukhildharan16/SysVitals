# SysVitals Desktop

This is a standalone Tauri desktop GUI. It embeds the existing `frontend/`
HTML, CSS, and JavaScript, then fetches the configured SysVitals API through a
small Rust HTTP bridge. The bridge avoids the CORS restriction a local webview
would otherwise have when calling the deployed API and forwards the access
token received during login.

## Run or build on Windows

Install the Rust toolchain and Microsoft C++ build tools, then install the
Tauri CLI:

```powershell
winget install Rustlang.Rustup
cargo install tauri-cli --version "^2.0.0" --locked
```

Restart PowerShell after installing Rust, then run the GUI from this directory:

```powershell
cd desktop\src-tauri
cargo tauri dev
```

Create an installer with:

```powershell
cargo tauri build
```

On the login screen, enter only the public SysVitals base URL, such as
`https://status.example.com`, then sign in normally. Do not enter a full
`/api/...` endpoint; the app adds API paths itself. The base URL and access
token are stored only in the desktop app's local storage.
