import traceback
from io import BytesIO

import discord
from discord import app_commands, Interaction, Embed
from discord.ext import commands
import typing
from sqlalchemy import select, func, desc, delete
from database import Session
from helpers.custom_embed import CustomEmbed
from models.application import Application
from models.boss import Boss
from models.build import Build
from models.config import Config
from models.enums.application_status import ApplicationStatus
from models.enums.config_key import ConfigKey
from models.enums.pools import KillProofPool, BossLogPool
from models.enums.profession import Profession
from models.feedback import FeedbackLevel
from snowcrows import get_sc_build, get_sc_builds
from views.application_overview import ApplicationOverview


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.guild_only
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="init")
    async def init(self, interaction: Interaction):
        info_embed = CustomEmbed(self.bot, title="Regular Application initialization")

        # Check if config is set up correctly
        async with Session.begin() as session:
            fbg = await Config.check(session)
            if fbg.level != FeedbackLevel.SUCCESS:
                await interaction.response.send_message(embed=fbg.to_embed(info_embed), ephemeral=True)
                return

        # Stop old view if it exists
        for view in self.bot.persistent_views:
            if type(view) == ApplicationOverview:
                view.stop()

        embed = Embed(title="Regular Application Bot",
                      description="You can use this bot to apply for the `Regular` role on this server.",
                      colour=discord.Colour.from_rgb(99, 51, 4)
                      )
        embed.set_author(name=self.bot.user.display_name,
                         icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.add_field(name=" ", value="", inline=False)
        embed.add_field(name="How to get your API key",
                        value="1. Open the [API Key Management](https://account.arena.net/applications) and log into your ArenaNet account.\n"
                              "2. Press `New Key`\n"
                              "3. Enter a name for the key\n"
                              "4. Select the `account`, `characters`, `builds` and `progression` permission\n"
                              "5. Press `Create API Key`\n"
                              "__The bot will not store your API key__\n",
                        inline=False)
        embed.add_field(name=" ", value="", inline=False)
        embed.add_field(name="How to apply for Regular",
                        value="1. Press the `Regular` button below to start the application process\n"
                              "2. Enter your API key and the name of the character that you want to apply with.\n"
                              "3. The bot will check your Mastery and how many bosses you have killed\n"
                              "4. Select your equipment template and the build you want to apply for\n"
                              "5. The bot will compare your equipment to the build you selected\n"
                              "6. If your gear is correct the bot will automatically grant you the role\n",
                        inline=False)
        await interaction.channel.send(view=ApplicationOverview(self.bot), embed=embed)
        info_embed.add_field(name="Embed initialized", value="", inline=False)
        await interaction.response.send_message(embed=info_embed, ephemeral=True)

    @commands.command("sync")
    @commands.is_owner()
    async def sync(self, ctx: commands.Context, sync_global: typing.Optional[bool] = False) -> None:
        if sync_global:
            await self.bot.tree.sync()
            await ctx.send("Synced commands globally")
        else:
            self.bot.tree.copy_global_to(guild=ctx.guild)
            await self.bot.tree.sync(guild=ctx.guild)
            await ctx.send("Synced commands to this guild")

    @app_commands.guild_only
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.command(name="builds", description="Get a list of all allowed builds")
    async def builds(self, interaction: Interaction, profession: typing.Optional[Profession]):
        await interaction.response.defer(thinking=True, ephemeral=True)
        professions = []
        if profession:
            professions = [profession]
        else:
            for profession in Profession:
                professions.append(profession)

        builds = ""
        async with Session() as session:
            for profession in professions:
                builds += "Profession: " + profession.name + "\n"
                for build in await Build.from_profession(session, profession):
                    builds += f"{build.to_link()}\n"
                builds += "\n"

        await interaction.followup.send("Builds", file=discord.File(BytesIO(builds.encode(encoding="utf-8")), "builds.txt"), ephemeral=True)

    build = app_commands.Group(name="build", description="Add and remove builds")

    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @build.command(name="add", description="Add a build to the database")
    async def build_add(self, interaction: Interaction, snowcrows_url: str):
        if not snowcrows_url.startswith("https://snowcrows.com"):
            await interaction.response.send_message("Invalid url", ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        async with Session.begin() as session:
            if await Build.find(session, url=snowcrows_url):
                await interaction.followup.send("Build already exists", ephemeral=True)
                return
            build = await get_sc_build(snowcrows_url)
            session.add(build)
        await interaction.followup.send("Build was added", ephemeral=True)

    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @build.command(name="archive", description="Removes the build from the list of allowed builds")
    async def build_archive(self, interaction: Interaction, snowcrows_url: str):
        if not snowcrows_url.startswith("https://snowcrows.com"):
            await interaction.response.send_message("Invalid url", ephemeral=True)
            return

        async with Session.begin() as session:
            build = await Build.find(session, url=snowcrows_url)
            if build:
                await build.archive()
                await interaction.response.send_message("Build was removed", ephemeral=True)
            else:
                await interaction.response.send_message("Build not found", ephemeral=True)

    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @build.command(name="init", description="Populates the database with all recommended and viable builds")
    async def build_init(self, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        errors = ""
        async with Session.begin() as session:
            for profession in Profession:
                urls = await get_sc_builds(profession)
                new_builds = []
                for url in urls:
                    try:
                        build_sc = await get_sc_build(url)
                        new_builds.append(build_sc.name)
                        build = await Build.find(session, name=build_sc.name)
                        # If the build already exists in the DB: check if the gear is the same. if not archive old build
                        if build:
                            fbc = build.equipment.compare(build_sc.equipment)
                            if fbc.level <= FeedbackLevel.SUCCESS:
                                # Don't need to add it again if the gear is the same
                                continue
                            else:
                                await build.archive()
                        session.add(build_sc)
                    except Exception as e:
                        print(f"Error adding build {url}:\n{traceback.format_exc()}")
                        errors += f"Error adding build {url}: {e}\n"

                # Archive builds that are not in the list of new builds
                for build in await Build.from_profession(session, profession):
                    if build.name not in new_builds:
                        await build.archive()

        await interaction.followup.send(f"Added all recommended and viable builds (hand kite builds were ignored)\n{errors}", ephemeral=True)

    @app_commands.guild_only
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.command(name="stats", description="Show some stats about the applications")
    async def stats(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = CustomEmbed(self.bot, title="Application Stats")
        async with Session.begin() as session:
            # Total applications
            stmt = select(func.count(Application.id))
            res = await session.execute(stmt)
            embed.description = f"**Total applications:** {res.scalar()}"

            # Applications per status
            stmt = select(Application.status, func.count(Application.status)).group_by(Application.status)
            res = await session.execute(stmt)
            v = ""
            for r in res.all():
                v += f"{r.status.name}: {r.count}\n"
            embed.add_field(name="Applications per status:", value=v, inline=False)

            # Users with the most reviews
            stmt = select(Application.reviewer, func.count(Application.reviewer).label("count"))\
                .where(Application.status.in_((ApplicationStatus.REVIEW_ACCEPTED, ApplicationStatus.REVIEW_DENIED)))\
                .group_by(Application.reviewer).order_by(desc("count")).limit(5)
            res = await session.execute(stmt)
            v = ""
            for r in res.all():
                user = self.bot.get_user(r.reviewer)
                v += f"{user.mention if user else 'Unknown'}: {r.count}\n"
            embed.add_field(name="Most reviews:", value=v, inline=False)

            # Most popular accepted builds
            stmt = select(Build.name, func.count(Application.id).label("count")).join(Application)\
                .where(Application.status.in_((ApplicationStatus.REVIEW_ACCEPTED, ApplicationStatus.ACCEPTED)))\
                .group_by(Build.name).order_by(desc("count")).limit(10)
            res = await session.execute(stmt)
            v = ""
            for r in res.all():
                v += f"{r.name}: {r.count}\n"
            embed.add_field(name="Most popular accepted builds:", value=v, inline=False)
        await interaction.followup.send(embed=embed)


    boss = app_commands.Group(name="boss", description="Manage the list of bosses")

    @app_commands.guild_only
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    @boss.command(name="list", description="Get a list of all bosses")
    async def bosses_list(self, interaction: Interaction):
        async with Session.begin() as session:
            bosses = await Boss.all(session)
            msg = f"**eiEncounterID, boss_name, is_cm, kp_pool, log_pool, achievement_id**\n"
            for boss in bosses:
                msg += f"{boss.to_csv()}\n"

        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @boss.command(name="init", description="Initialize the bosses in the database")
    async def bosses_init(self, interaction: Interaction):
        async with Session.begin() as session:
            await session.execute(delete(Boss))
            await Boss.init(session)
        await interaction.response.send_message("Bosses initialized", ephemeral=True)


    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @boss.command(name="add", description="Add a boss to the database")
    async def bosses_add(self, interaction: Interaction, ei_encounter_id: int, is_cm: bool, boss_name: str, kp_pool: KillProofPool, log_pool: BossLogPool, achievement_id: int = None):
        if not achievement_id and kp_pool != KillProofPool.NOT_ALLOWED:
            await interaction.response.send_message("Achievement ID can only be empty if the boss is not used for the KP check", ephemeral=True)
            return
        async with Session.begin() as session:
            boss = await Boss.get(session, ei_encounter_id, is_cm)
            if boss:
                await interaction.response.send_message(f"Boss already exists:\n"
                                                        f"{boss}\n\n"
                                                        f"Use `/bosses delete` first to change it",
                                                        ephemeral=True)
                return

            boss = Boss(ei_encounter_id=ei_encounter_id, boss_name=boss_name, is_cm=is_cm, kp_pool=kp_pool, log_pool=log_pool, achievement_id=achievement_id)
            session.add(boss)
        await interaction.response.send_message("Boss added", ephemeral=True)


    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @boss.command(name="delete", description="Delete a boss from the database")
    async def bosses_delete(self, interaction: Interaction, ei_encounter_id: int, is_cm: bool):
        async with Session.begin() as session:
            boss = await Boss.get(session, ei_encounter_id, is_cm)
            if not boss:
                await interaction.response.send_message(f"Boss not found", ephemeral=True)
                return

            await session.execute(delete(Boss).where(Boss.encounter_id == ei_encounter_id).where(Boss.is_cm == is_cm))
        await interaction.response.send_message("Boss deleted", ephemeral=True)


    config = app_commands.Group(name="config", description="Configure the bot")

    @app_commands.guild_only
    @app_commands.guild_only
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    @config.command(name="list", description="Show the current configuration")
    async def config_list(self, interaction: Interaction):
        async with Session.begin() as session:
            config = await Config.all(session)
            msg = ""
            for c in config:
                config_key = ConfigKey[c.key]
                msg += f"**{config_key.value} ({config_key.name}):** {c.value}\n"

        await interaction.response.send_message(msg, ephemeral=True)


    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @config.command(name="init", description="Initialize the config")
    async def config_init(self, interaction: Interaction, is_prod: bool = True):
        async with Session.begin() as session:
            await session.execute(delete(Config))
            await Config.init(session, is_prod)
        await interaction.response.send_message("Config initialized", ephemeral=True)

    @app_commands.guild_only
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @config.command(name="set", description="Set a config value")
    async def config_set(self, interaction: Interaction, key: ConfigKey, value: str):
        async with Session.begin() as session:
            config = await session.get(Config, key.name)
            if not config:
                config = Config(key=key, value=value)
                session.add(config)
            else:
                config.value = value
        await interaction.response.send_message("Config updated", ephemeral=True)
