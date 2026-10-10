"""Resolve a camera phrase to the stable camera id using both message catalogs."""

import re

from messages.catalog import SUPPORTED_LANGUAGES, get_catalog


def resolve_camera_id_from_records(cameras, phrase: str, stem: str) -> str:
    """Resolve ``phrase`` against camera rows and localized catalog aliases.

    This pure variant is also usable while a protocol is being bound, before an
    agent/store instance exists.  Unknown references are intentionally returned
    unchanged so the eventual tool call can produce an explicit not-found result.
    """

    text = phrase.strip()
    if not text:
        return ""
    camera_rows = tuple(cameras)
    folded = text.casefold()
    for camera in camera_rows:
        if str(camera.get("camera_id", "")).casefold() == folded:
            return camera["camera_id"]
    for camera in camera_rows:
        if str(camera.get("name", "")).casefold() == folded:
            return camera["camera_id"]
    if re.fullmatch(r"\d+", text):
        number = int(text)
        numeric_matches = []
        for camera in camera_rows:
            suffix = re.search(r"(\d+)$", str(camera.get("camera_id", "")))
            if suffix and int(suffix.group(1)) == number:
                numeric_matches.append(camera["camera_id"])
        if len(numeric_matches) == 1:
            return numeric_matches[0]
    if not stem:
        return text

    known_ids = {str(camera.get("camera_id", "")).casefold(): camera["camera_id"] for camera in camera_rows}
    best_id = ""
    best_len = 0
    prefix = f"{stem}.camera."
    for language in SUPPORTED_LANGUAGES:
        catalog = get_catalog(language)
        for key in catalog.messages:
            if not key.startswith(prefix) or not key.endswith(".id"):
                continue
            slug = key[len(prefix):-3]
            catalog_camera_id = catalog.text(key)
            camera_id = known_ids.get(catalog_camera_id.casefold())
            if not camera_id:
                continue
            aliases = [catalog_camera_id]
            name_key = f"{stem}.camera.{slug}.name"
            alias_key = f"{stem}.camera.{slug}.aliases"
            if name_key in catalog.messages:
                aliases.append(catalog.text(name_key))
            if alias_key in catalog.messages:
                aliases.extend(part.strip() for part in catalog.text(alias_key).split("|"))
            for alias in aliases:
                if not alias:
                    continue
                alias_folded = alias.casefold()
                if folded == alias_folded or alias_folded in folded:
                    if len(alias) > best_len:
                        best_id = camera_id
                        best_len = len(alias)
    return best_id or text


def resolve_camera_id(store, phrase: str, stem: str) -> str:
    """Return the camera id for an id, a stored name, or a catalog alias.

    An unknown phrase is returned unchanged so the store can still report that
    the camera does not exist.
    """

    text = phrase.strip()
    if not text:
        return ""
    existing = store.get_camera(text)
    if existing is not None:
        return existing["camera_id"]
    return resolve_camera_id_from_records(store.list_cameras(), text, stem)
