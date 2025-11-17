# bot.py
# Discord Quest Bot — Gamified Rebuild
# PostgreSQL/Supabase ready version
# Requires:
#   pip install -U discord.py python-dotenv asyncpg psycopg2-binary
#   Python 3.10+

import asyncio
import os
import datetime
import random
from typing import Optional, List

import discord
from discord.ext import commands
from discord.ui import Button, View, Select
from dotenv import load_dotenv

# --- DATABASE IMPORTS ---
import asyncpg
# =========================
# CONFIG
# =========================

# It is highly recommended to load the token from the environment variable (Render)
load_dotenv()
TOKEN = os.environ.get("TOKEN") or "" # Fallback, but use ENV on Render!
PREFIX = "!"
MILESTONES = [500, 1000, 1500, 2000]

# --- CHANNEL IDS ---
# Replace these with the ID of your desired channels.
NOTIFICATION_CHANNEL_ID = 1420050178450133174 
ANNOUNCEMENT_CHANNEL_ID = 1420049893375869019
WITHDRAWAL_CHANNEL_ID = 1420055834871992473
SLOTS_CHANNEL_ID = 1420169348147843092

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# =========================
# DATABASE (ASYNC/POSTGRESQL)
# =========================
db_pool: asyncpg.Pool = None

async def init_db():
    global db_pool
    DATABASE_URL = os.environ.get("DATABASE_URL")
    
    if not DATABASE_URL:
        print("FATAL: DATABASE_URL environment variable is not set.")
        # Raise an error or exit if the connection is critical
        return

    try:
        # Create a connection pool to manage connections efficiently
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        print("✅ Database pool created successfully.")

        async with db_pool.acquire() as conn:
            # Use execute for non-SELECT statements like CREATE TABLE
            # PostgreSQL uses BIGINT for large integers (Discord IDs) and SERIAL for auto-increment.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    points  INTEGER NOT NULL DEFAULT 0
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id              SERIAL PRIMARY KEY,
                    title           TEXT NOT NULL,
                    points          INTEGER NOT NULL,
                    max_submissions INTEGER NOT NULL,
                    archived        INTEGER NOT NULL DEFAULT 0,
                    role_reward_id  BIGINT,
                    daily_flag      INTEGER NOT NULL DEFAULT 0,
                    type            TEXT NOT NULL DEFAULT 'link',
                    task_link       TEXT,
                    announcement_message_id BIGINT
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS submissions (
                    id            SERIAL PRIMARY KEY,
                    user_id       BIGINT NOT NULL,
                    task_id       INTEGER NOT NULL REFERENCES tasks(id),
                    proof         TEXT,
                    status        TEXT NOT NULL DEFAULT 'pending',
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at   TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id              SERIAL PRIMARY KEY,
                    user_id         BIGINT NOT NULL,
                    bank_name       TEXT NOT NULL,
                    account_number  TEXT NOT NULL,
                    account_name    TEXT NOT NULL,
                    points          INTEGER NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id BIGINT PRIMARY KEY
                )
            """)
        print("✅ Database tables ensured.")

    except Exception as e:
        print(f"FATAL: Failed to connect or initialize database: {e}")
        # The bot will likely not function without a database.
        await bot.close()


# =========================
# HELPERS (ASYNC)
# =========================
async def ensure_user(user_id: int) -> None:
    async with db_pool.acquire() as conn:
        # Use $1 for parameter substitution in asyncpg, and ON CONFLICT DO NOTHING for INSERT OR IGNORE
        await conn.execute(
            "INSERT INTO users (user_id, points) VALUES ($1, 0) ON CONFLICT (user_id) DO NOTHING", 
            user_id
        )

async def get_user_points(user_id: int) -> int:
    async with db_pool.acquire() as conn:
        # fetchrow returns a dict-like Row object or None
        row = await conn.fetchrow("SELECT points FROM users WHERE user_id=$1", user_id)
        return row['points'] if row else 0

async def is_banned(user_id: int) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM banned_users WHERE user_id=$1", user_id)
        return row is not None

def calc_progress_bar(done: int, limit: int, width: int = 12) -> str:
    if limit <= 0:
        return " " * width
    filled = int(round((done / limit) * width))
    filled = max(0, min(filled, width))
    return "█" * filled + " " * (width - filled)

async def task_title_by_id(task_id: int) -> Optional[str]:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT title FROM tasks WHERE id=$1", task_id)
        return row['title'] if row else None

def next_milestones_reached(old_pts: int, new_pts: int, milestones: List[int]) -> List[int]:
    return [m for m in milestones if old_pts < m <= new_pts]

def get_rank(points: int) -> str:
    if points >= 2000: return "👑 Master"
    if points >= 1500: return "🔥 Pro"
    if points >= 1000: return "🌟 Adventurer"
    if points >= 500: return "🚀 Explorer"
    return "🎈 Newbie"

# =========================
# EVENTS
# =========================
@bot.event
async def on_ready():
    # Bug Fix: Ensure on_ready doesn't run multiple times on reconnects
    if not hasattr(bot, 'ready_ran'):
        bot.ready_ran = True
        banner = """
        ============================
          🚀 Quest Bot is ONLINE! 🚀
        ============================
        """
        print(banner)
        # Initialize the database connection pool
        await init_db() 
        print(f"✅ Logged in as {bot.user} (id={bot.user.id})")
    print(f"Bot reconnected or ready. Current status: {bot.user}")

# =========================
# COMMANDS
# =========================

@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    embed = discord.Embed(title="Quest Bot — Commands", color=0x00E5A8)
    embed.description = (
        f"**Players**\n"
        f"• `{PREFIX}tasks` – list active tasks\n"
        f"• `{PREFIX}submit` – submit to a task\n"
        f"• `{PREFIX}profile [@user]` – show profile & task history\n"
        f"• `{PREFIX}leaderboard` – top 10 users\n\n"
        f"**Admins**\n"
        f"• `{PREFIX}admindashboard` – the new admin hub\n"
    )
    await ctx.send(embed=embed)

# ---- Admin: Add Task ----
@bot.command()
@commands.has_permissions(administrator=True)
async def addtask(ctx, title: str, points: int, max_subs: int, ttype: str = "link", role: Optional[discord.Role] = None):
    if ttype not in ("like", "rt", "link"):
        await ctx.send("⚠️ Task type must be one of: like, rt, link.")
        return

    async with db_pool.acquire() as conn:
        # Insert the task and return the ID (PostgreSQL specific)
        row = await conn.fetchrow(
            "INSERT INTO tasks (title, points, max_submissions, role_reward_id, type) VALUES ($1, $2, $3, $4, $5) RETURNING id",
            title, points, max_subs, role.id if role else None, ttype
        )
        tid = row['id'] if row else None
    
    if not tid:
        await ctx.send("❌ Failed to add task to database.")
        return

    await ctx.send(f"✅ Task added (ID {tid}): **{title}** — {points} pts, max {max_subs}, type {ttype}\nPlease reply with the **link** for this task.")

    def check(msg): return msg.author == ctx.author and msg.channel == ctx.channel
    try:
        msg = await bot.wait_for("message", check=check, timeout=120)
        link = msg.content.strip()

        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET task_link=$1 WHERE id=$2", link, tid)
        
        await ctx.send(f"🔗 Task link saved for **{title}**: {link}")

        announce_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        if announce_channel:
            try:
                announcement_msg = await announce_channel.send(f"@everyone, a new task has just dropped! ({title} | {points} points)\n(Type `!tasks` to view the task and submit proof)")
                
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE tasks SET announcement_message_id=$1 WHERE id=$2", announcement_msg.id, tid)

            except Exception as e:
                print(f"Error sending announcement to channel {ANNOUNCEMENT_CHANNEL_ID}: {e}")

    except asyncio.TimeoutError:
        await ctx.send("⌛ Timed out waiting for a link. You can set it manually later.")


# ------------------------
# Task Button callback factory (top-level, reusable)
# ------------------------
last_click = {}
BUTTON_COOLDOWN = 10  # seconds

COOLDOWN_MESSAGES = [
    "⏳ Quest cooldown! Take a sip of water 💧",
    "😅 Whoa {user}, calm down — adventurers need rest too 🛑",
    "🕒 Patience {user}, greatness takes time ⚡",
    "🚦 Too much traffic on **{title}** — wait {sec}s!",
    "😴 Even heroes nap. Try again in {sec}s!",
    "🐢 Slow and steady wins the quest {user}!"
]

def make_task_callback_factory(board_msg: discord.Message, ctx_author: discord.Member):
    """
    Returns an async callback for a task button.
    board_msg: the message object of the tasks board (so we can refresh it later)
    ctx_author: the member who invoked the original !tasks command (keeps the original behavior)
    """
    async def factory(tid: int, title: str, ttype: str, link: Optional[str],
                      max_subs: int, pts: int, emoji: str, star: str):
        async def task_cb(interaction: discord.Interaction):
            if interaction.user != ctx_author:
                await interaction.response.send_message(
                    "⛔ Only the command caller can use this button.",
                    ephemeral=True
                )
                return
            
            # Check if user is banned
            if await is_banned(interaction.user.id):
                await interaction.response.send_message("⛔ You are banned from submitting quests.", ephemeral=True)
                return

            now = datetime.datetime.utcnow()
            key = (interaction.user.id, tid)
            last = last_click.get(key)
            if last and (now - last).total_seconds() < BUTTON_COOLDOWN:
                remaining = BUTTON_COOLDOWN - (now - last).total_seconds()
                msg = random.choice(COOLDOWN_MESSAGES).format(
                    user=interaction.user.mention,
                    title=title,
                    sec=int(remaining)
                )
                await interaction.response.send_message(msg, ephemeral=True)
                return
            last_click[key] = now
            
            # Get the current count of submissions for this task
            async with db_pool.acquire() as conn:
                done_row = await conn.fetchrow(
                    "SELECT COUNT(*) AS cnt FROM submissions WHERE task_id=$1 AND status!='rejected'",
                    tid
                )
                done_count = done_row['cnt'] if done_row else 0

                if done_count >= max_subs:
                    await interaction.response.send_message(
                        f"🚫 Quest **{title}** is full and now closed.",
                        ephemeral=True
                    )
                    await conn.execute("UPDATE tasks SET archived=1 WHERE id=$1", tid)
                    await refresh_task_board(board_msg)
                    return
                
                # Check if the user has already submitted
                existing = await conn.fetchrow(
                    "SELECT id, status FROM submissions WHERE user_id=$1 AND task_id=$2",
                    interaction.user.id, tid
                )
            
            if existing:
                await interaction.response.send_message(
                    f"⚠️ You already submitted for **{title}**.\n"
                    f"📝 Status: `{existing['status']}`",
                    ephemeral=True
                )
                return
            

            intro = discord.Embed(
                title=f"{emoji} Quest: {title}",
                description=f"Type: `{ttype.upper()}`\nReward: **{pts} pts**\n\n"
                            f"Follow the instructions and submit your **link** proof!",
                color=0x32CD32
            )
            task_view = View()
            if link and link.startswith(("http://", "https://")):
                task_link_button = Button(label="Click Here", style=discord.ButtonStyle.link, url=link)
                task_view.add_item(task_link_button)

            await interaction.response.send_message(embed=intro, view=task_view, ephemeral=True)

            proof = None
            try:
                # IMPORTANT: Since cloud hosting uses ephemeral storage, we **ONLY** accept link proof.
                prompt = await interaction.followup.send(f"🔗 {interaction.user.mention}, paste your proof link for **{title}** (3 min timeout).", ephemeral=True)
                
                # Check for any message containing a link
                def check_link(msg): return msg.author.id == interaction.user.id and msg.content.startswith("http") and msg.channel == interaction.channel
                try:
                    msg = await bot.wait_for("message", check=check_link, timeout=180)
                    proof = msg.content.strip()
                    await msg.delete()
                except asyncio.TimeoutError:
                    await interaction.followup.send("⌛ Time’s up! Submission cancelled.", ephemeral=True)
                    return
            except Exception as e:
                try:
                    await interaction.followup.send("❌ An error occurred while collecting proof. Try again.", ephemeral=True)
                except:
                    pass
                print("[collect proof error]", e)
                return

            await ensure_user(interaction.user.id)
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO submissions (user_id, task_id, proof) VALUES ($1, $2, $3)",
                    interaction.user.id, tid, proof
                )
            
            slots_channel = bot.get_channel(SLOTS_CHANNEL_ID)
            if slots_channel:
                try:
                    new_done_count = done_count + 1
                    slots_left = max_subs - new_done_count
                    slots_embed = discord.Embed(
                        title="📢 Quest Progress Update",
                        description=f"**{title}** has {slots_left} slots left!",
                        color=0x32CD32
                    )
                    slots_embed.set_footer(text=f"A new submission was received. {slots_left} slots remaining!")
                    await slots_channel.send(embed=slots_embed)
                except Exception as e:
                    print(f"Error sending slot announcement to channel {SLOTS_CHANNEL_ID}: {e}")

            success = discord.Embed(
                title="📥 Submission Sent!",
                description=f"✨ **{title}**\nYour proof is pending admin review.\n\n"
                            f"✅ Once approved, you’ll earn your points!",
                color=0xFFD700
            )
            success.set_footer(text="Keep grinding quests 💪")
            await interaction.followup.send(embed=success, ephemeral=True)

            await refresh_task_board(board_msg)

        return task_cb
    return factory

# ---- Public: Tasks ----
@bot.command(name="tasks")
async def tasks_cmd(ctx: commands.Context):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, points, max_submissions, type, daily_flag, task_link FROM tasks WHERE archived=0"
        )

    if not rows:
        await ctx.send("⚠️ No active quests right now.")
        return

    embed = discord.Embed(
        title="📋 Available Quests",
        description="Pick a quest below and submit your proof directly!",
        color=0x00B8FF
    )

    view = View()

    # Send a temporary message to get a message object for refreshing
    board_msg = await ctx.send(embed=embed, view=view)
    factory = make_task_callback_factory(board_msg, ctx.author)
    
    # We need a new view to attach callbacks to
    new_view = View() 
    
    type_emojis = {"like": "👍", "rt": "🔁", "link": "🔗"}

    for r in rows[:10]:
        tid, title, pts, max_subs, ttype, daily, link = r['id'], r['title'], r['points'], r['max_submissions'], r['type'], r['daily_flag'], r['task_link']
        
        # Calculate the number of "done" submissions
        async with db_pool.acquire() as conn:
            done_row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM submissions WHERE task_id=$1 AND status!='rejected'",
                tid
            )
            done = done_row['cnt'] if done_row else 0
        
            if done >= max_subs:
                await conn.execute("UPDATE tasks SET archived=1 WHERE id=$1", tid)
                continue

        star = " ⭐" if daily else ""
        emoji = type_emojis.get(ttype, "🎯")
        button_label = f"{emoji} {title} ({pts} pts){star} [{done}/{max_subs}]"

        cb = await factory(tid, title, ttype, link, max_subs, pts, emoji, star)
        btn = Button(label=button_label, style=discord.ButtonStyle.primary)
        btn.callback = cb
        new_view.add_item(btn)

    await board_msg.edit(embed=embed, view=new_view)

async def refresh_task_board(message: discord.Message):
    """Rebuild the board view after submission so counters update immediately."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, points, max_submissions, type, daily_flag, task_link FROM tasks WHERE archived=0"
        )
    
    view = View()
    type_emojis = {"like": "👍", "rt": "🔁", "link": "🔗"}

    ctx_author = message.interaction.user if message.interaction else (message.guild.owner if message.guild else None)

    if not ctx_author:
        # Cannot proceed without a valid context author to bind the factory
        return

    factory = make_task_callback_factory(message, ctx_author)

    for r in rows[:10]:
        tid, title, pts, max_subs, ttype, daily, link = r['id'], r['title'], r['points'], r['max_submissions'], r['type'], r['daily_flag'], r['task_link']
        
        async with db_pool.acquire() as conn:
            done_row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM submissions WHERE task_id=$1 AND status!='rejected'",
                tid
            )
            done = done_row['cnt'] if done_row else 0
        
            if done >= max_subs:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE tasks SET archived=1 WHERE id=$1", tid)
                continue
            
        star = " ⭐" if daily else ""
        emoji = type_emojis.get(ttype, "🎯")
        btn_label = f"{emoji} {title} ({pts} pts){star} [{done}/{max_subs}]"
        cb = await factory(tid, title, ttype, link, max_subs, pts, emoji, star)
        btn = Button(label=btn_label, style=discord.ButtonStyle.primary)
        btn.callback = cb
        view.add_item(btn)

    try:
        # Check if view is empty before editing, to prevent discord.HTTPException: Must be an interaction or an original response to a non-interaction
        if view.children:
            await message.edit(view=view)
        else:
            await message.edit(embed=message.embeds[0], view=None, content="⚠️ No active quests right now.")
            
    except Exception as e:
        print(f"[refresh_task_board error] {e}")

