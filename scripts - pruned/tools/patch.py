def heal_dangling_try_blocks(src: str) -> str:
    """
    Make panes.py syntactically safe by:
      1) Adding a generic 'except' to any top-level try: block missing except/finally.
      2) Wrapping 'viewport' lines (get_viewport_client_width/height, configure_item size calls)
         that appear at top-level outside any try in a try/except.
      3) Healing lone 'try:' lines (no indented body).
    Idempotent â€” safe to run multiple times.
    """
    import re

    # --- (A) Fix top-level 'try:' blocks that have a body but no except/finally
    block_pat = re.compile(
        r"(^try:\n(?:^(?: {4}|\t).*\n)+)"      # top-level try + at least one indented line
        r"(?=(?:^\S)|\Z)",                     # next top-level stmt or EOF
        re.MULTILINE
    )
    out, pos = [], 0
    for m in block_pat.finditer(src):
        out.append(src[pos:m.start()])
        block = m.group(1)
        after = src[m.end():]
        if re.match(r"^(?:except\b|finally\b)", after, re.MULTILINE):
            out.append(block)  # already has a handler
        else:
            out.append(block + "except Exception:\n    pass\n")
        pos = m.end()
    out.append(src[pos:])
    src = "".join(out)

    # --- (B) Heal lone 'try:' lines (no body at all)
    # e.g., "try:\n<next top-level or EOF>"
    lone_try_pat = re.compile(r"^try:\s*(?=^\S|\Z)", re.MULTILINE)
    if lone_try_pat.search(src):
        src = lone_try_pat.sub("try:\n    pass\nexcept Exception:\n    pass\n", src)

    # --- (C) Wrap viewport-ish lines that are stranded at top-level (not under try)
    # We conservatively detect a short "chunk" of contiguous top-level lines touching viewport APIs
    v_needles = (
        "get_viewport_client_width()", "get_viewport_client_height()",
        "set_item_width(", "set_item_height(", "configure_item("
    )
    lines = src.splitlines(True)

    def is_top_level(line: str) -> bool:
        return not line.startswith((" ", "\t"))

    def is_view_line(line: str) -> bool:
        s = line.strip()
        if s.startswith(("def ", "class ", "from ", "import ", "try:", "except", "finally")):
            return False
        return any(n in line for n in v_needles)

    fixed = []
    i = 0
    while i < len(lines):
        if is_top_level(lines[i]) and is_view_line(lines[i]):
            # collect a small chunk of viewport-ish lines until we hit a blank or a new top-level def/import/try
            j = i
            chunk = []
            while j < len(lines):
                Ls = lines[j].strip()
                if not Ls:
                    chunk.append(lines[j]); j += 1
                    continue
                if Ls.startswith(("def ", "class ", "from ", "import ", "try:", "except", "finally")):
                    break
                # stop if line is indented (belongs to some other block)
                if lines[j].startswith((" ", "\t")):
                    break
                # include it if it's "viewport-ish"; otherwise stop (to avoid wrapping random code)
                if is_view_line(lines[j]):
                    chunk.append(lines[j]); j += 1
                else:
                    break
            # wrap the collected chunk
            fixed.append("try:\n")
            fixed.extend("    " + L for L in chunk)
            fixed.append("except Exception:\n    pass\n")
            i = j
        else:
            fixed.append(lines[i])
            i += 1

    return "".join(fixed)

