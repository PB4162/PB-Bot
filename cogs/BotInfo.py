import discord
import datetime
import humanize
import psutil
import sys
import inspect

from discord.ext import commands, menus
from jishaku import Jishaku

from utils import utils
from utils.classes import CustomContext, PB_Bot

# constants

PREFIX_LENGTH_LIMIT = 10
TOTAL_PREFIX_LIMIT = 50


def top5(items: list):
    top5items = zip(items, ["🥇", "🥈", "🥉", "🏅", "🏅"])
    return "\n".join(
        f"{ranking[1]} {ranking[0][0]} ({ranking[0][1]} use{'' if ranking[0][1] == 1 else 's'})"
        for ranking in top5items)


class BotInfo(commands.Cog, name="Bot Info"):
    """
    Commands that display information about the bot.
    """
    def __init__(self, bot: PB_Bot):
        self.bot = bot
        self.rtt_cooldown = commands.CooldownMapping.from_cooldown(1, 30, type=commands.BucketType.channel)
        self.op_codes = {1: "HEARTBEAT",
                         2: "IDENTIFY",
                         3: "PRESENCE_UPDATE",
                         4: "VOICE_STATE_UPDATE",
                         5: "VOICE_PING",
                         6: "RESUME",
                         7: "RECONNECT",
                         8: "REQUEST_GUILD_MEMBERS",
                         9: "INVALID_SESSION",
                         10: "HELLO",
                         11: "HEARTBEAT_ACK"}

    @commands.Cog.listener()
    async def on_socket_response(self, message):
        if message["op"] == 0:
            return self.bot.cache.socketstats.update([message["t"]])
        msg = self.op_codes.get(message["op"], "NONE")
        self.bot.cache.socketstats.update([msg])

    @commands.command(aliases=["up"])
    async def uptime(self, ctx: CustomContext):
        """
        Displays how long the bot has been online for since last restart.
        """
        uptime = datetime.datetime.now() - ctx.bot.start_time
        await ctx.send(f"Bot has been online for **`{humanize.precisedelta(uptime)}`**.")

    @commands.command(usage="[-rtt|--round-trip-time]")
    async def ping(self, ctx: CustomContext, *flags):
        """
        Displays the websocket latency, api response time and the database response time.

        **Flags:**
        `-rtt|--round-trip-time` - If this flag is provided, round-trip time will also be displayed.
        """
        decimal_places = 5

        embed = discord.Embed(title="Pong!", colour=ctx.bot.embed_colour)
        embed.add_field(name="Websocket Latency",
                        value=f"```py\n{ctx.bot.latency * 1000:.{decimal_places}f}ms```")
        embed.add_field(name="API Response Time",
                        value=f"```py\n{(first_ping := await ctx.bot.api_ping(ctx)) * 1000:.{decimal_places}f}ms```")
        embed.add_field(name="Database Ping (postgresql)",
                        value=f"```py\n{await ctx.bot.postgresql_ping() * 1000:.{decimal_places}f}ms```")
        embed.add_field(name="Database Ping (redis)",
                        value=f"```py\n{await ctx.bot.redis_ping() * 1000:.{decimal_places}f}ms```")

        if "-rtt" in flags or "--round-trip-time" in flags:
            # cooldown check
            bucket = self.rtt_cooldown.get_bucket(ctx.message)
            retry_after = bucket.update_rate_limit()
            if retry_after:
                raise commands.CommandOnCooldown(bucket, retry_after)

            rtts = [first_ping] + [await ctx.bot.api_ping(ctx) for _ in range(4)]  # makes 5 api requests instead of 6
            rtt_str = "\n".join(f"Reading {number}: {ms * 1000:{decimal_places}f}ms" for number, ms in enumerate(rtts, start=1))
            embed.insert_field_at(2, name="\u200b", value="\u200b")
            embed.add_field(name="\u200b", value="\u200b")
            embed.add_field(name="Round-Trip Time", value=f"```py\n{rtt_str}```")

        await ctx.send(embed=embed)

    @commands.command()
    async def botinfo(self, ctx: CustomContext):
        """
        Displays information about the bot.
        """
        v = sys.version_info
        p = psutil.Process()
        m = p.memory_full_info()
        top5commands_today = ctx.bot.cache.command_stats["top_commands_today"].most_common(5)
        uptime = datetime.datetime.now() - ctx.bot.start_time
        recent_commits = await ctx.bot.get_recent_commits()
        latencies = {k: f"{v * 1000:.2f}ms" for k, v in zip(
            ["Websocket Latency", "API Response Time", "Database Ping (postgresql)", "Database Ping (redis)"],
            [ctx.bot.latency, await ctx.bot.api_ping(ctx), await ctx.bot.postgresql_ping(), await ctx.bot.redis_ping()]
        )}

        embed = discord.Embed(title="Bot Info", colour=ctx.bot.embed_colour)
        embed.set_thumbnail(url=ctx.bot.user.avatar_url)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.avatar_url)

        embed.add_field(
            name="General",
            value=
            f"• Running discord.py version **{discord.__version__}** on python **{v.major}.{v.minor}.{v.micro}**\n"
            f"• This bot is not sharded and can see **{len(ctx.bot.guilds)}** servers and **{len(ctx.bot.users)}** users\n"
            f"• **{len(ctx.bot.cogs)}** cogs loaded and **{len(ctx.bot.commands)}** commands loaded\n"
            f"• **Uptime since last restart:** {humanize.precisedelta(uptime)}", inline=False)

        embed.add_field(
            name="What's New",
            value="\n".join(f"[`{commit['sha'][:6]}`]({commit['html_url']}) {commit['commit']['message']}"
                            for commit in recent_commits), inline=False)

        embed.add_field(name="Top 5 Commands Today", value=top5(top5commands_today) or "No commands have been used today.")

        embed.add_field(
            name="System",
            value=
            f"• `{p.cpu_percent()}%` cpu\n"
            f"• `{humanize.naturalsize(m.rss)}` physical memory\n"
            f"• `{humanize.naturalsize(m.vms)}` virtual memory\n"
            f"• running on PID `{p.pid}` with `{p.num_threads()}` thread(s)",)

        embed.add_field(
            name="Latency Info",
            value=f"```py\n{utils.padding(latencies, separator=' - ')}```", inline=False)

        await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    async def prefix(self, ctx: CustomContext):
        """
        Shows the prefix or prefixes for the current server.
        """
        if not ctx.guild:
            return await ctx.send("My prefix is always `pb` in direct messages. You can also mention me.")
        cache = await ctx.cache()
        if cache is None or not cache["prefixes"]:
            prefixes = ["pb"]
        else:
            prefixes = cache["prefixes"]
        if len(prefixes) == 1:
            return await ctx.send(f"My prefix for this server is `{prefixes[0]}`")
        await ctx.send(f"My prefixes for this server are `{utils.humanize_list(prefixes)}`")

    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @prefix.command(name="add")
    async def add_(self, ctx: CustomContext, *, prefix: str):
        """
        Add a prefix to the prefix list for the current server. The `manage server` permission is required to use this command.

        `prefix` - The prefix to add.
        """
        if len(prefix) > PREFIX_LENGTH_LIMIT:
            return await ctx.send(f"Sorry, that prefix is too long (>{PREFIX_LENGTH_LIMIT} characters).")

        cache = await ctx.cache()
        if cache is None:
            await ctx.bot.cache.create_guild_info(ctx.guild.id)  # no need to do checks
        else:
            prefixes = cache["prefixes"]
            if prefix in prefixes:
                return await ctx.send(f"`{prefix}` is already a prefix for this server.")
            if len(prefixes) > TOTAL_PREFIX_LIMIT:
                return await ctx.send(f"This server already has {TOTAL_PREFIX_LIMIT} prefixes.")

        await ctx.bot.cache.add_prefix(ctx.guild.id, prefix)
        await ctx.send(f"Added `{prefix}` to the list of server prefixes.")

    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @prefix.command()
    async def remove(self, ctx: CustomContext, *, prefix: str):
        """
        Remove a prefix from the prefix list for the current server. The `manage server` permission is required to use this command.

        `prefix` - The prefix to remove.
        """
        if len(prefix) > PREFIX_LENGTH_LIMIT:
            return await ctx.send(f"Sorry, that prefix is too long (>{PREFIX_LENGTH_LIMIT} characters).")

        cache = await ctx.cache()
        if cache is None or not (prefixes := cache["prefixes"]):
            return await ctx.send("This server doesn't have any custom prefixes.")
        elif prefix not in prefixes:
            return await ctx.send(f"Couldn't find `{prefix}` in the list of prefixes for this server.")

        await ctx.bot.cache.remove_prefix(ctx.guild.id, prefix)
        await ctx.send(f"Removed `{prefix}` from the list of server prefixes.")

    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @prefix.command()
    async def clear(self, ctx: CustomContext):
        """
        Clears the current server's prefix list. The `manage server` permission is required to use this command.
        """
        if (cache := await ctx.cache()) is None or not cache["prefixes"]:
            return await ctx.send("This server doesn't have any custom prefixes.")
        confirm = await utils.Confirm("Are you sure that you want to clear the prefix list for this server?").prompt(ctx)
        if confirm:
            await ctx.bot.cache.clear_prefixes(ctx.guild.id)
            await ctx.send("Cleared the list of server prefixes.")

    @commands.command()
    async def invite(self, ctx: CustomContext):
        """
        Displays my invite link.
        """
        embed = discord.Embed(title="Invite me to your server!", url=ctx.bot.invite_url, colour=ctx.bot.embed_colour)
        await ctx.send(embed=embed)

    @commands.command(aliases=["src"])
    async def source(self, ctx: CustomContext, *, command: str = None):
        """
        View my source code for a specific command.

        `command` - The command to view the source code of (Optional).
        """
        if not command:
            embed = discord.Embed(title="Here is my source code.",
                                  description="Don't forget the license! (A star would also be appreciated ^^)",
                                  url=ctx.bot.github_url, colour=ctx.bot.embed_colour)
            return await ctx.send(embed=embed)

        command = ctx.bot.help_command if command.lower() == "help" else ctx.bot.get_command(command)
        if not command:
            return await ctx.send("Couldn't find command.")
        if isinstance(command.cog, Jishaku):
            return await ctx.send("<https://github.com/Gorialis/jishaku>")

        if isinstance(command, commands.HelpCommand):
            lines, starting_line_num = inspect.getsourcelines(type(command))
            filepath = f"{command.__module__.replace('.', '/')}.py"
        else:
            lines, starting_line_num = inspect.getsourcelines(command.callback.__code__)
            filepath = f"{command.callback.__module__.replace('.', '/')}.py"

        ending_line_num = starting_line_num + len(lines) - 1
        command = "help" if isinstance(command, commands.HelpCommand) else command
        embed = discord.Embed(
            title=f"Here is my source code for the `{command}` command.",
            description="Don't forget the license! (A star would also be appreciated ^^)",
            url=f"https://github.com/PB4162/PB-Bot/blob/master/{filepath}#L{starting_line_num}-L{ending_line_num}",
            colour=ctx.bot.embed_colour)
        await ctx.send(embed=embed)

    @commands.command()
    async def stats(self, ctx: CustomContext):
        """
        Displays the command usage stats.
        """
        top5commands_today = ctx.bot.cache.command_stats["top_commands_today"].most_common(5)
        top5commands_overall = ctx.bot.cache.command_stats["top_commands_overall"].most_common(5)
        top5users_today = [(f"<@!{user_id}>", counter)
                           for user_id, counter in ctx.bot.cache.command_stats["top_users_today"].most_common(5)]
        top5users_overall = [(f"<@!{user_id}>", counter)
                             for user_id, counter in ctx.bot.cache.command_stats["top_users_overall"].most_common(5)]

        embed = discord.Embed(title="Command Stats", colour=ctx.bot.embed_colour)
        embed.add_field(name="Top 5 Commands Today", value=top5(top5commands_today) or "No commands have been used today.")
        embed.add_field(name="Top 5 Users Today", value=top5(top5users_today) or "No one has used any commands today.")
        embed.add_field(name="\u200b", value="\u200b")
        embed.add_field(name="Top 5 Commands Overall", value=top5(top5commands_overall) or "No commands have been used.")
        embed.add_field(name="Top 5 Users Overall", value=top5(top5users_overall) or "No one has used any commands.")
        embed.add_field(name="\u200b", value="\u200b")

        await ctx.send(embed=embed)

    @commands.command()
    async def support(self, ctx: CustomContext):
        """
        Displays my support server's invite link.
        """
        embed = discord.Embed(title=f"Support Server Invite", url=ctx.bot.support_server_invite, colour=ctx.bot.embed_colour)
        await ctx.send(embed=embed)

    @commands.command()
    async def vote(self, ctx: CustomContext):
        """
        Displays my vote link.
        """
        embed = discord.Embed(title="Top.gg Page", description="Remember to leave an honest review. :)",
                              url=ctx.bot.top_gg_url, colour=ctx.bot.embed_colour)
        await ctx.send(embed=embed)

    @commands.command()
    async def socketstats(self, ctx: CustomContext):
        """
        Displays the socketstats.
        """
        menu = menus.MenuPages(
            utils.SocketStatsSource(ctx.bot.cache.socketstats.most_common()),
            clear_reactions_after=True
        )
        await menu.start(ctx)


def setup(bot):
    bot.add_cog(BotInfo(bot))
