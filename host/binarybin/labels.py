"""Which ImageNet classes mean "recyclable" and which mean "not".

The zero-shot backend runs an ImageNet-pretrained MobileNetV3 and folds its
1000 classes down to our two. It will never be as good as a model fine-tuned
on photographs of our own bin under our own LED, but it works on day one with
no dataset, and it is a genuinely useful baseline for the items people
actually throw away: bottles, cans, cartons, wrappers, food.

Edit these sets freely - that is the point of keeping them in a plain Python
file rather than baked into a checkpoint. The substring rules catch the long
tail ("beer bottle", "wine bottle", "pop bottle" are all separate classes).
"""

from __future__ import annotations

RECYCLABLE = "recyclable"
NON_RECYCLABLE = "non_recyclable"
UNKNOWN = "unknown"

# --- exact ImageNet-1k category names --------------------------------------
RECYCLABLE_NAMES: set[str] = {
    "water bottle", "pop bottle", "beer bottle", "wine bottle", "pill bottle",
    "whiskey jug", "water jug", "milk can", "beer glass", "goblet", "vase",
    "carton", "packet", "envelope", "paper towel", "plastic bag", "shopping basket",
    "tin foil", "can opener", "soap dispenser", "lotion", "sunscreen",
    "perfume", "pop can", "beaker", "petri dish", "measuring cup", "cocktail shaker",
    "coffee mug", "cup", "pitcher", "teapot", "tray", "ashcan", "crate",
    "cassette", "CD player", "remote control", "mouse", "cellular telephone",
    "nail", "screw", "hook", "safety pin", "padlock", "lighter",
    "ballpoint", "fountain pen", "rubber eraser", "binder", "notebook",
    "book jacket", "comic book", "menu", "jigsaw puzzle", "crossword puzzle",
}

NON_RECYCLABLE_NAMES: set[str] = {
    # food and organics
    "banana", "orange", "lemon", "pineapple", "strawberry", "fig", "pomegranate",
    "Granny Smith", "jackfruit", "custard apple", "corn", "ear", "acorn",
    "broccoli", "cauliflower", "cucumber", "zucchini", "bell pepper", "mushroom",
    "head cabbage", "artichoke", "cardoon", "butternut squash", "acorn squash",
    "spaghetti squash", "hip", "rapeseed", "buckeye",
    "hotdog", "cheeseburger", "pizza", "meat loaf", "burrito", "bagel",
    "pretzel", "French loaf", "dough", "mashed potato", "carbonara",
    "ice cream", "ice lolly", "trifle", "guacamole", "consomme", "hot pot",
    "potpie", "eggnog", "red wine", "espresso", "chocolate sauce", "plate",
    # soiled or non-recoverable
    "toilet tissue", "diaper", "handkerchief", "mop", "broom", "swab",
    "face powder", "hair spray", "Band Aid", "rubber", "bath towel", "wool",
    "velvet", "sock", "jersey", "sandal", "running shoe", "cowboy boot",
}

# --- substring fallbacks, checked only if the exact name misses -------------
RECYCLABLE_SUBSTRINGS: tuple[str, ...] = (
    "bottle", "can", "carton", "jar", "jug", "tin", "paper", "cardboard",
    "box", "newspaper", "magazine", "foil", "glass", "aluminium", "aluminum",
)

NON_RECYCLABLE_SUBSTRINGS: tuple[str, ...] = (
    "food", "fruit", "vegetable", "soup", "sauce", "bread", "cake", "meat",
    "tissue", "napkin", "nappy", "leaf", "flower", "plant", "shell", "bone",
)


def category_for(imagenet_name: str) -> str:
    """Fold one ImageNet category name into our two-way decision."""
    if imagenet_name in RECYCLABLE_NAMES:
        return RECYCLABLE
    if imagenet_name in NON_RECYCLABLE_NAMES:
        return NON_RECYCLABLE

    low = imagenet_name.lower()
    # Non-recyclable wins ties: a "food can" that is actually a half-eaten tin
    # belongs in the wet bin, and contaminating the dry stream is the
    # expensive mistake.
    for frag in NON_RECYCLABLE_SUBSTRINGS:
        if frag in low:
            return NON_RECYCLABLE
    for frag in RECYCLABLE_SUBSTRINGS:
        if frag in low:
            return RECYCLABLE
    return UNKNOWN


def bin_for(category: str) -> int:
    from .config import BIN_NON_RECYCLABLE, BIN_RECYCLABLE

    return BIN_RECYCLABLE if category == RECYCLABLE else BIN_NON_RECYCLABLE
