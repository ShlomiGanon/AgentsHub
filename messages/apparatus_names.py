"""Resolve localized apparatus names to stable apparatus identifiers."""

from messages.catalog import SUPPORTED_LANGUAGES, get_catalog


def _catalog_aliases_by_id(apparatus_rows, stem: str) -> dict[str, tuple[str, ...]]:
    """Return every localized alias grouped by a registered stable id."""

    rows = tuple(apparatus_rows)
    aliases_by_id: dict[str, list[str]] = {
        str(row.get("apparatus_id", "")): [
            str(row.get("apparatus_id", "")), str(row.get("callsign", ""))
        ]
        for row in rows
        if row.get("apparatus_id")
    }
    if not stem:
        return {key: tuple(value) for key, value in aliases_by_id.items()}

    known_ids = {apparatus_id.casefold(): apparatus_id for apparatus_id in aliases_by_id}
    prefix = f"{stem}.apparatus."
    for language in SUPPORTED_LANGUAGES:
        catalog = get_catalog(language)
        for key in catalog.messages:
            if not key.startswith(prefix) or not key.endswith(".id"):
                continue
            slug = key[len(prefix):-3]
            catalog_id = catalog.text(key)
            apparatus_id = known_ids.get(catalog_id.casefold())
            if not apparatus_id:
                continue
            name_key = f"{stem}.apparatus.{slug}.name"
            alias_key = f"{stem}.apparatus.{slug}.aliases"
            if name_key in catalog.messages:
                aliases_by_id[apparatus_id].append(catalog.text(name_key))
            if alias_key in catalog.messages:
                aliases_by_id[apparatus_id].extend(
                    part.strip() for part in catalog.text(alias_key).split("|")
                )
    return {
        apparatus_id: tuple(dict.fromkeys(alias for alias in aliases if alias))
        for apparatus_id, aliases in aliases_by_id.items()
    }


def resolve_apparatus_id_from_records(apparatus_rows, phrase: str, stem: str) -> str:
    """Resolve an id, callsign, or catalog alias against registered apparatus.

    Unknown references are returned unchanged so the store reports an explicit
    not-found result instead of silently selecting another vehicle.
    """

    text = phrase.strip()
    if not text:
        return ""
    rows = tuple(apparatus_rows)
    folded = text.casefold()
    for apparatus in rows:
        if str(apparatus.get("apparatus_id", "")).casefold() == folded:
            return apparatus["apparatus_id"]
    for apparatus in rows:
        if str(apparatus.get("callsign", "")).casefold() == folded:
            return apparatus["apparatus_id"]
    best_id = ""
    best_len = 0
    for apparatus_id, aliases in _catalog_aliases_by_id(rows, stem).items():
        for alias in aliases:
            alias_folded = alias.casefold()
            if folded == alias_folded or alias_folded in folded:
                if len(alias) > best_len:
                    best_id = apparatus_id
                    best_len = len(alias)
    return best_id or text


def resolve_apparatus_ids_from_records(apparatus_rows, phrases, stem: str) -> tuple[str, ...]:
    """Return only registered apparatus mentioned across extracted phrases or report text."""

    rows = tuple(apparatus_rows)
    if isinstance(phrases, str):
        phrases = (phrases,)
    texts = tuple(str(phrase).strip() for phrase in phrases if str(phrase).strip())
    aliases_by_id = _catalog_aliases_by_id(rows, stem)
    resolved: list[str] = []
    for text in texts:
        folded = text.casefold()
        matches: list[tuple[int, int, str]] = []
        for apparatus_id, aliases in aliases_by_id.items():
            best_position = -1
            best_length = 0
            for alias in aliases:
                position = folded.find(alias.casefold())
                if position >= 0 and len(alias) > best_length:
                    best_position = position
                    best_length = len(alias)
            if best_position >= 0:
                matches.append((best_position, -best_length, apparatus_id))
        for _position, _negative_length, apparatus_id in sorted(matches):
            if apparatus_id not in resolved:
                resolved.append(apparatus_id)
    return tuple(resolved)


def resolve_apparatus_id(store, phrase: str, stem: str) -> str:
    """Resolve a localized apparatus reference using the live registry."""

    text = phrase.strip()
    if not text:
        return ""
    existing = store.get_apparatus(text)
    if existing is not None:
        return existing["apparatus_id"]
    return resolve_apparatus_id_from_records(store.list_apparatus(), text, stem)
