from pathlib import Path

def load():
    return Path('config.txt').read_text().strip()
