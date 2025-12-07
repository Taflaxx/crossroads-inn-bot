from discord import Interaction
from sqlalchemy import select
from database import Session
from helpers.embeds import generate_error_embed, get_progress_embed
from helpers.logging import log_to_channel
from models.application import Application
from models.config import Config
from models.enums.application_status import ApplicationStatus
from models.enums.config_key import ConfigKey
from models.enums.role import Role
from models.feedback import *
from api import API
from views.application import ApplicationView
from discord.ext import commands
from views.submit_log import SubmitLogModal


class ApplicationOverview(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Regular [Gearcheck]", style=discord.ButtonStyle.primary, custom_id="persistent_view:t1")
    async def apply_t1(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user already has an open application
        async with Session.begin() as session:
            stmt = select(Application).where(Application.discord_user_id == interaction.user.id) \
                .where(Application.status == ApplicationStatus.WAITING_FOR_REVIEW)
            application = (await session.execute(stmt)).scalar()
            if application:
                response = await interaction.response.send_message(
                    ephemeral=True,
                    content="You already have an open application. Please wait until it has been reviewed.\n\n"
                            "If you want you can close your application by clicking the button below.",
                    view=CloseApplicationView(self.bot, application.id))
                return
            config = await Config.to_dict(session)

        # Check if user already has role
        for role in interaction.user.roles:
            if role.id in [int(config[ConfigKey.T1_ROLE_ID]), int(config[ConfigKey.T2_ROLE_ID]), int(config[ConfigKey.T3_ROLE_ID])]:
                await interaction.response.send_message(ephemeral=True, content="You are already a Regular or above.")
                return
        await interaction.response.send_modal(ApplicationModal(self.bot))

    # Tier 2 and Tier 3 has been retired
    #
    # @discord.ui.button(label="Tier 2 [Log]", style=discord.ButtonStyle.primary, custom_id="persistent_view:submit_log_t2", row=1)
    # async def submit_log_t2(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     async with Session.begin() as session:
    #         t1_role_id = int((await Config.get_value(session, ConfigKey.T1_ROLE_ID)))
    #     for role in interaction.user.roles:
    #         if role.id == t1_role_id:
    #             break
    #     else:
    #         await interaction.response.send_message(ephemeral=True, content="You need to be tier 1 to apply for tier 2.")
    #         return
    #     await interaction.response.send_modal(SubmitLogModal(self.bot, 2, Role.NONE))
    #
    # @discord.ui.button(label="Tier 3: pDPS [Log]", style=discord.ButtonStyle.primary, custom_id="persistent_view:submit_log_t3_pdps", row=2)
    # async def submit_log_t3_pdps(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await self.submit_log_t3(interaction, Role.POWER_DPS)
    #
    # @discord.ui.button(label="Tier 3: cDPS [Log]", style=discord.ButtonStyle.primary, custom_id="persistent_view:submit_log_t3_cdps", row=2)
    # async def submit_log_t3_cdps(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await self.submit_log_t3(interaction, Role.CONDITION_DPS)
    #
    # @discord.ui.button(label="Tier 3: Boon DPS [Log]", style=discord.ButtonStyle.primary, custom_id="persistent_view:submit_log_t3_bdps", row=2)
    # async def submit_log_t3_bdps(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await self.submit_log_t3(interaction, Role.BOON_DPS)
    #
    # @discord.ui.button(label="Tier 3: Heal [Log]", style=discord.ButtonStyle.primary, custom_id="persistent_view:submit_log_t3_heal", row=2)
    # async def submit_log_t3_heal(self, interaction: discord.Interaction, button: discord.ui.Button):
    #     await self.submit_log_t3(interaction, Role.HEAL)
    #
    # async def submit_log_t3(self, interaction: discord.Interaction,role: Role):
    #     async with Session.begin() as session:
    #         config = await Config.to_dict(session)
    #
    #     # Check if user has the correct tier
    #     for user_role in interaction.user.roles:
    #         if user_role.id == int(config[ConfigKey.T2_ROLE_ID]) or user_role.id == int(config[ConfigKey.T3_ROLE_ID]):
    #             break
    #     else:
    #         await interaction.response.send_message(ephemeral=True, content="You need to be at least tier 2 to apply for tier 3.")
    #         return
    #
    #     # Check if user already has the role
    #     for user_role in interaction.user.roles:
    #         if user_role.id == int(config[role.get_config_key()]):
    #             await interaction.response.send_message(ephemeral=True, content=f"You already have the tier 3 {role.value} role.")
    #             return
    #
    #     await interaction.response.send_modal(SubmitLogModal(self.bot, 3, role))

    @discord.ui.button(label="View Progress", style=discord.ButtonStyle.green, custom_id="persistent_view:view_progress", row=3)
    async def view_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with Session.begin() as session:
            await interaction.response.send_message(embed=await get_progress_embed(session, interaction.user), ephemeral=True)

    async def on_error(self, interaction: Interaction, error: Exception, item: discord.ui.Item) -> None:
        # Send message to user and log error
        await interaction.response.send_message(ephemeral=True, content="An unknown error occured. Please try again later.")
        await super().on_error(interaction, error, item)


class CloseApplicationView(discord.ui.View):
    def __init__(self, bot: commands.Bot, application_id: int):
        super().__init__()
        self.bot = bot
        self.application_id = application_id

    @discord.ui.button(label='Close Application', style=discord.ButtonStyle.danger)
    async def close_application(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        # Update application status
        async with Session.begin() as session:
            application = await session.get(Application, self.application_id)
            application.status = ApplicationStatus.CLOSED_BY_APPLICANT

            # Delete review message
            rr_channel = interaction.guild.get_channel(int(await Config.get_value(session, ConfigKey.GEAR_REVIEW_CHANNEL_ID)))
            await (await rr_channel.fetch_message(application.review_message_id)).delete()
            application.review_message_id = None

            # Log to log channel
            embed = Embed(title=f"Application closed by user:", colour=discord.Color.red())
            embed.description = f"**ID:** {self.application_id}\n" \
                                f"**User:** {interaction.guild.get_member(application.discord_user_id)}"
            await log_to_channel(self.bot, embed)

        # Send message to user
        await interaction.response.send_message(ephemeral=True,
                                                content=f"{FeedbackLevel.SUCCESS.emoji} Your application has been "
                                                        f"closed. You can apply again.")


class ApplicationModal(discord.ui.Modal, title="Regular Application"):
    api_key: str = discord.ui.TextInput(label="API Key")
    character: str = discord.ui.TextInput(label="Character Name", min_length=3, max_length=19)

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: Interaction) -> None:
        # Defer to prevent interaction timeout
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Create embed
        embed = Embed(title="Regular Application", color=0x0099ff)
        failed_registration = False

        # Check API Key
        api = API(str(self.api_key))
        key_feedback = await api.check_key()
        embed = key_feedback.to_embed(embed)
        if key_feedback.level == FeedbackLevel.ERROR:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if str(self.character) in await api.get_characters():
            embed.add_field(name=f"{FeedbackLevel.SUCCESS.emoji} Character '{self.character}' found", value="",
                            inline=False)
        else:
            embed.add_field(name=f"{FeedbackLevel.ERROR.emoji} Character '{self.character}' doesn't exist", value="",
                            inline=False)
            failed_registration = True

        # Check masteries
        mastery_feedback = await api.check_mastery()
        embed = mastery_feedback.to_embed(embed)
        if mastery_feedback.level == FeedbackLevel.ERROR:
            failed_registration = True

        # Check KP
        kp_feedback = await api.check_kp(1)
        embed = kp_feedback.to_embed(embed)
        if kp_feedback.level == FeedbackLevel.ERROR:
            failed_registration = True

        if failed_registration:
            embed.colour = discord.Colour.red()
            embed.add_field(name=f"{FeedbackLevel.ERROR.emoji} Please fix these errors and apply again.", value="",
                            inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            embed.colour = discord.Colour.green()
            view = ApplicationView(self.bot, api, str(self.character))
            await view.init()
            response = await interaction.followup.send(embed=embed, ephemeral=True, view=view)
            view.original_message = response

    async def on_error(self, interaction: Interaction, error: Exception) -> None:
        await interaction.followup.send(embed=generate_error_embed(error), ephemeral=True)
        # Log error
        await super().on_error(interaction, error)