"""postprocess.py — spoken command substitutions.

Whisper already handles capitalisation and punctuation, so we only do
command substitutions here (e.g. "new line" → newline character).
The substitution table lives in config so users can extend it.
"""

import re


def apply(text: str, substitutions: dict[str, str]) -> str:
    """Replace spoken command phrases with their literal equivalents."""
    if not text or not substitutions:
        return text

    # Sort by length descending so longer phrases match before their substrings.
    # Word boundaries (\b) prevent matches inside other words — e.g. "comma" must
    # not match inside "command", which Whisper produces when you say "comma and".
    for phrase, replacement in sorted(substitutions.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        text = pattern.sub(replacement, text)

    # Whisper often auto-punctuates the inflection of "comma"/"period" AND
    # writes the word, so substitution produces ",, " or ". .". Collapse those.
    # Ellipses ("...") are preserved because we only match when whitespace
    # separates the periods (or for commas / ? / !, where doubling is never valid).
    text = re.sub(r",(\s*,)+", ",", text)
    text = re.sub(r"\.(\s+\.)+", ".", text)
    text = re.sub(r"\?(\s*\?)+", "?", text)
    text = re.sub(r"!(\s*!)+", "!", text)
    # "word ," → "word," (whisper-inserted comma left a leading space)
    text = re.sub(r"\s+([,.!?])", r"\1", text)

    return text.strip()
