"""Helpers for normalizing emoji sequences in width-sensitive message rendering."""

# Strip zero-width and presentation modifiers that make terminal cell width inconsistent.
EMOJI_MODIFIER_REPLACEMENTS = {
    "\u200d": "",
    "\u20e3": "",
    "\ufe0e": "",
    "\ufe0f": "",
    "\U0001F3FB": "",
    "\U0001F3FC": "",
    "\U0001F3FD": "",
    "\U0001F3FE": "",
    "\U0001F3FF": "",
}

_EMOJI_MODIFIER_TRANSLATION = str.maketrans(EMOJI_MODIFIER_REPLACEMENTS)
_REGIONAL_INDICATOR_START = ord("\U0001F1E6")
_REGIONAL_INDICATOR_END = ord("\U0001F1FF")


def _regional_indicator_to_letter(char: str) -> str:
    return chr(ord("A") + ord(char) - _REGIONAL_INDICATOR_START)


def _normalize_flag_emoji(text: str) -> str:
    """Convert flag emoji built from regional indicators into ASCII country codes."""
    normalized = []
    index = 0

    while index < len(text):
        current = text[index]
        current_ord = ord(current)

        if _REGIONAL_INDICATOR_START <= current_ord <= _REGIONAL_INDICATOR_END and index + 1 < len(text):
            next_char = text[index + 1]
            next_ord = ord(next_char)
            if _REGIONAL_INDICATOR_START <= next_ord <= _REGIONAL_INDICATOR_END:
                normalized.append(_regional_indicator_to_letter(current))
                normalized.append(_regional_indicator_to_letter(next_char))
                index += 2
                continue

        normalized.append(current)
        index += 1

    return "".join(normalized)


def normalize_message_text(text: str) -> str:
    """Strip modifiers and rewrite flag emoji into stable terminal-friendly text."""
    if not text:
        return text

    return _normalize_flag_emoji(text.translate(_EMOJI_MODIFIER_TRANSLATION))
