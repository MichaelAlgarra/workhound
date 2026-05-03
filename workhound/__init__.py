__version__ = "0.2.0"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
}

DEFAULT_MAX_PAGES = 5
DEFAULT_PROFILE_NAME = "default"

LEVEL_EXCLUDES = {
    "entry": [
        r"director", r"vice president", r"VP", r"head of", r"chief",
        r"executive", r"principal", r"distinguished",
    ],
    "mid": [
        r"director", r"vice president", r"VP", r"head of", r"chief",
        r"intern", r"co-op", r"manager.*(?:people|team)", r"executive",
    ],
    "senior": [
        r"intern", r"co-op", r"junior",
    ],
    "leadership": [
        r"intern", r"co-op", r"junior",
    ],
    "any": [],
}
