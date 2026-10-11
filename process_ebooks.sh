#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
BACKUP_DIR="$SCRIPT_DIR/backup"
LOG_FILE="$SCRIPT_DIR/metadata_issues.log"
CALIBRE_LIB="$SCRIPT_DIR/.calibre_lib"

cleanup() { rm -rf "$CALIBRE_LIB" 2>/dev/null || true; }
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"
mkdir -p "$CALIBRE_LIB"
: > "$LOG_FILE"

log_issue() { printf '%s\n' "$1" >> "$LOG_FILE"; }

get_embedded_metadata() {
    local file="$1"
    local meta title author year pubdate
    meta=$(ebook-meta "$file" 2>/dev/null)
    title=$(printf '%s' "$meta" | grep "^Title" | grep -v "Title sort" | sed "s/^Title[^:]*: *//" | sed 's/[[:space:]]*$//')
    author=$(printf '%s' "$meta" | grep "^Author" | head -1 | sed "s/^Author[^:]*: *//" | sed 's/[[:space:]]*$//')
    pubdate=$(printf '%s' "$meta" | grep "^Published" | sed "s/^Published[^:]*: *//" | sed 's/[[:space:]]*$//')
    year=$(printf '%s' "$pubdate" | grep -oE "^19[0-9]{2}|^20[0-9]{2}" | head -1 || true)
    printf '%s\n%s\n%s\n' "$title" "$author" "$year"
}

fetch_online_metadata() {
    local title="$1"
    local author="$2"
    local timeout="${3:-10}"

    if [[ -z "$title" && -z "$author" ]]; then
        return 1
    fi

    local query_args=()
    if [[ -n "$title" ]]; then
        query_args+=(--title "$title")
    fi
    if [[ -n "$author" ]]; then
        query_args+=(--authors "$author")
    fi

    local result
    result=$(timeout 15 fetch-ebook-metadata "${query_args[@]}" --timeout "$timeout" --opf 2>/dev/null)
    if [[ -n "$result" ]]; then
        local fetched_title fetched_author fetched_year
        fetched_title=$(printf '%s' "$result" | grep -i "^<dc:title" | sed 's/.*<dc:title[^>]*>//;s/<\/dc:title>//' | head -1)
        fetched_author=$(printf '%s' "$result" | grep -i "^<dc:creator" | sed 's/.*<dc:creator[^>]*>//;s/<\/dc:creator>//' | head -1)
        fetched_year=$(printf '%s' "$result" | grep -i "^<dc:date" | sed 's/.*<dc:date[^>]*>//;s/<\/dc:date>//' | grep -oE "^20[0-9]{2}|^19[0-9]{2}" | head -1)
        printf '%s\n%s\n%s\n' "${fetched_title:-}" "${fetched_author:-}" "${fetched_year:-}"
        return 0
    fi
    return 1
}

parse_annas_archive() {
    local stem="$1"
    if [[ "$stem" != *"Anna's Archive"* ]]; then
        printf '%s\n\n\n' "$stem"
        return
    fi

    local title author year
    title=$(printf '%s' "$stem" | sed 's/ -- .*//' | sed 's/_/ /g' | sed 's/  */ /g' | sed 's/^ //;s/ $//')
    author=$(printf '%s' "$stem" | sed 's/.* -- //' | sed 's/ -- .*//' | sed 's/;.*$//' | sed 's/\[.*\]//' | sed 's/  */ /g' | sed 's/^ //;s/ $//')
    year=$(printf '%s' "$stem" | grep -oE "(19|20)[0-9]{2}" | grep -E "^(19|20)" | head -1)

    printf '%s\n%s\n%s\n' "$title" "$author" "${year:-}"
}

