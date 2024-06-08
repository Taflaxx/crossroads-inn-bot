from bs4 import BeautifulSoup
import aiohttp
from models.build import Build
from models.enums.profession import Profession
from models.equipment import Equipment
from models.enums.equipment_slot import EquipmentSlot
from models.item import Item
from models.enums.rarity import Rarity
from api import API
from models.stats import EquipmentStats


async def sc_get(url):
    if not url.startswith("https://snowcrows.com/"):
        raise ValueError("Only snowcrows links are allowed")

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            return await r.read()


async def get_sc_build(url: str, api: API = API("")) -> Build:
    resp = await sc_get(url)
    sc_soup = BeautifulSoup(resp.decode("utf-8"), "html.parser")
    table_data = sc_soup.find_all("td")
    build = Build()
    build.name = f"{sc_soup.find_all('h1')[0].text}"
    build.profession = Profession[sc_soup.find_all("i", {"class": "fa-solid fa-shuffle mr-2"})[0].parent.text.strip().split(' ')[0].strip()]
    build.url = url
    equipment = Equipment()
    stats = EquipmentStats()
    mh, oh, ring, accessory = 1, 1, 1, 1
    for i in range(0, len(table_data), 2):
        div = table_data[i].div

        # Check if slot has item
        if not div["data-armory-ids"]:
            continue

        item = Item()
        item.item_id = int(div["data-armory-ids"])
        item_data = await api.get_item(item.item_id)
        item.name = item_data["name"]
        item.rarity = Rarity[item_data["rarity"]]
        item.level = item_data["level"]

        # TODO: handle relics
        if item_data["type"] == "Relic":
            continue

        if item_data["type"] in ["Consumable", "Gizmo"]:
            break

        # Infusion stats
        if item_data["type"] == "UpgradeComponent":
            amount = int(table_data[i+1].p.span.text.replace("x", ""))
            stats.add_attributes(EquipmentSlot.Helm, infix_upgrade=item_data["details"]["infix_upgrade"], multiplier=amount)
            continue

        if "infix_upgrade" in item_data["details"]:
            stats_id = item_data["details"]["infix_upgrade"]["id"]
        elif f"data-armory-{item.item_id}-stat" in str(div):
            stats_id = div[f"data-armory-{item.item_id}-stat"]
        else:
            print(str(div))
            raise Exception(f"Unable to determine stats for {item.name} on {url}")
        stats_data = await api.get_item_stats(stats_id)
        item.stats = stats_data["name"]

        upgrade_ids = []
        if f"data-armory-{item.item_id}-upgrades" in str(div):
            upgrade_ids = div[f"data-armory-{item.item_id}-upgrades"].split(",")
        for upgrade_id in upgrade_ids:
            item.add_upgrade((await api.get_item(int(upgrade_id)))["name"])

        slot = table_data[i + 1].p.span.string
        if slot == "Main Hand":
            slot = f"Weapon{'A' if mh == 1 else 'B'}1"
            mh += 1
        if slot == "Off Hand":
            slot = f"Weapon{'A' if oh == 1 and mh == 2 else 'B'}2"
            oh += 1
        if slot == "Ring":
            slot = f"Ring{ring}"
            ring += 1
        if slot == "Accessory":
            slot = f"Accessory{accessory}"
            accessory += 1
        if slot == "Backpiece":
            slot = "Backpack"

        if "type" in item_data["details"]:
            item.type = item_data["details"]["type"]
        else:
            item.type = slot
        item.slot = EquipmentSlot[slot]

        # Add stat attributes
        if "infix_upgrade" in item_data["details"]:
            stats.add_attributes(item.slot, infix_upgrade=item_data["details"]["infix_upgrade"])
        else:
            attribute_adjustment = item_data["details"]["attribute_adjustment"]
            if stats_id in item_data["details"]["stat_choices"]:
                attributes = stats_data["attributes"]
            else:
                for id in item_data["details"]["stat_choices"]:
                    stats_data = await api.get_item_stats(id)
                    if stats_data["name"] == item.stats:
                        attributes = stats_data["attributes"]
                        break
                else:
                    raise Exception(f"Invalid stats id: {url} at {item.name} (id: {item.item_id}, stat_id: {stats_id})")
            stats.calculate_attributes(item.slot, attributes, attribute_adjustment)

        equipment.add_item(item)
    equipment.stats = stats
    build.equipment = equipment
    return build


async def get_sc_builds(profession: Profession):
    # Find all recommended and viable builds that are not kite builds or beginner builds
    links = []
    for category in ["featured"]:
        resp = await sc_get(f"https://snowcrows.com/builds/{profession.name}?category={category}")
        sc_soup = BeautifulSoup(resp.decode("utf-8"), "html.parser")
        for link in sc_soup.find_all("a", href=True):
            if link["href"].startswith("/builds/") and "kite" not in link["href"] and "beginner" not in link["href"] and link["href"].count("/") > 2:
                links.append("https://snowcrows.com" + link["href"])
    return links
