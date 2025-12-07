import discord
from discord import Interaction, ButtonStyle, Embed
from discord.ext import commands
from discord.ui import View, Modal
from database import Session
from helpers.emotes import get_random_success_emote
from models.application import Application
from models.config import Config
from models.enums.application_status import ApplicationStatus
from models.enums.config_key import ConfigKey
from views.callback_button import CallbackButton
from helpers.logging import log_to_channel


class ReviewView(View):
    def __init__(self, bot: commands.Bot, application_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.application_id = application_id
        self.add_item(CallbackButton(self.accept, label="Accept", style=ButtonStyle.green,
                                     custom_id=f"{self.bot.user.id}-application-{application_id}:accept"))
        self.add_item(CallbackButton(self.deny, label="Deny", style=ButtonStyle.red,
                                     custom_id=f"{self.bot.user.id}-application-{application_id}:deny"))
        self.add_item(CallbackButton(self.compare_stats, label="Compare Stats", style=ButtonStyle.primary,
                                     custom_id=f"{self.bot.user.id}-application-{application_id}:stats"))

    async def accept(self, interaction: Interaction):
        await interaction.response.send_modal(ReviewModal(self.bot, ApplicationStatus.REVIEW_ACCEPTED, self.application_id, self))

    async def deny(self, interaction: Interaction):
        await interaction.response.send_modal(ReviewModal(self.bot, ApplicationStatus.REVIEW_DENIED, self.application_id, self))

    async def compare_stats(self, interaction: Interaction):
        async with Session.begin() as session:
            application = await session.get(Application, self.application_id)
            player_stats = application.equipment.stats.to_dict()
            build_stats = application.build.equipment.stats.to_dict()
            attributes, attributes_player, attributes_build = "", "", ""
            for attribute in player_stats.keys():
                attributes += "**" + attribute + "**\n"
                if attribute in ["Boon Duration", "Critical Chance", "Critical Damage", "Condition Duration"]:
                    attributes_player += str(player_stats[attribute] * 100) + "%\n"
                    attributes_build += str(build_stats[attribute] * 100) + "%\n"
                else:
                    attributes_player += str(player_stats[attribute]) + "\n"
                    attributes_build += str(build_stats[attribute]) + "\n"

            embed = Embed(title="Equipment Stats", colour=application.status.colour)
            embed.description = f"Stats calculated including infusions, without runes\n\n" \
                                f"**User:** {interaction.guild.get_member(application.discord_user_id)}\n" \
                                f"**Build:** {application.build.to_link()}\n"
            embed.add_field(name="Attribute", value=attributes)
            embed.add_field(name="Player", value=attributes_player)
            embed.add_field(name="Build", value=attributes_build)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_error(self, interaction: Interaction, error: Exception, item: discord.ui.Item) -> None:
        # Send message to user and log error
        await interaction.response.send_message(ephemeral=True, content="An unknown error occured. Please try again later.")
        await super().on_error(interaction, error, item)


class ReviewModal(Modal, title="Regular Application"):
    feedback = discord.ui.TextInput(label="Feedback", style=discord.TextStyle.paragraph)

    def __init__(self, bot: commands.Bot, status: ApplicationStatus, application_id: int, parent_view: View):
        self.bot = bot
        self.application_id = application_id
        self.parent_view = parent_view
        self.status = status

        match status:
            case ApplicationStatus.REVIEW_ACCEPTED:
                self.feedback.default = "Congrats on becoming a Regular"
            case ApplicationStatus.REVIEW_DENIED:
                self.feedback.default = "Your Regular application has been denied"

        super().__init__()

    async def on_submit(self, interaction: Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        async with Session.begin() as session:
            application: Application = await session.get(Application, self.application_id)
            # Make sure application has not been handled already
            if application.status != ApplicationStatus.WAITING_FOR_REVIEW:
                reviewer = self.bot.get_user(application.reviewer)
                await interaction.followup.send(content=f"This application has already been {application.status} by {reviewer.mention}", ephemeral=True)
                return

            # Add role and send feedback message
            emote = ""
            ta_channel = interaction.guild.get_channel(int(await Config.get_value(session, ConfigKey.TIER_ASSIGNMENT_CHANNEL_ID)))
            rr_channel = interaction.guild.get_channel(int(await Config.get_value(session, ConfigKey.GEAR_REVIEW_CHANNEL_ID)))
            member = interaction.guild.get_member(application.discord_user_id)
            if self.status == ApplicationStatus.REVIEW_ACCEPTED:
                role = interaction.guild.get_role(int(await Config.get_value(session, ConfigKey.T1_ROLE_ID)))
                old_role = interaction.guild.get_role(int(await Config.get_value(session, ConfigKey.T0_ROLE_ID)))
                emote = get_random_success_emote()
                await member.add_roles(role)
                await member.remove_roles(old_role)
            await ta_channel.send(content=f"{member.mention} {self.feedback} {emote}")

            # Update application
            application.status = self.status
            application.reviewer = interaction.user.id

            # Cleanup
            await (await rr_channel.fetch_message(application.review_message_id)).delete()
            application.review_message_id = None
        self.parent_view.stop()
        await interaction.followup.send(content=f"The application has been {self.status}", ephemeral=True)

        # Log
        embed = Embed(title=f"Manual Gear Check: {self.status}", colour=self.status.colour)
        embed.description = f"**ID:** {self.application_id}\n**User:** {member}\n**Reviewer:** {interaction.user.mention}\n"
        embed.add_field(name="Feedback", value=self.feedback)
        await log_to_channel(self.bot, embed)
