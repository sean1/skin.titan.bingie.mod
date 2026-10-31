#!/bin/sh

set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
addon_file="$project_dir/addon.xml"
output_dir=${1:-"$project_dir/dist"}
subtitle_credentials_rel="resources/private/subtitle_providers.json"
subtitle_credentials="$project_dir/$subtitle_credentials_rel"
livetv_credentials_rel="resources/private/livetv-account.json"
livetv_credentials="$project_dir/$livetv_credentials_rel"
football_key_rel="resources/private/api-football-key.txt"
sportapi_key_rel="resources/private/sportapi-key.txt"

addon_id=$(sed -n 's/^[[:space:]]*<addon[[:space:]][^>]*id="\([^"]*\)".*/\1/p' "$addon_file" | head -n 1)
addon_version=$(sed -n 's/^[[:space:]]*<addon[[:space:]][^>]*version="\([^"]*\)".*/\1/p' "$addon_file" | head -n 1)

if [ -z "$addon_id" ] || [ -z "$addon_version" ]; then
	printf '%s\n' "Unable to read the add-on ID or version from $addon_file" >&2
	exit 1
fi

case "$addon_id$addon_version" in
	*/* | *\\*)
		printf '%s\n' "The add-on ID and version must not contain path separators" >&2
		exit 1
		;;
esac

mkdir -p "$output_dir"
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)
package_name="$addon_id-$addon_version.zip"
package_path="$output_dir/$package_name"
temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/bingie-lite-package.XXXXXX")
trap 'rm -rf -- "$temporary_dir"' EXIT HUP INT TERM
tracked_manifest="$temporary_dir/tracked-files"
untracked_manifest="$temporary_dir/untracked-files"

git -C "$project_dir" ls-files -z -- . ':(exclude).gitignore' ':(exclude)scripts/**' ':(exclude)tests/**' > "$tracked_manifest"
git -C "$project_dir" ls-files --others --exclude-standard -z -- . ':(exclude)scripts/**' ':(exclude)tests/**' > "$untracked_manifest"
if [ -s "$untracked_manifest" ]; then
	printf '%s\n' 'Untracked package files must be added to Git before building:' >&2
	tr '\000' '\n' < "$untracked_manifest" >&2
	exit 1
fi

mkdir -p "$temporary_dir/$addon_id"
tar -C "$project_dir" \
	--null \
	--verbatim-files-from \
	--files-from="$tracked_manifest" \
	-cf - | tar -C "$temporary_dir/$addon_id" -xf -

if [ ! -e "$subtitle_credentials" ] && [ ! -L "$subtitle_credentials" ]; then
	printf '%s\n' "Subtitle credential file is required to build a complete package: $subtitle_credentials_rel" >&2
	exit 1
fi
if [ -e "$subtitle_credentials" ] || [ -L "$subtitle_credentials" ]; then
	if [ -L "$subtitle_credentials" ] || [ ! -f "$subtitle_credentials" ]; then
		printf '%s\n' "Subtitle credential file must be a regular file, not a symlink" >&2
		exit 1
	fi
	if [ "$(stat -c '%a' "$subtitle_credentials")" != "600" ]; then
		printf '%s\n' "Subtitle credential file permissions must be 0600" >&2
		exit 1
	fi
	credential_size=$(wc -c < "$subtitle_credentials")
	if [ "$credential_size" -lt 2 ] || [ "$credential_size" -gt 16384 ]; then
		printf '%s\n' "Subtitle credential file has an invalid size" >&2
		exit 1
	fi
	if ! python3 - "$subtitle_credentials" <<'PY'
import json
import sys

try:
	with open(sys.argv[1], encoding='utf-8-sig') as source:
		config = json.load(source)
	valid = isinstance(config, dict)
	for provider in ('opensubtitles', 'subdl', 'subsource'):
		entry = config.get(provider) if valid else None
		fields = ('api_key', 'user_agent') if provider == 'opensubtitles' else ('api_key',)
		valid = valid and isinstance(entry, dict) and all(isinstance(entry.get(field), str) and entry[field].strip() and not entry[field].startswith('YOUR_') for field in fields)
except (OSError, ValueError, UnicodeError):
	valid = False
sys.exit(0 if valid else 1)
PY
	then
		printf '%s\n' "Subtitle credential file must configure all provider API keys and the OpenSubtitles user agent" >&2
		exit 1
	fi
	mkdir -p "$temporary_dir/$addon_id/resources/private"
	install -m 600 "$subtitle_credentials" "$temporary_dir/$addon_id/$subtitle_credentials_rel"
fi

if [ ! -e "$livetv_credentials" ] && [ ! -L "$livetv_credentials" ]; then
	printf '%s\n' "Live TV account file is required to build a complete package: $livetv_credentials_rel" >&2
	exit 1
fi
if [ -e "$livetv_credentials" ] || [ -L "$livetv_credentials" ]; then
	if [ -L "$livetv_credentials" ] || [ ! -f "$livetv_credentials" ]; then
		printf '%s\n' "Live TV account file must be a regular file, not a symlink" >&2
		exit 1
	fi
	if [ "$(stat -c '%a' "$livetv_credentials")" != "600" ]; then
		printf '%s\n' "Live TV account file permissions must be 0600" >&2
		exit 1
	fi
	credential_size=$(wc -c < "$livetv_credentials")
	if [ "$credential_size" -lt 2 ] || [ "$credential_size" -gt 1048576 ]; then
		printf '%s\n' "Live TV account file has an invalid size" >&2
		exit 1
	fi
	mkdir -p "$temporary_dir/$addon_id/resources/private"
	if ! python3 - "$livetv_credentials" <<'PY'
import json
import sys

try:
	with open(sys.argv[1], encoding='utf-8') as account_file:
		account = json.load(account_file)
	if isinstance(account, dict) and isinstance(account.get('accounts'), list):
		account = next((entry for entry in account['accounts'] if isinstance(entry, dict) and entry.get('id') == 'primary' and entry.get('enabled', True) is True), None)
	valid = isinstance(account, dict) and all(isinstance(account.get(key), str) and account[key].strip() for key in ('server', 'username', 'password'))
except (OSError, ValueError, UnicodeError):
	valid = False
sys.exit(0 if valid else 1)
PY
	then
		printf '%s\n' "Live TV account file must contain valid server, username, and password strings" >&2
		exit 1
	fi
	install -m 600 "$livetv_credentials" "$temporary_dir/$addon_id/$livetv_credentials_rel"
fi

for key_rel in "$football_key_rel" "$sportapi_key_rel"; do
	key_file="$project_dir/$key_rel"
	if [ ! -e "$key_file" ] && [ ! -L "$key_file" ]; then
		printf '%s\n' "Key file is required to build a complete package: $key_rel" >&2
		exit 1
	fi
	if [ -L "$key_file" ] || [ ! -f "$key_file" ]; then
		printf '%s\n' "Key file must be a regular file, not a symlink: $key_rel" >&2
		exit 1
	fi
	if [ "$(stat -c '%a' "$key_file")" != "600" ]; then
		printf '%s\n' "Key file permissions must be 0600: $key_rel" >&2
		exit 1
	fi
	credential_size=$(wc -c < "$key_file")
	if [ "$credential_size" -lt 8 ] || [ "$credential_size" -gt 257 ]; then
		printf '%s\n' "Key file has an invalid size: $key_rel" >&2
		exit 1
	fi
	if ! python3 - "$key_file" <<'PY'
import re
import sys

try:
	with open(sys.argv[1], encoding='utf-8') as key_file:
		key = key_file.read().strip()
	valid = bool(re.fullmatch(r'[A-Za-z0-9_-]{8,256}', key))
except (OSError, UnicodeError, ValueError):
	valid = False
sys.exit(0 if valid else 1)
PY
	then
		printf '%s\n' "Key file must contain a valid token: $key_rel" >&2
		exit 1
	fi
	mkdir -p "$temporary_dir/$addon_id/resources/private"
	install -m 600 "$key_file" "$temporary_dir/$addon_id/$key_rel"
done

(
	cd "$temporary_dir"
	zip -q -r "$package_name" "$addon_id"
)

mv -f -- "$temporary_dir/$package_name" "$package_path"
printf '%s\n' "Created $package_path"
