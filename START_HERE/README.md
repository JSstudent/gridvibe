# Start GridVibe

Windows users can launch GridVibe from here:

```powershell
.\START_HERE\Start GridVibe.bat
```

## Downloaded the release ZIP?

Then this folder is your entry point. Extract the archive somewhere you can write to, open this folder, and run `Start GridVibe.bat`. You need **Python 3.10+** installed; the launcher does everything else — it creates the `.venv`, installs dependencies, offers the optional voice packages, and asks whether to start in Desktop or Browser mode.

One difference from a `git clone`: a copy extracted from a ZIP has no `.git` directory, so the launcher's **Check for updates** cannot fast-forward it. It will tell you to download the next release from the [Releases page](https://github.com/JSstudent/gridvibe/releases) instead. Clone the repository if you want in-app updates.

This file is only a visible shortcut for GitHub releases. The real launcher remains at:

```powershell
.\GridVibe.bat
```

GitHub controls repository file icons by file type, so a batch file cannot have a custom icon in the file list. Keeping this `START_HERE` folder near the top of the repository makes the Windows launcher easier to find without duplicating launcher logic.