# ---- Public: Profile with Next Page (UPDATED FOR GAMIFIED HISTORY) ----
class TaskHistoryView(discord.ui.View):
    def __init__(self, member: discord.Member, total_submissions: int, profile_embed: discord.Embed, main_profile_view: discord.ui.View, page: int = 0, *, timeout=180):
        super().__init__(timeout=timeout)
        self.member = member
        self.total_submissions = total_submissions
        self.submissions_per_page = 10
        self.page = page
        self.profile_embed = profile_embed
        self.main_profile_view = main_profile_view
        
        self.total_pages = (self.total_submissions + self.submissions_per_page - 1) // self.submissions_per_page
        
        self.back_to_profile_button = Button(label="⬅️ Back to Profile", style=discord.ButtonStyle.secondary)
        self.back_to_profile_button.callback = self.on_back_to_profile_click
        self.add_item(self.back_to_profile_button)

        self.previous_page_button = Button(label="Previous Page", style=discord.ButtonStyle.secondary, disabled=(self.page <= 0))
        self.next_page_button = Button(label="Next Page", style=discord.ButtonStyle.secondary, disabled=(self.page >= self.total_pages - 1))
        
        self.previous_page_button.callback = self.on_previous_page_click
        self.next_page_button.callback = self.on_next_page_click
        
        if self.total_pages > 1:
            self.add_item(self.previous_page_button)
            self.add_item(self.next_page_button)

    async def build_history_embed(self) -> discord.Embed:
        offset = self.page * self.submissions_per_page
        
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT s.status, t.title, t.points, s.reviewed_at
                FROM submissions s 
                JOIN tasks t ON t.id = s.task_id 
                WHERE s.user_id = $1 
                AND s.status IN ('approved', 'rejected')
                ORDER BY s.id DESC 
                LIMIT $2 OFFSET $3
            """, self.member.id, self.submissions_per_page, offset)

        history_lines = []
        for r in rows:
            status_emoji = '✅' if r['status'] == 'approved' else '❌'
            status_text = 'Approved' if r['status'] == 'approved' else 'Rejected'
            points_text = f" (+{r['points']} pts)" if r['status'] == 'approved' else ""
            
            # Format datetime object from reviewed_at
            reviewed_at_str = r['reviewed_at'].strftime("%b %d, %Y") if r['reviewed_at'] else "N/A"
            
            history_lines.append(
                f"{status_emoji} **{r['title']}**\n"
                f"↳ Status: `{status_text}` {points_text}\n"
                f"↳ Reviewed: `{reviewed_at_str}`"
            )

        history_description = "\n\n".join(history_lines) or "_No approved or rejected tasks yet._"
        
        embed = discord.Embed(
            title=f"📜 {self.member.display_name}'s Quest History", 
            description=history_description, 
            color=0x5B9BFF
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages}")
        
        return embed
    
    async def on_back_to_profile_click(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.profile_embed, view=self.main_profile_view)

    async def on_previous_page_click(self, interaction: discord.Interaction):
        self.page -= 1
        await self.update_view(interaction)
    
    async def on_next_page_click(self, interaction: discord.Interaction):
        self.page += 1
        await self.update_view(interaction)

    async def update_view(self, interaction: discord.Interaction):
        self.previous_page_button.disabled = self.page <= 0
        self.next_page_button.disabled = self.page >= self.total_pages - 1
        
        new_embed = await self.build_history_embed()
        await interaction.response.edit_message(embed=new_embed, view=self)

@bot.command(name="profile")
async def profile_cmd(ctx, member: Optional[discord.Member] = None):
    member = member or ctx.author
    await ensure_user(member.id)

    pts = await get_user_points(member.id)

    async with db_pool.acquire() as conn:
        total_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM submissions WHERE user_id=$1", member.id)
        approved_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM submissions WHERE user_id=$1 AND status='approved'", member.id)
        rejected_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM submissions WHERE user_id=$1 AND status='rejected'", member.id)
        total_completed_tasks_row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM submissions WHERE user_id = $1 AND status IN ('approved', 'rejected')",
            member.id
        )

    total = total_row['cnt'] if total_row else 0
    approved = approved_row['cnt'] if approved_row else 0
    rejected = rejected_row['cnt'] if rejected_row else 0
    total_completed_tasks = total_completed_tasks_row['cnt'] if total_completed_tasks_row else 0


    profile_embed = discord.Embed(title=f"{member.display_name}'s Profile", color=0x00E676)
    profile_embed.set_thumbnail(url=member.display_avatar.url)
    
    profile_embed.add_field(name="🏆 Points", value=f"**{pts}**", inline=True)
    profile_embed.add_field(name="🎖 Rank", value=f"**{get_rank(pts)}**", inline=True)
    profile_embed.add_field(name="📊 Total Quests", value=f"**{total}**", inline=True)
    profile_embed.add_field(name="✅ Approved Quests", value=f"**{approved}**", inline=True)
    profile_embed.add_field(name="❌ Failed Quests", value=f"**{rejected}**", inline=True)

    main_profile_view = View()
    
    # Add the Withdraw button if the profile belongs to the command invoker
    if ctx.author.id == member.id:
        withdraw_button = Button(label="💰 Withdraw Points", style=discord.ButtonStyle.success)
        async def withdraw_cb(interaction: discord.Interaction):
            
            current_points = await get_user_points(interaction.user.id)
            if current_points < 1000:
                await interaction.response.send_message("🚫 You must have at least **1000 points** to withdraw.", ephemeral=True)
                return

            await interaction.response.send_message("Let's process your withdrawal. What is your **Bank Name**?", ephemeral=True)
            
            details = {}
            
            def check(msg): return msg.author == interaction.user and msg.channel == interaction.channel

            try:
                bank_name_msg = await bot.wait_for("message", check=check, timeout=60.0)
                details['bank_name'] = bank_name_msg.content
                await bank_name_msg.delete(delay=10)
                
                await interaction.followup.send("What is your **Account Number**?", ephemeral=True)
                account_number_msg = await bot.wait_for("message", check=check, timeout=60.0)
                details['account_number'] = account_number_msg.content
                await account_number_msg.delete(delay=10)

                await interaction.followup.send("What is your **Account Name**?", ephemeral=True)
                account_name_msg = await bot.wait_for("message", check=check, timeout=60.0)
                details['account_name'] = account_name_msg.content
                await account_name_msg.delete(delay=10)

                await interaction.followup.send("How many **points** do you want to withdraw? (Minimum 1000)", ephemeral=True)
                points_msg = await bot.wait_for("message", check=check, timeout=60.0)
                details['points'] = points_msg.content
                await points_msg.delete(delay=10)

            except asyncio.TimeoutError:
                await interaction.followup.send("⌛ Withdrawal process timed out. Please try again.", ephemeral=True)
                return

            try:
                points_to_withdraw = int(details['points'])
                if points_to_withdraw <= 0:
                    await interaction.followup.send("⚠️ Please enter a valid number of points.", ephemeral=True)
                    return
                if points_to_withdraw < 1000:
                    await interaction.followup.send("🚫 The minimum withdrawal amount is **1000 points**.", ephemeral=True)
                    return

            except ValueError:
                await interaction.followup.send("⚠️ Please enter a valid number of points.", ephemeral=True)
                return

            if points_to_withdraw > current_points:
                await interaction.followup.send(f"🚫 You only have {current_points} points. You can't withdraw more than you have!", ephemeral=True)
                return
            
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET points = points - $1 WHERE user_id=$2", points_to_withdraw, interaction.user.id)

                row = await conn.fetchrow(
                    "INSERT INTO withdrawals (user_id, bank_name, account_number, account_name, points, status) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
                    interaction.user.id, details['bank_name'], details['account_number'], details['account_name'], points_to_withdraw, 'pending'
                )
                withdrawal_id = row['id']

            await interaction.followup.send(f"✅ Withdrawal request for **{points_to_withdraw} points** sent! Your new balance is **{current_points - points_to_withdraw} points**.", ephemeral=True)

            announce_channel = bot.get_channel(WITHDRAWAL_CHANNEL_ID)
            if announce_channel:
                try:
                    embed = discord.Embed(
                        title="💰 New Withdrawal Request",
                        description=f"A new withdrawal request has been submitted by <@{interaction.user.id}>.",
                        color=0xFFD700
                    )
                    embed.add_field(name="Points", value=str(points_to_withdraw), inline=True)
                    embed.add_field(name="Status", value="Pending", inline=True)
                    embed.set_footer(text=f"Request ID: {withdrawal_id}")
                    
                    admin_view = View()
                    view_details_btn = Button(label="View Details", style=discord.ButtonStyle.primary)
                    approve_btn = Button(label="✅ Approve", style=discord.ButtonStyle.success)
                    
                    async def view_details_cb(admin_inter: discord.Interaction):
                        if not admin_inter.user.guild_permissions.administrator:
                            await admin_inter.response.send_message("⛔ You must be an administrator to view this.", ephemeral=True)
                            return
                        
                        async with db_pool.acquire() as conn:
                            row = await conn.fetchrow("SELECT * FROM withdrawals WHERE id=$1", withdrawal_id)
                        
                        if not row:
                            await admin_inter.response.send_message("⚠️ Withdrawal request not found.", ephemeral=True)
                            return
                            
                        details_embed = discord.Embed(title=f"Withdrawal Request #{withdrawal_id} Details", color=0x00B8FF)
                        details_embed.add_field(name="User", value=f"<@{row['user_id']}>", inline=False)
                        details_embed.add_field(name="Points", value=str(row['points']), inline=False)
                        details_embed.add_field(name="Bank Name", value=row['bank_name'], inline=False)
                        details_embed.add_field(name="Account Name", value=row['account_name'], inline=False)
                        details_embed.add_field(name="Account Number", value=row['account_number'], inline=False)
                        details_embed.add_field(name="Status", value=row['status'], inline=False)
                        
                        await admin_inter.response.send_message(embed=details_embed, ephemeral=True)

                    async def approve_withdrawal_cb(admin_inter: discord.Interaction):
                        if not admin_inter.user.guild_permissions.administrator:
                            await admin_inter.response.send_message("⛔ You must be an administrator to approve this.", ephemeral=True)
                            return
                        
                        async with db_pool.acquire() as conn:
                            await conn.execute("UPDATE withdrawals SET status='completed' WHERE id=$1", withdrawal_id)
                        
                        approved_embed = discord.Embed(
                            title="✅ Withdrawal Approved",
                            description=f"Request **#{withdrawal_id}** for **{points_to_withdraw} points** has been approved.",
                            color=0x00CC66
                        )
                        approved_embed.set_footer(text=f"Approved by {admin_inter.user.display_name}")
                        
                        admin_view.children[0].disabled = True
                        admin_view.children[1].disabled = True
                        
                        await admin_inter.response.edit_message(embed=approved_embed, view=admin_view)

                    view_details_btn.callback = view_details_cb
                    approve_btn.callback = approve_withdrawal_cb
                    
                    admin_view.add_item(view_details_btn)
                    admin_view.add_item(approve_btn)

                    await announce_channel.send(embed=embed, view=admin_view)
                except Exception as e:
                    print(f"Error sending withdrawal notification to channel {WITHDRAWAL_CHANNEL_ID}: {e}")
    
    withdraw_button.callback = withdraw_cb
    main_profile_view.add_item(withdraw_button)
    
    if total_completed_tasks > 0:
        next_btn = Button(label="➡️ View Task History", style=discord.ButtonStyle.primary)

        async def next_cb(interaction: discord.Interaction):
            if interaction.user.id != member.id and not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("⛔ You can only view your own history.", ephemeral=True)
                return

            history_view = TaskHistoryView(member, total_completed_tasks, profile_embed=profile_embed, main_profile_view=main_profile_view)
            initial_embed = await history_view.build_history_embed()
            
            await interaction.response.edit_message(embed=initial_embed, view=history_view)

        next_btn.callback = next_cb
        main_profile_view.add_item(next_btn)
    
    await ctx.send(embed=profile_embed, view=main_profile_view)

# ---- Public: Leaderboard ----
@bot.command(name="leaderboard")
async def leaderboard(ctx: commands.Context, limit: int = 10):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, points FROM users ORDER BY points DESC LIMIT $1",
            limit
        )

    if not rows:
        await ctx.send("⚠️ No users yet.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []

    for i, r in enumerate(rows, start=1):
        user = ctx.guild.get_member(r["user_id"]) or bot.get_user(r["user_id"])
        name = user.display_name if hasattr(user, "display_name") else (user.name if user else f"User {r['user_id']}")
        emoji = medals[i - 1] if i <= len(medals) else f"#{i}"
        lines.append(f"{emoji} **{name}** — {r['points']} pts")
    
    # Get the user's rank and points
    async with db_pool.acquire() as conn:
        user_rows = await conn.fetch("SELECT user_id, points FROM users ORDER BY points DESC")
    
    rank = next((i + 1 for i, rr in enumerate(user_rows) if rr["user_id"] == ctx.author.id), None)
    user_points = next((rr["points"] for rr in user_rows if rr["user_id"] == ctx.author.id), 0)

    embed = discord.Embed(
        title="🏆 Quest Leaderboard",
        description="\n".join(lines),
        color=0xFFD700
    )
    embed.add_field(name="\u200B", value="⚡──────────⚡", inline=False)

    if rank:
        embed.add_field(
            name="⭐ Your Rank",
            value=f"You are **#{rank}** with **{user_points} pts**",
            inline=False
        )
    else:
        embed.add_field(
            name="⭐ Your Rank",
            value="You haven’t scored any points yet. Start completing tasks! 🚀",
            inline=False
        )

    embed.set_footer(text="Keep grinding those quests 💪")
    await ctx.send(embed=embed)

# ---- Review dashboard & review commands ----
def progress_bar(current, total, length=10):
    if total == 0:
        return "▱" * length
    filled = int(length * current / max(1, total))
    return "▰" * filled + "▱" * (length - filled)

async def build_review_embed():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT 
                t.title,
                t.id,
                COUNT(s.id) AS pending_count
            FROM tasks t
            LEFT JOIN submissions s ON s.task_id = t.id AND s.status='pending'
            GROUP BY t.id
            HAVING COUNT(s.id) > 0
        """)

        if not rows:
            embed = discord.Embed(
                title="🎯 Review Queue Dashboard",
                description="🎉 All clear! No pending submissions. Great job, mods 🚀",
                color=0x00CC66
            )
            return embed

        lines = []
        medal = ["🥇", "🥈", "🥉"]
        # Convert rows (list of asyncpg.Record) to a standard list of dicts for sorting
        sorted_rows = sorted(rows, key=lambda r: r['pending_count'], reverse=True)

        for i, r in enumerate(sorted_rows):
            total_submissions_row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt FROM submissions WHERE task_id=$1",
                r['id']
            )
            total_submissions_for_task = total_submissions_row['cnt'] if total_submissions_row else 0

            bar = progress_bar(r['pending_count'], total_submissions_for_task)
            emoji = medal[i] if i < len(medal) else "🔸"
            lines.append(
                f"{emoji} **{r['title']}**\n"
                f"Pending: `{r['pending_count']}` / {total_submissions_for_task} {bar}"
            )

        embed = discord.Embed(
            title="🎯 Review Queue Dashboard",
            description="\n\n".join(lines),
            color=0xFF4500
        )
        embed.set_footer(text="Keep the reviews flowing, admins! 💪")
        return embed

