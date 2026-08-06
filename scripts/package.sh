#!/bin/sh

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
addon_file="$project_dir/addon.xml"
output_dir=${1:-"${HOME:?}/Downloads"}

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

mkdir -p "$temporary_dir/$addon_id"
tar -C "$project_dir" \
	--exclude='./.git' \
	--exclude='./.agents' \
	--exclude='./.codex' \
	--exclude='./.gitignore' \
	--exclude='./.idea' \
	--exclude='./build' \
	--exclude='./dist' \
	--exclude='./scripts' \
	--exclude='./shortcuts' \
	--exclude='./playlists' \
	--exclude='./extras/categories' \
	--exclude='./extras/media/pumpkin' \
	--exclude='./extras/media/snow' \
	--exclude='./extras/skinthemes' \
	--exclude='./extras/viewthumbs' \
	--exclude='./extras/widgetplaylists' \
	-cf - . | tar -C "$temporary_dir/$addon_id" -xf -

(
	cd "$temporary_dir"
	zip -q -r "$package_name" "$addon_id"
)

mv -f -- "$temporary_dir/$package_name" "$package_path"
printf '%s\n' "Created $package_path"
