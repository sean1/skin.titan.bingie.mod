# BINGIE Lite

A self-contained Movies and TV Shows experience for Kodi Omega, based on Titan BINGIE MOD. BINGIE now includes the browsing, playback, Real-Debrid, subtitle, source-selection, and trailer features that previously came from POV Lite.

## Installation

Build BINGIE Lite from this source tree, then install the generated ZIP through Kodi's **Add-ons > Install from zip file** menu:

```sh
./scripts/package.sh
```

The package is written to `dist/skin.titan.bingie.lite-<version>.zip`. It contains one installable Kodi add-on and does not require POV Lite, YouTube, or separately installed Python module add-ons.
The build also creates a local Git tag named `v<version>` at the current commit. Commit changes to packaged files first so the tag matches the source in the ZIP; rebuilding reuses an existing tag only when it points to the same commit. Private credentials included in the ZIP are not tracked by the tag. The tag is not pushed automatically.
Streaming still requires network access and a configured Real-Debrid account.

Join the official thread for updates: https://forum.kodi.tv/showthread.php?tid=355993

Special thanks to @marcelveldt and @cartman.dos for their work on Titan and the original Titan BINGIE skin and add-ons, to kodifitzwell for POV Lite, and to everyone else who has contributed.

### Sports IPTV accounts

Configure Xtream-compatible accounts in `resources/private/livetv-account.json`. The existing single-account format remains supported; to add accounts, use this format:

```json
{
  "accounts": [
    {"id": "primary", "server": "https://provider.example", "username": "your-username", "password": "your-password", "enabled": true},
    {"id": "backup_1", "server": "https://backup.example", "username": "your-username", "password": "your-password", "enabled": false}
  ]
}
```

Use a unique, stable ID containing letters, numbers, underscores or hyphens. The `primary` account is used for ordinary Live TV; enabled accounts contribute sports feeds. Set `enabled` to `true` once an account is configured. Use the provider's server base URL, without a playlist path. Keep this file private with permissions `0600`: it contains passwords. The package script includes this entire file, including all configured account credentials. Treat the resulting ZIP as private.
