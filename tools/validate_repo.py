"""Repository checks: YAML syntax of packages and examples, and privacy."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def check_yaml() -> None:
    for path in sorted([*ROOT.glob("packages/*.yaml"), *ROOT.glob("examples/*.yaml")]):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document, f"{path.name} is empty"
        if path.parent.name == "packages" or "rest" in document:
            names = [s["name"] for b in document["template"] for s in b["sensor"]]
            ids = [s["unique_id"] for b in document["template"] for s in b["sensor"]]
            assert len(ids) == len(set(ids)), f"{path.name}: duplicate unique_id"
            assert len(names) == len(set(names)), f"{path.name}: duplicate name"
            for rest in document["rest"]:
                assert rest["scan_interval"] >= 600, f"{path.name}: please poll at most every 10 minutes"
        print(f"YAML ok: {path.relative_to(ROOT)}")


# Installation-specific patterns that must never be published.
PRIVATE_PATTERNS = {
    "private IPv4 address": r"\b(?:10|127|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}\b",
    "MAC address": r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b",
    "e-mail address": r"[\w.+-]+@(?!users\.noreply\.github\.com)[\w-]+\.[A-Za-z]{2,}",
    "precise GPS coordinate": r"(?:latitude|longitude)[=:\s]+-?\d{1,3}\.\d{3,}",
    "long serial number": r"\b\d{10,}\b",
}
# Optional local word list (one word per line), never committed: .privacy-words
PRIVATE_WORDS_FILE = ROOT / ".privacy-words"


def check_privacy() -> None:
    words = []
    if PRIVATE_WORDS_FILE.exists():
        words = [w.strip() for w in PRIVATE_WORDS_FILE.read_text(encoding="utf-8").splitlines() if w.strip()]
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or ".git" in path.parts or path.name in {Path(__file__).name, ".privacy-words"}:
            continue
        if path.suffix.lower() not in {".yaml", ".yml", ".md", ".py", ".txt", ".json", ".csv"}:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in PRIVATE_PATTERNS.items():
            for match in re.finditer(pattern, text):
                problems.append(f"{path.relative_to(ROOT)}: {label}: {match.group(0)}")
        for word in words:
            if re.search(rf"(?i)\b{re.escape(word)}", text):
                problems.append(f"{path.relative_to(ROOT)}: installation-specific word: {word}")
    assert not problems, "Privacy check failed:\n" + "\n".join(problems)
    print(f"Privacy check passed ({len(words)} local words)")


if __name__ == "__main__":
    check_yaml()
    check_privacy()
