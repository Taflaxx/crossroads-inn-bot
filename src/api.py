from typing import List
from sqlalchemy import select
from database import Session
from exceptions import APIException
from models.boss import Boss
from models.enums.pools import KillProofPool
from models.feedback import *
from aiohttp_client_cache import CachedSession, SQLiteBackend
from models.enums.equipment_slot import EquipmentSlot
from models.enums.rarity import Rarity
from models.equipment import Equipment
from models.item import Item
from models.stats import EquipmentStats


class API:
    def __init__(self, api_key: str = None, version: str = "2021-07-24T00%3A00%3A00Z"):
        self.api_key = api_key
        self.version = version

        self.headers = {}
        if self.api_key:
            self.headers["Authorization"] = f"Bearer {self.api_key}"
        if self.version:
            self.headers["X-Schema-Version"] = self.version

        self.cache = SQLiteBackend(
            cache_name="api-cache.db",
            allowed_codes=(200,),
            urls_expire_after={
                "https://api.guildwars2.com/v2/items": 60*60*24*7,      # Cache items for 1 week
                "https://api.guildwars2.com/v2/itemstats": 60*60*24*7,  # Cache item stats for 1 week
                "https://api.guildwars2.com/v2/characters?id=*": 60,    # Cache characters for 1 min
                "https://api.guildwars2.com/": 0,                       # Don't cache anything else
            })

    async def get_endpoint_v2(self, endpoint: str):
        url = f"https://api.guildwars2.com/v2/{endpoint}"
        async with CachedSession(cache=self.cache) as session:
            resp = await session.get(url, headers=self.headers)
            if resp.status in (200, 401):
                return await resp.json()
            else:
                try:
                    raise APIException(url, resp.status, await resp.json())
                except Exception:
                    raise APIException(url, resp.status, None)

    async def check_key(self) -> FeedbackGroup:
        fbg = FeedbackGroup("API Key")
        # Check if api key is valid
        tokeninfo = await self.get_endpoint_v2("tokeninfo")
        if "Invalid access token" in str(tokeninfo):
            fbg.add(Feedback("Invalid API Key", FeedbackLevel.ERROR))
            return fbg

        fbg.add(Feedback("API Key is valid", FeedbackLevel.SUCCESS))

        # Check if correct permissions are set
        perms = tokeninfo["permissions"]
        if "account" not in perms:  # Should always be set but check anyway
            fbg.add(Feedback("API Key is missing 'account' permission", FeedbackLevel.ERROR))
        if "progression" not in perms:
            fbg.add(Feedback("API Key is missing 'progression' permission", FeedbackLevel.ERROR))
        if "characters" not in perms:
            fbg.add(Feedback("API Key is missing 'characters' permission", FeedbackLevel.ERROR))
        if "builds" not in perms:
            fbg.add(Feedback("API Key is missing 'builds' permission", FeedbackLevel.ERROR))

        if fbg.level == FeedbackLevel.SUCCESS:
            fbg.add(Feedback("API Key permissions are set up correctly", FeedbackLevel.SUCCESS))
        return fbg

    async def get_account_name(self) -> str:
        account = await self.get_endpoint_v2("account")
        return account["name"]

    async def get_characters(self):
        return await self.get_endpoint_v2("characters")

    async def get_character_data(self, character_name: str):
        return await self.get_endpoint_v2(f"characters?id={character_name}")

    async def get_item(self, item_id: int):
        return await self.get_endpoint_v2(f"items/{item_id}")

    async def get_item_stats(self, item_id: int):
        return await self.get_endpoint_v2(f"itemstats/{item_id}")

    async def check_mastery(self) -> FeedbackGroup:
        fbg = FeedbackGroup("Masteries")
        mastery_list = await self.get_endpoint_v2("account/masteries")
        gliding = False
        jackal = False

        for mastery in mastery_list:
            # check "Ley Line Gliding"
            if mastery["id"] == 8 and mastery["level"] >= 5:
                gliding = True
            # check "Shifting Sands"
            if mastery["id"] == 18 and mastery["level"] >= 2:
                jackal = True

        # add feedback
        if gliding:
            fbg.add(Feedback("Ley Line Gliding is unlocked", FeedbackLevel.SUCCESS))
        else:
            fbg.add(Feedback("Ley Line Gliding is not unlocked", FeedbackLevel.ERROR))
        if jackal:
            fbg.add(Feedback("Shifting Sands is unlocked", FeedbackLevel.SUCCESS))
        else:
            fbg.add(Feedback("Shifting Sands is not unlocked", FeedbackLevel.ERROR))
        return fbg

    async def check_kp(self, tier: int) -> FeedbackGroup:
        # load all relevant bosses from database
        async with Session.begin() as session:
            bosses = (await session.execute(select(Boss).where(Boss.kp_pool != KillProofPool.NOT_ALLOWED))).scalars().all()

            # get achievements of player
            achievements = await self.get_endpoint_v2("account/achievements")

            # check achievements
            bosses_killed = []
            for achievement in achievements:
                for boss in bosses:
                    # check if achievement is relevant and if it is done
                    if boss.achievement_id == achievement["id"]:
                        if achievement["done"]:
                            bosses_killed.append(boss)



            match tier:
                case 1:
                    return self.__check_kp_t1(bosses_killed, len(bosses))
                case 2:
                    return self.__check_kp_t2(bosses_killed, len(bosses))
                case 3:
                    bosses_missing = [boss for boss in bosses if boss not in bosses_killed]
                    return self.__check_kp_t3(bosses_killed, bosses_missing, len(bosses))
                case _:
                    raise ValueError("Invalid tier")

    def __check_kp_t1(self, bosses_killed: List[Boss], max_bosses: int, fbg: FeedbackGroup = None) -> FeedbackGroup:
        if not fbg:
            fbg = FeedbackGroup("Killproof")
        # check if at least 5 different bosses were killed
        if len(bosses_killed) >= 5:
            fbg.add(Feedback(f"You have killed {len(bosses_killed)}/{max_bosses} different bosses (5 required)", FeedbackLevel.SUCCESS))
        else:
            fbg.add(Feedback(f"You have killed {len(bosses_killed)}/{max_bosses} different bosses (5 required)", FeedbackLevel.ERROR))
        return fbg

    def __check_kp_t2(self, bosses_killed: List[Boss], max_bosses: int, fbg: FeedbackGroup = None) -> FeedbackGroup:
        if not fbg:
            fbg = FeedbackGroup("Killproof")
        # count the amount of restricted boss kills and remove them from the list
        pool_a_count = 0
        pool_b_count = 0
        for boss in bosses_killed:
            if boss.kp_pool == KillProofPool.POOL_B:
                pool_b_count += 1
            elif boss.kp_pool == KillProofPool.POOL_A:
                pool_a_count += 1
            else:
                raise ValueError(f"Invalid KillProofPool: {boss}")

        # allow a maximum of 5 bosses from the restricted boss pool
        if pool_a_count + min(pool_b_count, 5) >= 10:
            fbg.add(Feedback(f"You have killed {len(bosses_killed)}/{max_bosses} different bosses", FeedbackLevel.SUCCESS))
        # if not enough bosses were killed return error
        else:
            fbg.add(Feedback(f"You have killed {len(bosses_killed)}/{max_bosses} different bosses "
                             f"(pool A: {pool_a_count}, pool B: {pool_b_count}). "
                             f"In total you need 10 different boss kills with a minimum of 5 from pool B.", FeedbackLevel.ERROR))
        return fbg

    def __check_kp_t3(self, bosses_killed: List[Boss], bosses_missing: List[Boss], max_bosses: int, fbg: FeedbackGroup = None) -> FeedbackGroup:
        if not fbg:
            fbg = FeedbackGroup("Killproof")
        # check if all bosses were killed
        if len(bosses_killed) == max_bosses:
            fbg.add(Feedback(f"You have killed {len(bosses_killed)}/{max_bosses} different bosses", FeedbackLevel.SUCCESS))
        # if not enough bosses were killed check for missing encounters
        else:
            # if only HT CM + ToF CM is missing return success
            permitted_missing_bosses = ["Harvest Temple CM", "Temple of Febe CM"]
            applicant_can_pass = True
        
            for boss in bosses_missing:
                if boss.full_name not in permitted_missing_bosses:
                    applicant_can_pass = False
                    
            if applicant_can_pass:
                fbg.add(Feedback(f"You have killed {len(bosses_killed)}/{max_bosses} different bosses", FeedbackLevel.SUCCESS))
            # if not enough bosses were killed return error
            else:
                fbg.add(Feedback(f"You have killed {len(bosses_killed)}/{max_bosses} different bosses. "
                                 f"You need to have killed all bosses except HT CM & ToF CM.", FeedbackLevel.ERROR))
                nl = "\n"
                fbg.add(Feedback(f"You are missing the following bosses:\n"
                                 f"{nl.join([f'- {boss.full_name}' for boss in bosses_missing])}", FeedbackLevel.ERROR))
                
        return fbg

    async def get_equipment(self, character: str, tab: int = 1):
        char_data = await self.get_character_data(character)
        equipment_tab_items = None
        for equipment_tab in char_data["equipment_tabs"]:
            if equipment_tab["tab"] == tab:
                equipment_tab_items = equipment_tab

        if not equipment_tab_items:
            raise Exception("Equipment Tab not found")

        equipment = Equipment()
        stats = EquipmentStats()
        for equipment_tab_item in equipment_tab_items["equipment"]:
            # Skip items like underwater weapons and aqua breather
            try:
                EquipmentSlot[equipment_tab_item["slot"]]
            except KeyError:
                continue

            item = Item()
            item.item_id = equipment_tab_item["id"]
            item.slot = EquipmentSlot[equipment_tab_item["slot"]]
            item_data = await self.get_item(equipment_tab_item['id'])
            item.name = item_data["name"]
            item.rarity = Rarity[item_data["rarity"]]
            item.level = item_data["level"]

            if "type" in item_data["details"]:
                item.type = item_data["details"]["type"]
            else:
                item.type = equipment_tab_item["slot"]

            if "stats" in equipment_tab_item:
                stats_id = equipment_tab_item["stats"]["id"]
                stats.add_attributes(item.slot, stats=equipment_tab_item["stats"])
            elif "infix_upgrade" in item_data["details"]:
                stats_id = item_data["details"]["infix_upgrade"]["id"]
                stats.add_attributes(item.slot, infix_upgrade=item_data["details"]["infix_upgrade"])
            else:
                for equipment_item in char_data["equipment"]:
                    if item.item_id == equipment_item["id"] and equipment_tab_items["tab"] in equipment_item["tabs"] and "stats" in equipment_item:
                        stats_id = equipment_item["stats"]["id"]
                        stats.add_attributes(item.slot, stats=equipment_item["stats"])
                        break
                else:
                    stats_id = None

            if stats_id:
                stats_data = await self.get_item_stats(stats_id)
                item.stats = stats_data["name"]
            else:
                item.stats = "none"

            if "upgrades" in equipment_tab_item:
                for upgrade_data in equipment_tab_item["upgrades"]:
                    item.add_upgrade((await self.get_item(upgrade_data))["name"])

            if "infusions" in equipment_tab_item:
                for infusion in equipment_tab_item["infusions"]:
                    infusion_data = await self.get_item(infusion)
                    stats.add_attributes(item.slot, infix_upgrade=infusion_data["details"]["infix_upgrade"])
            else:
                for equipment_item in char_data["equipment"]:
                    if item.item_id == equipment_item["id"] and equipment_tab_items["tab"] in equipment_item["tabs"] and "infusions" in equipment_item:
                        for infusion in equipment_item["infusions"]:
                            infusion_data = await self.get_item(infusion)
                            stats.add_attributes(item.slot, infix_upgrade=infusion_data["details"]["infix_upgrade"])
                        break

            equipment.add_item(item)
        equipment.stats = stats
        return equipment