@bot.command(name="reviewstats")
@commands.has_permissions(administrator=True)
async def review_stats(ctx: commands.Context):
    embed = await build_review_embed()

    refresh_btn = Button(label="🔄 Refresh", style=discord.ButtonStyle.primary)

    async def refresh_cb(interaction: discord.Interaction):
        new_embed = await build_review_embed()
        await interaction.response.edit_message(embed=new_embed, view=view)

    refresh_btn.callback = refresh_cb
    view = View()
    view.add_item(refresh_btn)

    msg = await ctx.send(embed=embed, view=view)

    async def auto_refresh():
        try:
            while True:
                await asyncio.sleep(30)
                new_embed = await build_review_embed()
                try:
                    await msg.edit(embed=new_embed, view=view)
                except Exception:
                    pass
        except Exception as e:
            print(f"[AutoRefresh stopped] {e}")

    # Use a safe way to check if the task is already running, though for this example, creating it once is fine.
    bot.loop.create_task(auto_refresh())

@bot.command(name="review")
@commands.has_permissions(administrator=True)
async def review_cmd(ctx: commands.Context):
    async with db_pool.acquire() as conn:
        subs = await conn.fetch(
            """
            SELECT s.id, s.user_id, s.proof, s.task_id, t.title
            FROM submissions s 
            JOIN tasks t ON s.task_id = t.id
            WHERE s.status='pending' 
            LIMIT 25
            """
        )

    if not subs:
        await ctx.send("🎉 No pending submissions.")
        return

    options = [
        discord.SelectOption(
            label=f"#{r['id']} | {r['title']} from {ctx.guild.get_member(r['user_id']).display_name if ctx.guild.get_member(r['user_id']) else r['user_id']}",
            value=str(r["id"])
        )
        for r in subs
    ]

    class PendingSelect(Select):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
        
        async def callback(self, interaction: discord.Interaction):
            sid = int(self.values[0])
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT s.*, t.title, t.points AS task_points
                    FROM submissions s
                    JOIN tasks t ON s.task_id = t.id
                    WHERE s.id=$1
                """, sid)
            
            if not row or row['status'] != 'pending':
                await interaction.response.send_message("Submission is no longer pending or not found.", ephemeral=True)
                return

            embed = discord.Embed(title=f"📜 Review Submission #{sid} for '{row['title']}'", color=0x3494DB)
            embed.add_field(name="User", value=f"<@{row['user_id']}>", inline=True)
            embed.add_field(name="Task ID", value=f"#{row['task_id']}", inline=True)

            file = None
            
            # Since local file storage is removed, we only display the link.
            proof_link = row["proof"]
            if proof_link and proof_link.startswith(("http://", "https://")):
                embed.add_field(name="Proof", value=f"Proof link: [Click Here]({proof_link})", inline=False)
            else:
                embed.add_field(name="Proof", value="⚠️ Proof link not provided or invalid.", inline=False)

            approve_btn = Button(label="✅ Approve", style=discord.ButtonStyle.success)
            reject_btn = Button(label="❌ Reject", style=discord.ButtonStyle.danger)
            
            async def approve_cb(btn_inter: discord.Interaction):
                task_points = row['task_points']
                user_id = row['user_id']

                async with db_pool.acquire() as conn:
                    # Perform both updates in the same block for efficiency/atomicity
                    await conn.execute("UPDATE submissions SET status='approved', reviewed_at=CURRENT_TIMESTAMP WHERE id=$1", sid)
                    await conn.execute("UPDATE users SET points = points + $1 WHERE user_id=$2", task_points, user_id)
                
                notification_channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
                if notification_channel:
                    try:
                        task_title = await task_title_by_id(row['task_id'])
                        await notification_channel.send(f"🎉 Hey <@{user_id}>, your submission for **{task_title}** has been **approved**! You earned **{task_points}** points. 🏆")
                    except Exception as e:
                        print(f"Error sending approval notification: {e}")
                
                await btn_inter.response.edit_message(content=f"✅ Approved submission #{sid} and awarded {task_points} points to <@{user_id}>.", view=None, embed=None)

            async def reject_cb(btn_inter: discord.Interaction):
                user_id = row['user_id']
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE submissions SET status='rejected', reviewed_at=CURRENT_TIMESTAMP WHERE id=$1", sid)

                notification_channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
                if notification_channel:
                    try:
                        task_title = await task_title_by_id(row['task_id'])
                        await notification_channel.send(f"❌ <@{user_id}>, your submission for **{task_title}** has been **rejected**. Please check the task details and try again.")
                    except Exception as e:
                        print(f"Error sending rejection notification: {e}")

                await btn_inter.response.edit_message(content=f"❌ Rejected submission #{sid}.", view=None, embed=None)

            approve_btn.callback = approve_cb
            reject_btn.callback = reject_cb

            v = View()
            v.add_item(approve_btn)
            v.add_item(reject_btn)
            
            kwargs = {"embed": embed, "view": v, "ephemeral": True}
            
            await interaction.response.send_message(**kwargs)

    v = View()
    v.add_item(PendingSelect(placeholder="Pick submission to review", options=options, min_values=1, max_values=1))
    await ctx.send("📌 Select a submission to review:", view=v)

# =========================
# NEW ADMIN DASHBOARD
# =========================

# --- MODALS ---
class AddTaskModal(discord.ui.Modal, title="Add a New Quest"):
    title_input = discord.ui.TextInput(label="Quest Title", placeholder="e.g., Follow us on X", min_length=5)
    points_input = discord.ui.TextInput(label="Points", placeholder="e.g., 50", min_length=1)
    max_subs_input = discord.ui.TextInput(label="Max Submissions", placeholder="e.g., 100", min_length=1)
    type_input = discord.ui.TextInput(label="Type (link/like/rt)", placeholder="e.g., link", min_length=2)
    link_input = discord.ui.TextInput(label="Quest Link", placeholder="e.g., https://discord.com", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            points = int(self.points_input.value)
            max_subs = int(self.max_subs_input.value)
            ttype = self.type_input.value.lower()
            if ttype not in ("link", "like", "rt"):
                raise ValueError("Invalid task type.")
            
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO tasks (title, points, max_submissions, type, task_link) VALUES ($1, $2, $3, $4, $5) RETURNING id",
                    self.title_input.value, points, max_subs, ttype, self.link_input.value
                )
                tid = row['id']
            
            await interaction.response.send_message("✅ Quest added successfully! Announcing now...", ephemeral=True)
            
            announce_channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            if announce_channel:
                announcement_msg = await announce_channel.send(f"@everyone, a new task has just dropped! ({self.title_input.value} | {points} points)\n(Type `!tasks` to view the task and submit proof)")
                
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE tasks SET announcement_message_id=$1 WHERE id=$2", announcement_msg.id, tid)

        except ValueError:
            await interaction.response.send_message("⚠️ Please enter valid numbers for points and max submissions, and a valid type (link/like/rt).", ephemeral=True)
        except Exception as e:
            print(f"Error adding task: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)


class BanUserModal(discord.ui.Modal, title="Ban a User from Submitting Quests"):
    user_id_input = discord.ui.TextInput(label="User ID to Ban", placeholder="e.g., 1234567890", min_length=18)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_id_input.value)
            async with db_pool.acquire() as conn:
                await conn.execute("INSERT INTO banned_users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
            
            await interaction.response.send_message(f"✅ User ID `{user_id}` has been banned from submitting quests.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("⚠️ Please enter a valid user ID (numbers only).", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

# --- VIEWS ---
class AdminDashboardView(discord.ui.View):
    def __init__(self, *, timeout=180):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="📝 Add Quest", style=discord.ButtonStyle.success, emoji="➕")
    async def add_task_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddTaskModal())
    
    @discord.ui.button(label="🔎 Review Submissions", style=discord.ButtonStyle.primary, emoji="📋")
    async def review_tasks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        # Bug Fix: Correctly call the review_cmd function
        ctx = await bot.get_context(interaction.message)
        await review_cmd(ctx)
        
    @discord.ui.button(label="⛔ Ban User", style=discord.ButtonStyle.danger, emoji="🚫")
    async def ban_user_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BanUserModal())

    @discord.ui.button(label="✍️ Edit/Remove Quests", style=discord.ButtonStyle.secondary, emoji="⚙️")
    async def manage_tasks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with db_pool.acquire() as conn:
            tasks = await conn.fetch("SELECT id, title FROM tasks WHERE archived=0")
        
        if not tasks:
            await interaction.response.send_message("⚠️ No active tasks to manage.", ephemeral=True)
            return

        options = [discord.SelectOption(label=f"#{t['id']} | {t['title']}", value=str(t['id'])) for t in tasks]

        select_menu = Select(placeholder="Choose a quest to manage...", options=options, row=0)

        async def select_callback(interaction: discord.Interaction):
            task_id = int(select_menu.values[0])
            await interaction.response.send_message(f"Selected quest #{task_id}. What would you like to do?", view=ManageTaskView(task_id), ephemeral=True)

        select_menu.callback = select_callback
        
        view = View(timeout=180)
        view.add_item(select_menu)

        await interaction.response.send_message("Select a quest to manage from the list below:", view=view, ephemeral=True)

class ManageTaskView(discord.ui.View):
    def __init__(self, task_id: int, *, timeout=180):
        super().__init__(timeout=timeout)
        self.task_id = task_id

    @discord.ui.button(label="❌ Remove Quest", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_task_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with db_pool.acquire() as conn:
            # Delete submissions first due to foreign key constraint
            await conn.execute("DELETE FROM submissions WHERE task_id=$1", self.task_id)
            await conn.execute("DELETE FROM tasks WHERE id=$1", self.task_id)

        await interaction.response.edit_message(content=f"✅ Quest #{self.task_id} and all its submissions have been removed.", view=None)

    @discord.ui.button(label="📦 Archive Quest", style=discord.ButtonStyle.secondary, emoji="📁")
    async def archive_task_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET archived=1 WHERE id=$1", self.task_id)

        await interaction.response.edit_message(content=f"✅ Quest #{self.task_id} has been archived and is no longer visible on the board.", view=None)
    
    @discord.ui.button(label="✏️ Edit Quest", style=discord.ButtonStyle.primary, emoji="📝")
    async def edit_task_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with db_pool.acquire() as conn:
            task = await conn.fetchrow("SELECT title, points, max_submissions, type, task_link FROM tasks WHERE id=$1", self.task_id)
        
        class EditTaskModal(discord.ui.Modal, title=f"Edit Quest #{self.task_id}"):
            def __init__(self, task_data):
                super().__init__()
                self.add_item(discord.ui.TextInput(label="Quest Title", default=task_data['title'], min_length=5))
                self.add_item(discord.ui.TextInput(label="Points", default=str(task_data['points']), min_length=1))
                self.add_item(discord.ui.TextInput(label="Max Submissions", default=str(task_data['max_submissions']), min_length=1))
                self.add_item(discord.ui.TextInput(label="Type (link/like/rt)", default=task_data['type'], min_length=2))
                self.add_item(discord.ui.TextInput(label="Quest Link", default=task_data['task_link'] or "", required=False))
            
            async def on_submit(self, interaction: discord.Interaction):
                try:
                    points = int(self.children[1].value)
                    max_subs = int(self.children[2].value)
                    ttype = self.children[3].value.lower()
                    if ttype not in ("link", "like", "rt"):
                        raise ValueError("Invalid task type.")
                    
                    async with db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE tasks SET title=$1, points=$2, max_submissions=$3, type=$4, task_link=$5 WHERE id=$6",
                            self.children[0].value, points, max_subs, ttype, self.children[4].value, self.task_id
                        )
                    
                    await interaction.response.edit_message(content=f"✅ Quest #{self.task_id} has been updated.", view=None)
                except ValueError:
                    await interaction.response.send_message("⚠️ Please enter valid numbers for points and max submissions.", ephemeral=True)
                except Exception as e:
                    await interaction.response.send_message(f"❌ An error occurred: {e}", ephemeral=True)

        if task:
            await interaction.response.send_modal(EditTaskModal(task))
        else:
            await interaction.response.send_message("Task not found.", ephemeral=True)

@bot.command(name="admindashboard")
@commands.has_permissions(administrator=True)
async def admin_dashboard_cmd(ctx: commands.Context):
    async with db_pool.acquire() as conn:
        pending_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM submissions WHERE status='pending'")
        total_tasks_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM tasks")
        total_users_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM users")
        banned_users_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM banned_users")

    pending_submissions = pending_row['cnt'] if pending_row else 0
    total_tasks = total_tasks_row['cnt'] if total_tasks_row else 0
    total_users = total_users_row['cnt'] if total_users_row else 0
    banned_users = banned_users_row['cnt'] if banned_users_row else 0

    embed = discord.Embed(
        title="🛠️ Admin Dashboard",
        description="Welcome to the quest-master's control panel! Use the buttons below to manage tasks, review submissions, and more.",
        color=0xFFD700
    )
    embed.add_field(name="📋 Pending Submissions", value=f"**{pending_submissions}**", inline=True)
    embed.add_field(name="✅ Total Quests", value=f"**{total_tasks}**", inline=True)
    embed.add_field(name="🧑‍🤝‍🧑 Active Users", value=f"**{total_users}**", inline=True)
    embed.add_field(name="🚫 Banned Users", value=f"**{banned_users}**", inline=True)
    await ctx.send(embed=embed, view=AdminDashboardView())

# -------------------------
# 🌐 Web Server for Keep-Alive (FREE TIER ONLY)
# -------------------------
from flask import Flask
from threading import Thread

# Create the Flask app
app = Flask(__name__)

@app.route('/')
def home():
    """A simple endpoint for the external pinger to hit."""
    return "Bot is alive and running!"

def run_flask_server():
    """Start Flask in a separate thread."""
    # Note: We must use 0.0.0.0 and the port specified by Render (usually 8080 or the PORT env var)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# -------------------------
# ▶ Run Bot (UPDATED)
# -------------------------

# 1. Start the Flask server in a background thread
t = Thread(target=run_flask_server)
t.start()

# 2. Start the Discord bot in the main thread
bot.run(TOKEN)

# =========================
# RUN
# =========================
if __name__ == "__main__":
    bot.run(TOKEN)