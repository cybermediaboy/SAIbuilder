"""SAIbuilder — top-level bootstrap convenience function."""
from __future__ import annotations
from pathlib import Path
from sai.core.index_schema import SAIIndex


def bootstrap(filepath: str, lang: str = "pine6") -> tuple[str, SAIIndex]:
    """Read *filepath*, run the language tokenizer, return (src, index).

    Usage::

        import sai
        src, index = sai.bootstrap("MyScript.pine", lang="pine6")
        print(index.summary())
    """
    src = Path(filepath).read_text(encoding="utf-8")
    if lang == "pine6":
        from sai.languages.pine6 import Pine6Tokenizer
        return src, Pine6Tokenizer().build(src, filepath=filepath)
    raise ValueError(f"Unsupported language: {lang!r}. Available: pine6")
