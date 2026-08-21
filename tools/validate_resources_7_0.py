#!/usr/bin/env python3
"""Validate the LunaGC 7.0 resource layout and playable-avatar references."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


TARGET_AVATARS = {
    10000125: ("Columbina", 12501),
    10000148: ("Alyosha", 14801),
    10000150: ("Odette", 15001),
}
SPECIAL_NON_COMBAT_AVATARS = {10000117, 10000118}


def load_json(path: pathlib.Path):
    text = path.read_text(encoding="utf-8-sig")
    if path.name == "QuestEncryptionKeys.json":
        text = re.sub(r"(?m)^\s*//.*$", "", text)
    return json.loads(text)


def keyed(rows, key="id"):
    return {row[key]: row for row in rows if key in row}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("resources", type=pathlib.Path)
    parser.add_argument(
        "--all-json", action="store_true", help="Parse every JSON file before reference checks."
    )
    args = parser.parse_args()
    root = args.resources.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    required = [
        "BinOutput/Ability/Temp",
        "BinOutput/Avatar",
        "BinOutput/Talent/AvatarTalents",
        "ExcelBinOutput/AvatarExcelConfigData.json",
        "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json",
        "ExcelBinOutput/AvatarSkillExcelConfigData.json",
        "ExcelBinOutput/AvatarTalentExcelConfigData.json",
        "ExcelBinOutput/ProudSkillExcelConfigData.json",
        "ScriptSceneData/flat.luas.scenes.full_globals.lua.json",
        "Server/QuestEncryptionKeys.json",
        "TextMap/TextMapEN.json",
    ]
    for relative in required:
        if not (root / relative).exists():
            errors.append(f"missing required path: {relative}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    parsed_json = 0
    if args.all_json:
        for path in root.rglob("*.json"):
            try:
                load_json(path)
                parsed_json += 1
            except Exception as exc:
                errors.append(f"invalid JSON {path.relative_to(root)}: {exc}")

    excel = root / "ExcelBinOutput"
    avatars = keyed(load_json(excel / "AvatarExcelConfigData.json"))
    depots = keyed(load_json(excel / "AvatarSkillDepotExcelConfigData.json"))
    skills = keyed(load_json(excel / "AvatarSkillExcelConfigData.json"))
    talents = keyed(load_json(excel / "AvatarTalentExcelConfigData.json"), "talentId")
    proud_rows = load_json(excel / "ProudSkillExcelConfigData.json")
    proud_groups = {row.get("proudSkillGroupId") for row in proud_rows}

    ability_names: set[str] = set()
    ability_files = 0
    for path in (root / "BinOutput/Ability/Temp").rglob("*.json"):
        try:
            rows = load_json(path)
        except Exception as exc:
            errors.append(f"cannot parse ability file {path.relative_to(root)}: {exc}")
            continue
        ability_files += 1
        if not isinstance(rows, list):
            continue
        for wrapper in rows:
            default = wrapper.get("Default") if isinstance(wrapper, dict) else None
            if isinstance(default, dict) and default.get("abilityName"):
                ability_names.add(default["abilityName"])

    playable = [
        avatar
        for avatar in avatars.values()
        if 10000002 <= avatar.get("id", 0) < 10000901
        and avatar.get("useType") != "AVATAR_TEST"
        and avatar.get("id") not in SPECIAL_NON_COMBAT_AVATARS
    ]
    for avatar_id in sorted(SPECIAL_NON_COMBAT_AVATARS & avatars.keys()):
        warnings.append(
            f"special mannequin avatar {avatar_id} is intentionally excluded from combat checks"
        )
    configured_ability_references = 0
    for avatar in playable:
        avatar_id = avatar["id"]
        depot_id = avatar.get("skillDepotId", 0)
        depot = depots.get(depot_id)
        if depot is None:
            errors.append(f"avatar {avatar_id}: missing skill depot {depot_id}")
            continue

        for skill_id in [*depot.get("skills", []), depot.get("energySkill", 0)]:
            if skill_id and skill_id not in skills:
                errors.append(f"avatar {avatar_id}: missing skill {skill_id}")
        for talent_id in depot.get("talents", []):
            if talent_id and talent_id not in talents:
                errors.append(f"avatar {avatar_id}: missing talent {talent_id}")
        for opening in depot.get("inherentProudSkillOpens", []):
            group_id = opening.get("proudSkillGroupId", 0)
            if group_id and group_id not in proud_groups:
                errors.append(f"avatar {avatar_id}: missing proud-skill group {group_id}")

        icon_name = avatar.get("iconName", "")
        internal_name = icon_name.removeprefix("UI_AvatarIcon_")
        config_path = root / f"BinOutput/Avatar/ConfigAvatar_{internal_name}.json"
        if not config_path.exists():
            errors.append(f"avatar {avatar_id}: missing {config_path.relative_to(root)}")
            continue

        config = load_json(config_path)
        configured = {
            item.get("abilityName")
            for item in config.get("abilities", [])
            if isinstance(item, dict) and item.get("abilityName")
        }
        configured_ability_references += len(configured)
        unresolved = sorted(configured - ability_names)
        if unresolved:
            errors.append(
                f"avatar {avatar_id}: unresolved configured abilities: {', '.join(unresolved)}"
            )

    for avatar_id, (name, expected_depot) in TARGET_AVATARS.items():
        avatar = avatars.get(avatar_id)
        if avatar is None:
            errors.append(f"target avatar {name}: missing AvatarExcel entry {avatar_id}")
            continue
        if avatar.get("skillDepotId") != expected_depot:
            errors.append(
                f"target avatar {name}: depot {avatar.get('skillDepotId')} != {expected_depot}"
            )

        config_path = root / f"BinOutput/Avatar/ConfigAvatar_{name}.json"
        ability_path = root / f"BinOutput/Ability/Temp/AvatarAbilities/ConfigAbility_Avatar_{name}.json"
        talent_path = root / f"BinOutput/Talent/AvatarTalents/ConfigTalent_{name}.json"
        for path in (config_path, ability_path, talent_path):
            if not path.exists():
                errors.append(f"target avatar {name}: missing {path.relative_to(root)}")

        if config_path.exists():
            config = load_json(config_path)
            configured = {
                item.get("abilityName")
                for item in config.get("abilities", [])
                if isinstance(item, dict) and item.get("abilityName")
            }
            unresolved = sorted(configured - ability_names)
            if unresolved:
                errors.append(
                    f"target avatar {name}: unresolved configured abilities: {', '.join(unresolved)}"
                )

    print(f"Resource root: {root}")
    print(f"Playable avatars checked: {len(playable)}")
    print(f"Avatar ability references checked: {configured_ability_references}")
    print(f"Ability files checked: {ability_files} ({len(ability_names)} named abilities)")
    if args.all_json:
        print(f"JSON files parsed: {parsed_json}")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print("RESULT: PASS" if not errors else f"RESULT: FAIL ({len(errors)} errors)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