parse_simple() {
    local stem="$1"
    if [[ "$stem" =~ ^(.+)[[:space:]]+-[[:space:]]+(.+)$ ]]; then
        local part1="${BASH_REMATCH[1]}"
        local part2="${BASH_REMATCH[2]}"
        part2=$(printf '%s' "$part2" | sed -E 's/^(PDF|epub|mobi|azw3|ebook).*$//gi' | sed 's/  */ /g' | sed 's/^ //;s/ $//')
        if [[ -n "$part2" && ${#part2} -gt 1 ]]; then
            printf '%s\n%s\n\n' "$part1" "$part2"
            return
        fi
    fi
    printf '%s\n\n\n' "$stem"
}

is_valid_year() { [[ "$1" =~ ^(19|20)[0-9]{2}$ ]]; }

sanitize() { printf '%s' "$1" | sed 's/[<>:"/\\|*]/-/g' | sed 's/  */ /g' | sed 's/^ //;s/ $//'; }

uniquify_path() {
    local path="$1"
    while [[ -f "$path" ]]; do
        local dir base ext i
        dir=$(dirname -- "$path")
        ext=$(basename -- "$path" | sed 's/.*\.//')
        base=$(basename -- "$path" | sed 's/\.[^.]*$//')
        i=1
        while [[ -f "$dir/${base}_${i}.$ext" ]]; do ((i++)); done
        path="$dir/${base}_${i}.$ext"
    done
    printf '%s' "$path"
}

move_numbered_folders() {
    local dir="$1"
    local folders
    mapfile -d '' folders < <(find "$dir" -maxdepth 2 -type d -name "* ([0-9]*)" -print0 2>/dev/null)
    for folder in "${folders[@]}"; do
        [[ -z "$folder" ]] && continue
        echo "Moving from: $folder"
        find "$folder" -maxdepth 1 -type f ! -name "Thumbs.db" ! -name ".DS_Store" -print0 2>/dev/null | while IFS= read -r -d '' f; do
            mv -v -- "$f" "${folder%/*}/"
        done
        rmdir "$folder" 2>/dev/null || true
    done
}

process_book() {
    local src="$1"
    local dst_dir="$2"
    local ext="${src##*.}"
    ext=$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')
    local orig_name="$(basename -- "$src")"
    local stem="${orig_name%.$ext}"

    echo ""
    echo "=== $orig_name ==="

    IFS=$'\n' read -r emb_title < <(get_embedded_metadata "$src" | sed -n '1p')
    IFS=$'\n' read -r emb_author < <(get_embedded_metadata "$src" | sed -n '2p')
    IFS=$'\n' read -r emb_year < <(get_embedded_metadata "$src" | sed -n '3p')

    local check_title="$emb_title" check_author="$emb_author"
    if [[ -z "$check_title" || ${#check_title} -lt 3 || "$check_title" == "$orig_name" ]]; then
        if [[ "$stem" == *"Anna's Archive"* ]]; then
            IFS=$'\n' read -r check_title check_author _ < <(parse_annas_archive "$stem")
        else
            IFS=$'\n' read -r check_title check_author _ < <(parse_simple "$stem")
        fi
    fi
    [[ -z "$check_author" || "$check_author" =~ ^(Unknown|Unbekannt|N/A)$ || ${#check_author} -lt 2 ]] && check_author=""
    [[ -z "$check_title" || ${#check_title} -lt 3 ]] && check_title="$stem"
    check_title=$(sanitize "$check_title")
    check_author=$(sanitize "$check_author")

    if [[ -f "$dst_dir/${check_title} - ${check_author}.opf" ]]; then
        echo "Already processed, skipping: $orig_name"
        return 0
    fi

    mkdir -p "$BACKUP_DIR/${src%/*}"
    cp -v -- "$src" "$BACKUP_DIR/$src"

    echo "Embedded: title='$emb_title' author='$emb_author' year='$emb_year'"

    local title="$emb_title" author="$emb_author" year="$emb_year"

    if [[ -z "$title" || ${#title} -lt 3 || "$title" == "$orig_name" ]]; then
        echo "Parsing filename..."
        if [[ "$stem" == *"Anna's Archive"* ]]; then
            IFS=$'\n' read -r title author year < <(parse_annas_archive "$stem")
        else
            IFS=$'\n' read -r title author _ < <(parse_simple "$stem")
        fi
        echo "Parsed: title='$title' author='$author' year='$year'"
    fi

    if [[ -z "$title" || ${#title} -lt 3 ]]; then
        title="$stem"
    fi

    if [[ -z "$author" || "$author" =~ ^(Unknown|Unbekannt|N/A)$ || ${#author} -lt 2 ]]; then
        author=""
    fi

    if ! is_valid_year "$year"; then
        year=""
    fi

    if [[ -z "$year" || -z "$author" ]]; then
        echo "Trying online metadata fetch..."
        if fetch_result=$(fetch_online_metadata "$title" "$author" 8); then
            IFS=$'\n' read -r fetch_title fetch_author fetch_year <<< "$fetch_result"
            if [[ -n "$fetch_title" ]]; then
                echo "Online fetch: title='$fetch_title' author='$fetch_author' year='$fetch_year'"
                title="${fetch_title:-$title}"
                author="${fetch_author:-$author}"
                year="${fetch_year:-$year}"
            fi
        fi
    fi

    if [[ -z "$author" || "$author" =~ ^(Unknown|Unbekannt|N/A)$ || ${#author} -lt 2 ]]; then
        author="Unknown"
    fi

    if ! is_valid_year "$year"; then
        year=""
    fi

    if ! is_valid_year "$year"; then unset year; fi

    title=$(sanitize "$title")
    author=$(sanitize "$author")

    echo "Final: title='$title' author='$author' year='${year:-}'"

    local output_file
    output_file=$(uniquify_path "$dst_dir/${title} - ${author}.${ext}")

    cp -v -- "$src" "$output_file"
    echo "Saved: $output_file"

    local out_stem="${output_file%.*}"

    if [[ -z "${year:-}" ]]; then
        year="0001"
    fi

    cat > "${out_stem}.opf" << OPEOF
<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:calibre="http://calibre.mobwiki.com/">
    <dc:title>${title}</dc:title>
    <dc:creator>${author}</dc:creator>
    <dc:date>${year:-0001}</dc:date>
    <dc:language>en</dc:language>
    <calibre:series>${title}</calibre:series>
    <calibre:series_index>1.0</calibre:series_index>
  </metadata>
  <manifest>
  </manifest>
  <spine>
  </spine>
</package>
OPEOF
    echo "OPF: ${out_stem}.opf"

    if ebook-meta --get-cover="${out_stem}.jpg" -- "$output_file" 2>/dev/null; then
        echo "Cover: ${out_stem}.jpg"
    else
        echo "No cover found for: $output_file"
    fi

    if [[ "$orig_name" != "$(basename -- "$output_file")" ]]; then
        echo "Renamed, removing: $src"
        rm -v -- "$src"
    else
        echo "Same name, keeping source"
    fi

    if [[ "$author" == "Unknown" || -z "${year:-}" ]]; then
        log_issue "ISSUE: $src -> title='$title' author='$author' year='${year:-missing}'"
    fi

    echo "=== Done ==="
}

process_folder() {
    local folder="$1"
    echo ""
    echo "===== Processing: $folder ====="

    move_numbered_folders "$folder"

    for azw3 in "$folder"/*.azw3; do
        [[ -f "$azw3" ]] || continue
        local epub="${azw3%.azw3}.epub"
        echo "Converting azw3 to epub: $(basename -- "$azw3")"
        if ebook-convert -- "$azw3" "$epub" 2>/dev/null; then
            echo "Converted: $epub"
        else
            echo "Conversion failed: $azw3"
        fi
    done

    local -a files
    mapfile -d '' files < <(find "$folder" -maxdepth 1 -type f \( -iname "*.pdf" -o -iname "*.epub" -o -iname "*.mobi" \) ! -name "*.opf" -print0 2>/dev/null | sort -z)

    for file in "${files[@]}"; do
        [[ -z "$file" ]] && continue
        process_book "$file" "$folder"
    done
}

echo "=== Starting ebook processing ==="
echo "Working dir: $SCRIPT_DIR"
echo "Backup: $BACKUP_DIR"

for target in "Spiritualität" "spiritualität" "SPIRITUALITÄT"; do
    if [[ -d "$target" ]]; then
        echo "Found: $target"
        mapfile -d '' subdirs < <(find "$target" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | sort -z)
        for subdir in "${subdirs[@]}"; do
            [[ -z "$subdir" ]] && continue
            process_folder "$subdir"
        done
        break
    fi
done

echo ""
echo "=== Complete ==="
echo "Backup: $BACKUP_DIR"
echo "Log: $LOG_FILE"
if [[ -s "$LOG_FILE" ]]; then
    echo ""
    echo "Files with issues:"
    cat "$LOG_FILE"
fi