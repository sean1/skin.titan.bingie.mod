#!/bin/sh

set -eu

umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
addon_file="$project_dir/addon.xml"
output_dir=${1:-"$project_dir/dist"}
subtitle_credentials_rel="resources/private/subtitle_providers.json"
subtitle_credentials="$project_dir/$subtitle_credentials_rel"

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
	python3 -m json.tool "$subtitle_credentials" >/dev/null
	mkdir -p "$temporary_dir/$addon_id/resources/private"
	install -m 600 "$subtitle_credentials" "$temporary_dir/$addon_id/$subtitle_credentials_rel"
fi

(
	cd "$temporary_dir"
	zip -q -r "$package_name" "$addon_id"
)

mv -f -- "$temporary_dir/$package_name" "$package_path"
printf '%s\n' "Created $package_path"
