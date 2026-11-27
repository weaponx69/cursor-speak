"""Turn a Markdown reply into plain prose that sounds natural when spoken.

Dropped: fenced code blocks, shell prompts, tables' header rows, URLs, file paths
and file names, commands, inline code that looks like code, images, HTML, emoji.
Kept: headings and bullets (as sentences), link text, plain words, and code-ish
names, which are read as words ("PlayerInput" becomes "Player Input").
"""

from __future__ import annotations

import re
import unicodedata

KNOWN_EXT = {
    "cs", "py", "sh", "bash", "js", "ts", "tsx", "jsx", "json", "md", "txt", "yml", "yaml",
    "png", "jpg", "jpeg", "gif", "svg", "webp", "unity", "prefab", "asset", "meta", "log",
    "csv", "xml", "html", "css", "toml", "ini", "cfg", "conf", "lock", "deb", "zip", "tar",
    "gz", "db", "sqlite", "wav", "mp3", "mp4", "mov", "asmdef", "shader", "mat", "anim",
    "controller", "cpp", "h", "c", "go", "rs", "java", "kt", "swift", "onnx", "pdf", "docx",
    "desktop", "service", "sock", "pid", "whl",
}
_EXT_ALT = "|".join(sorted(KNOWN_EXT, key=len, reverse=True))

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_BULLET = re.compile(r"^\s*[-*+\u2022]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d{1,3}[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>+\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_SHELL_PROMPT = re.compile(r"^\s*(\$|#!|>>>)\s")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"\b(?:https?|file|ftp)://\S+|\bwww\.\S+", re.I)
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_CODE = re.compile(r"(`+)(.+?)\1", re.S)
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")
_BARE_PATH = re.compile(
    r"(?<![\w])(?:~|\.{0,2})?/[\w.\-@+/]+|(?<![\w/])[\w.\-]+/[\w.\-/]+\.[A-Za-z0-9]{1,8}\b"
)
_BARE_FILE = re.compile(r"\b[\w\-]+\.(?:%s)\b" % _EXT_ALT, re.I)

_SUBS = str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789")
_CMD_START = re.compile(
    r"^(sudo|apt|apt-get|dpkg|git|gh|npm|npx|pip|pip3|pipx|python3?|bash|sh|cd|ls|cat|grep|rg|"
    r"curl|wget|chmod|chown|mkdir|rm|mv|cp|docker|dotnet|make|cargo|node|unity|xdotool|kill|"
    r"ps|tail|head|echo|export|source|systemctl|journalctl|ssh|scp)\b"
)


def split_identifier(s: str) -> str:
    s = s.replace("_", " ").replace(".", " ")
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


def _classify_code(code: str, identifiers: str) -> str:
    c = code.strip()
    if not c:
        return ""
    if re.fullmatch(r"[^\w\s]+", c):
        return {"?": "question mark", "!": "exclamation mark"}.get(c, "")
    if "/" in c or "\\" in c or c.startswith("~"):
        return ""
    if re.search(r"\s", c):
        if re.search(r"(^|\s)-{1,2}[A-Za-z]", c) or re.search(r"[|&;$><=]", c):
            return ""
        if _CMD_START.match(c):
            return ""
        if re.fullmatch(r"[A-Za-z0-9' ,.%]+", c) and len(c) <= 40:
            return c
        return ""
    m = re.fullmatch(r"(.+)\.([A-Za-z0-9]{1,8})", c)
    if m and m.group(2).lower() in KNOWN_EXT:
        return ""
    ident = re.sub(r"\(\)$", "", c)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", ident):
        if identifiers == "drop":
            return ident if re.fullmatch(r"[a-z]+", ident) else ""
        return split_identifier(ident)
    if re.fullmatch(r"[~+-]?\d[\d.,]*%?[A-Za-z]*", c):
        return c
    if re.fullmatch(r"[A-Za-z0-9_-]+", c):
        return c.replace("-", " ").replace("_", " ")
    return ""


def _strip_symbols(s: str) -> str:
    return "".join(
        ch
        for ch in s
        if unicodedata.category(ch) not in ("So", "Sk", "Cf", "Cs", "Co") and ch not in "\ufe0f\u200d"
    )


def clean_inline(s: str, identifiers: str = "speak") -> str:
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(_classify_code(m.group(2), identifiers))
        return f"\x00{len(codes) - 1}\x00"

    s = _CODE.sub(stash, s)
    s = _IMAGE.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = _URL.sub("", s)
    s = _TAG.sub("", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s)
    s = re.sub(r"(?<![\w])_(?!\s)(.+?)(?<!\s)_(?![\w])", r"\1", s)
    s = s.replace("**", "").replace("~~", "")
    s = _BARE_PATH.sub("", s)
    s = _BARE_FILE.sub("", s)
    s = s.translate(_SUBS)
    s = re.sub(r"(?<=\d)\s*[\u2013-](?=\d)", " to ", s)
    s = re.sub(r"\s*[\u2014\u2013]\s*", ", ", s)
    s = re.sub(r"\s*(?:\u2192|->|=>)\s*", " to ", s)
    s = s.replace("\u2026", "...")
    s = re.sub(r"\s*&\s*", " and ", s)
    s = re.sub(r"(\d)\s*%", r"\1 percent", s)
    s = re.sub(r"~\s*(?=\d)", "about ", s)
    s = s.replace("~", "")
    s = s.replace("\u2248", "about ").replace("\u00d7", " times ").replace("\u2265", " at least ")
    s = s.replace("\u2264", " at most ")
    s = re.sub(r"(?<=\s)\+(?=\s)", "plus", s)
    s = _strip_symbols(s)
    s = _PLACEHOLDER.sub(lambda m: codes[int(m.group(1))], s)
    s = re.sub(r"\(\s*[,;:]?\s*\)", "", s)
    s = re.sub(r"\[\s*\]", "", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([(\[])\s+", r"\1", s)
    s = re.sub(r"\s+([)\]])", r"\1", s)
    s = re.sub(r",\s*,+", ",", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"^[,;:\s\-]+", "", s)
    return s


def _terminal(s: str) -> str:
    if not s:
        return s
    core = s.rstrip("\"')]}")
    if core and core[-1] not in ".!?:;\u2026":
        return s + "."
    return s


def to_speech(markdown: str, identifiers: str = "speak") -> str:
    """Return speakable plain text, paragraphs separated by blank lines."""
    out: list[str] = []
    in_fence = False
    in_table = False
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            text = clean_inline(" ".join(buf), identifiers)
            text = _terminal(text)
            if text and re.search(r"\w", text):
                out.append(text)
        buf = []

    for raw in markdown.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if _FENCE.match(line):
            flush()
            in_fence = not in_fence
            in_table = False
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            in_table = False
            continue
        if stripped.startswith("|"):
            flush()
            if _TABLE_SEP.match(stripped):
                continue
            if not in_table:
                in_table = True  # header row: skip it
                continue
            cells = [clean_inline(c, identifiers) for c in stripped.strip("|").split("|")]
            cells = [c for c in cells if c and re.search(r"\w", c)]
            if cells:
                out.append(_terminal(", ".join(cells)))
            continue
        in_table = False
        if _HR.match(stripped) or _SHELL_PROMPT.match(line):
            flush()
            continue
        m = _HEADING.match(line)
        if m:
            flush()
            buf = [m.group(1)]
            flush()
            continue
        m = _BULLET.match(line) or _NUMBERED.match(line)
        if m:
            flush()
            buf = [m.group(1)]
            continue
        m = _QUOTE.match(line)
        if m:
            buf.append(m.group(1))
            continue
        buf.append(stripped)
    flush()
    return "\n\n".join(out).strip()


def limit(text: str, max_chars: int) -> str:
    """Cut overly long replies at a sentence boundary."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    idx = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("? "), cut.rfind("! "))
    if idx < max_chars // 2:
        idx = max_chars - 1
    return cut[: idx + 1].rstrip() + " The rest is in the terminal."


def clean(markdown: str, identifiers: str = "speak", max_chars: int = 2500) -> str:
    return limit(to_speech(markdown, identifiers), max_chars)


def split_chunks(text: str, limit_chars: int = 280) -> list[str]:
    """Split speech text into sentence-sized chunks for streaming synthesis."""
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        cur = ""
        for sent in sentences:
            if not sent:
                continue
            while len(sent) > limit_chars:
                cut = sent.rfind(", ", 0, limit_chars)
                if cut < 40:
                    cut = sent.rfind(" ", 0, limit_chars)
                if cut < 40:
                    cut = limit_chars
                piece, sent = sent[: cut + 1].strip(), sent[cut + 1 :].strip()
                if cur:
                    chunks.append(cur)
                    cur = ""
                chunks.append(piece)
            if cur and len(cur) + 1 + len(sent) > limit_chars:
                chunks.append(cur)
                cur = sent
            else:
                cur = f"{cur} {sent}".strip()
        if cur:
            chunks.append(cur)
    return [c for c in chunks if re.search(r"\w", c)]
