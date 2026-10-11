#!/bin/bash
folder="Spiritualität/Pagan"
echo "Starting find test for: $folder"
count=0
while IFS= read -r -d '' file; do
    echo "Found file: $file"
    ((count++)) || true
done < <(find "$folder" -maxdepth 1 -type f \( -iname "*.pdf" -o -iname "*.epub" \) ! -name "*.opf" -print0 2>/dev/null | sort -z)
echo "Total files found: $count"
