import os
import discord
from discord.ext import commands
from cogs.admin_commands import AdminCommands
from gw2.snowcrows import init_builds
from cogs.views.application_overview import ApplicationOverview

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def setup_hook():
    bot.add_view(ApplicationOverview(bot))


@bot.event
async def on_ready():
    await bot.add_cog(AdminCommands(bot))
    await init_builds()

bot.run(os.getenv("DISCORD_TOKEN"))