import re
from typing import List, Tuple

VK_LINK_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.|m\.)?vk\.(?:com|ru|me)/(?:im\?sel=|write)?([a-zA-Z0-9_.\-]+)',
    re.IGNORECASE
)

def clean_vk_link(line: str) -> str:
    """Cleans up a single VK link or target identifier."""
    line = line.strip().strip("'\"<>")
    if not line:
        return ""

    # Remove @ prefix if user pasted @username
    if line.startswith("@"):
        line = line[1:].strip()

    # Check for direct chat links like im?sel=12345 or im?sel=-12345
    if "im?sel=" in line:
        match_sel = re.search(r'im\?sel=(-?\d+)', line)
        if match_sel:
            sel_id = match_sel.group(1)
            if sel_id.startswith("-"):
                return f"https://vk.com/club{sel_id[1:]}"
            return f"https://vk.com/id{sel_id}"

    # Match standard URLs
    match = VK_LINK_PATTERN.search(line)
    if match:
        target = match.group(1)
        target = target.split('?')[0].split('#')[0].strip('/')
        return f"https://vk.com/{target}"

    # If raw target like 'id123', 'club123', or just username
    cleaned_raw = line.split('?')[0].split('#')[0].strip('/')
    if re.match(r'^[a-zA-Z0-9_.\-]+$', cleaned_raw):
        return f"https://vk.com/{cleaned_raw}"

    return line

def parse_links_from_content(text: str) -> Tuple[List[str], int, int]:
    """
    Parses a text containing VK links (one per line or separated by whitespace/commas).
    Returns:
        valid_links (List[str]): Unique list of valid VK links in order
        total_lines (int): Total non-empty lines found
        duplicates_count (int): Number of duplicate lines filtered out
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    total_lines = len(lines)

    valid_links: List[str] = []
    seen = set()
    duplicates_count = 0

    for line in lines:
        cleaned = clean_vk_link(line)
        if not cleaned:
            continue
        if cleaned in seen:
            duplicates_count += 1
            continue
        seen.add(cleaned)
        valid_links.append(cleaned)

    return valid_links, total_lines, duplicates_count
