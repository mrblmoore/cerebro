# Building the Windows installer

Most people should download `CerebroSetup.exe` from the
[releases page](https://github.com/mrblmoore/cerebro/releases) — it needs no
Python and no prerequisites. This page is for building it yourself.

## What gets built

```
dist/Cerebro/
├── Cerebro.exe          the API and dashboard
├── CerebroWidget.exe    the always-on-top widget
└── _internal/           bundled Python runtime, libraries, web UI, extension
dist/CerebroSetup.exe    the installer
```

Around 95 MB unpacked, ~35 MB as an installer. The Python runtime and every
dependency are inside — nothing is fetched at install time.

## Build it

```batch
packaging\build_windows.bat
```

That creates a build environment, installs the dependencies, runs PyInstaller
and then Inno Setup. Requires:

- **Python 3.9+** on PATH.
- **[Inno Setup 6](https://jrsoftware.org/isdl.php)** for the installer step. If
  it is missing, the application is still built and the script says so.

PyInstaller can only build for the platform it runs on, so a Windows installer
must be built on Windows. The `.github/workflows/build-windows.yml` workflow
does this on every push and attaches the installer to any semantic-version tag,
with or without a leading `v`.

The release tag must match the repository's `VERSION` file. For example, set
`VERSION` to `0.3.5` before creating either `v0.3.5` or `0.3.5`. This keeps the
API, dashboard, Windows metadata and installer label on the same version.

## What the installer does

- Installs to `%LOCALAPPDATA%\Programs\Cerebro` — **per-user, no admin rights**,
  which matters on a locked-down machine.
- Creates Start Menu entries, an optional desktop shortcut, and an optional
  startup entry for the widget.
- Keeps user data in `%LOCALAPPDATA%\Cerebro` — deliberately outside the install
  directory, so upgrading never touches your database.
- On uninstall, asks before deleting that data. Losing a case history to a
  routine uninstall would be a nasty surprise.

## Code signing

The build is unsigned, so SmartScreen will warn on first run. To sign it, add
these to `[Setup]` in `packaging/installer.iss` before building:

```
SignTool=signtool
SignedUninstaller=yes
```

and register a `signtool` command in the Inno Setup IDE, or pass
`/Ssigntool=...` to `ISCC`. Sign `dist\Cerebro\*.exe` before the installer step
so the bundled executables are signed too.

## Notes on the packaged build

- **Paths.** `app/core/paths.py` detects `sys.frozen` and switches the data
  directory to `%LOCALAPPDATA%\Cerebro`. The install directory is treated as
  read-only.
- **Configuration.** The packaged build reads and writes
  `%LOCALAPPDATA%\Cerebro\cerebro.env` rather than `backend/.env`.
- **Hidden imports.** Uvicorn's protocol modules, SQLAlchemy dialects and the
  document libraries are imported dynamically, so they are listed explicitly in
  `packaging/cerebro.spec`. Adding a dependency that is imported by name means
  adding it there.
- **The browser extension** is bundled into `_internal/browser-extension`, so an
  installed user can load it without cloning the repository.
