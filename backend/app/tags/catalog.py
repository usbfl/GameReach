import json
from pathlib import Path

VOCABULARY = json.loads(Path(__file__).with_name("vocabulary.json").read_text(encoding="utf-8"))
VERSION = VOCABULARY["version"]
TAGS = {t["id"]: t for t in VOCABULARY["tags"]}


def model_vocabulary(ids=None):
    return [
        {k: t[k] for k in ("id", "label", "family", "definition", "aliases", "confusions")}
        for t in TAGS.values()
        if ids is None or t["id"] in ids
    ]


def mapping_plan(intents, scope):
    """Mappings recall candidates; they are never evidence of lived experience."""
    result = []
    for intent in intents:
        t = TAGS[intent["tag_id"]]
        for store in scope.get("stores") or ["steam", "gog", "epic", "itch"]:
            result.append({"tag_id": t["id"], "label": t["label"], "store": store, **t["platform_mappings"][store]})
    return result


# Catalog vocabulary spelling variants and broad proxies; never review evidence.
PROXY_TERMS = {
    "action_combat": [
        "action rpg",
        "action role-playing",
        "action role playing",
        "soulslike",
        "souls-like",
        "hack and slash",
    ],
    "read_patterns": ["action rpg", "action role-playing", "soulslike", "souls-like", "boss fight", "parry"],
    "build_variety": ["character customisation", "skill tree", "loadout", "deck building"],
}


def recall_terms(tag):
    return list(dict.fromkeys(tag["igdb"]["keywords"] + PROXY_TERMS.get(tag["id"], [])))


def recall_action(intents, scope, *, broad=False):
    # Absence of a shop tag does not establish absence of the experience.
    wanted = [TAGS[i["tag_id"]] for i in intents if i["polarity"] == "want"]
    genres = list(dict.fromkeys(g for t in wanted for g in t["igdb"]["genre_ids"]))[:8]
    keys = list(dict.fromkeys(k for t in wanted for k in recall_terms(t)))[:12]
    return {
        "tool_name": "search_games",
        "arguments": {
            "discover": True,
            "q": "",
            "stores": scope.get("stores", []),
            "platforms": scope.get("platforms", []),
            "genre_ids": genres,
            "tag_keywords": [] if broad else keys,
            "tag_recall": True,
        },
    }


def catalog_matches(game, intents):
    text = " ".join(game.get("tags", []) + game.get("catalog_keywords", [])).casefold()
    matched = []
    for i in intents:
        t = TAGS[i["tag_id"]]
        terms = recall_terms(t) + t["aliases"] + [t["label"]]
        if i["polarity"] == "want" and any(x.casefold() in text for x in terms):
            matched.append(t["id"])
    return matched


def catalog_priority(game, intents):
    """Current input dominates attachment preferences; this is a recall heuristic only."""
    matched = set(catalog_matches(game, intents))
    # Summary can add low-strength recall clues, never evidence for the final rank.
    summary = game.get("summary", "").casefold()

    def strength(i):
        if i["tag_id"] in matched:
            return 1.0
        return 0.25 if any(k.casefold() in summary for k in recall_terms(TAGS[i["tag_id"]])) else 0

    current = sum(
        i["priority"] * strength(i)
        for i in intents
        if i["polarity"] == "want" and i.get("source_id", "current") == "current"
    )
    attached = sum(
        i["priority"] * strength(i)
        for i in intents
        if i["polarity"] == "want" and i.get("source_id", "current") != "current"
    )
    return (current, attached)


def recall_branches(intents, scope):
    main = [i for i in intents if i.get("source_id", "current") == "current" and i["polarity"] == "want"]
    highest = max((i["priority"] for i in main), default=0)
    focused = [i for i in main if i["priority"] == highest]
    # Independent queries prevent one broad attachment keyword from consuming every page.
    branches = [recall_action([i], scope) for i in focused[:3]]
    if not branches:
        branches = [recall_action(main or intents, scope)]
    branches.append(recall_action(main or intents, scope, broad=True))
    unique = {}
    for b in branches:
        unique[json.dumps(b, sort_keys=True)] = b
    return list(unique.values())
