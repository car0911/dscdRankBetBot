import discord
from discord.ext import commands
import re
import os
import asyncio
import aiohttp
from aiohttp import web
import pymongo

# ==========================================
# ⚙️ 환경변수
# ==========================================

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
MONGO_URI = os.environ.get("MONGODB_URI")

if not DISCORD_TOKEN:
    raise RuntimeError("🚨 DISCORD_TOKEN 환경변수가 설정되지 않았습니다.")

if not MONGO_URI:
    raise RuntimeError("🚨 MONGODB_URI 환경변수가 설정되지 않았습니다.")


# ==========================================
# 🤖 Discord Bot 설정
# ==========================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

PREFIX = "!"

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents
)


# ==========================================
# 💾 MongoDB 연결 및 초기화
# ==========================================

print("🔄 MongoDB에 연결하는 중...")

try:
    mongo_client = pymongo.MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=10000
    )

    # 실제 연결 확인
    mongo_client.admin.command("ping")

    print("✅ MongoDB 연결 성공!")

except Exception as e:
    raise RuntimeError(f"🚨 MongoDB 연결 실패: {e}")


db_client = mongo_client["discord_bet_bot"]
collection = db_client["bet_data"]


# ==========================================
# 📦 DB에서 데이터 불러오기
# ==========================================

try:
    doc = collection.find_one({"_id": "main_data"})

    if doc:
        teams = doc.get("teams", {})
        user_team = doc.get("user_team", {})

        match_data = doc.get(
            "match_data",
            {
                "is_active": False,
                "target_score": 0,
                "participating_teams": []
            }
        )

        print("✅ 기존 데이터를 MongoDB에서 불러왔습니다.")

    else:
        teams = {}
        user_team = {}

        match_data = {
            "is_active": False,
            "target_score": 0,
            "participating_teams": []
        }

        print("ℹ️ 기존 데이터가 없어 새 데이터로 시작합니다.")

except Exception as e:
    raise RuntimeError(f"🚨 MongoDB 데이터 불러오기 실패: {e}")


# ==========================================
# 💾 데이터 저장
# ==========================================

def save_data():
    try:
        collection.update_one(
            {"_id": "main_data"},
            {
                "$set": {
                    "teams": teams,
                    "user_team": user_team,
                    "match_data": match_data
                }
            },
            upsert=True
        )

    except Exception as e:
        print(f"🚨 MongoDB 저장 실패: {e}")


# ==========================================
# 🧹 전체 데이터 초기화
# ==========================================

def reset_all_data():
    teams.clear()
    user_team.clear()

    match_data["is_active"] = False
    match_data["target_score"] = 0
    match_data["participating_teams"].clear()

    save_data()


# ==========================================
# 🏆 경기 결과 표시
# ==========================================

async def match_result_display(channel):

    if not match_data["participating_teams"]:
        return

    sorted_teams = sorted(
        match_data["participating_teams"],
        key=lambda t: teams[t]["score"],
        reverse=True
    )

    ranks = [
        "Winner",
        "2nd",
        "3rd",
        "4th",
        "5th",
        "6th"
    ]

    result_lines = []

    for i, team_name in enumerate(sorted_teams):

        rank_tag = (
            ranks[i]
            if i < len(ranks)
            else f"{i + 1}th"
        )

        member_names = []

        for uid_str in teams[team_name]["members"]:

            try:
                user = bot.get_user(int(uid_str))

                if user:
                    name = user.display_name
                else:
                    name = "알수없음"

            except Exception:
                name = "알수없음"

            member_names.append(name)

        member_string = " ".join(member_names)

        result_lines.append(
            f"{rank_tag} '{team_name}' "
            f"{member_string} "
            f"({teams[team_name]['score']}점)"
        )

    embed = discord.Embed(
        title="🏆 최종 내기 결과 🏆",
        description="\n\n".join(result_lines),
        color=discord.Color.gold()
    )

    await channel.send(embed=embed)


# ==========================================
# 🌐 Render Health Check 서버
# ==========================================

async def health_check(request):
    return web.Response(
        text="OK",
        status=200
    )


async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health_check
    )

    app.router.add_get(
        "/health",
        health_check
    )

    runner = web.AppRunner(app)

    await runner.setup()

    port = int(
        os.environ.get(
            "PORT",
            "8000"
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"🌐 Health Check 서버가 "
        f"0.0.0.0:{port} 에서 실행 중입니다."
    )


# ==========================================
# 🤖 Bot Ready
# ==========================================

@bot.event
async def on_ready():

    print("=" * 50)
    print(f"✅ Discord 로그인 성공!")
    print(f"🤖 Bot: {bot.user}")
    print(f"🆔 Bot ID: {bot.user.id}")
    print("=" * 50)

    # 웹 서버가 중복 실행되지 않도록 확인
    if not hasattr(bot, "web_server_started"):

        bot.web_server_started = True

        bot.loop.create_task(
            start_web_server()
        )


# ==========================================
# 💬 메시지 처리
# ==========================================

@bot.event
async def on_message(message):

    if message.author == bot.user:
        return

    content_body = message.content.strip()

    # 1. 점수 입력 (-23, +23, 23 등)인지 먼저 확인
    match = re.match(
        r"^([+-]?)(\d+)$",
        content_body
    )

    if match:
        sign = match.group(1)
        num = int(match.group(2))

        # 기호가 '-'이면 음수, 아니면 양수(+, 혹은 기호 생략)
        points = (
            -num
            if sign == "-"
            else num
        )

        user_id_str = str(
            message.author.id
        )

        # 팀 소속 확인
        if user_id_str not in user_team:

            await message.channel.send(
                f"❌ {message.author.mention}님은 "
                f"소속된 팀이 없습니다."
            )

            return

        my_team = user_team[user_id_str]

        # 팀 존재 여부 확인
        if my_team not in teams:

            await message.channel.send(
                "❌ 소속된 팀 정보를 찾을 수 없습니다."
            )

            return

        # 팀 점수 변경
        teams[my_team]["score"] += points

        # 개인 점수 변경
        if user_id_str not in teams[my_team]["members"]:
            teams[my_team]["members"][user_id_str] = 0

        teams[my_team]["members"][user_id_str] += points

        save_data()

        team_score = teams[my_team]["score"]

        my_score = teams[my_team]["members"][user_id_str]

        sign_str = (
            f"+{points}"
            if points > 0
            else f"{points}"
        )

        await message.channel.send(
            f"📈 {my_team} 점수 변동: "
            f"{sign_str}점\n"
            f"> 🏆 팀 총점: {team_score}점 | "
            f"👤 {message.author.display_name} "
            f"개인 점수: {my_score}점"
        )

        # 내기 진행 중인지 확인
        if (
            match_data["is_active"]
            and my_team in match_data["participating_teams"]
        ):

            if team_score >= match_data["target_score"]:

                await message.channel.send(
                    f"\n🎉🎉 축하합니다! "
                    f"'{my_team}' 팀이 "
                    f"목표 점수("
                    f"{match_data['target_score']}점"
                    f")에 가장 먼저 도달했습니다! 🎉🎉"
                )

                await match_result_display(
                    message.channel
                )

                reset_all_data()

                await message.channel.send(
                    "🧹 내기가 종료되어 "
                    "모든 팀이 해산되고 "
                    "점수가 초기화되었습니다."
                )

        return

    # 2. 점수 입력이 아니라면 일반 봇 명령어(!팀생성, !명령어 등) 처리 수행
    await bot.process_commands(message)


# ==========================================
# 👥 팀 생성
# ==========================================

@bot.command(name="팀생성")
async def create_team(
    ctx,
    team_name: str
):

    if team_name in teams:

        await ctx.send(
            f"❌ '{team_name}'(은)는 "
            f"이미 존재하는 팀입니다."
        )

        return

    teams[team_name] = {
        "score": 0,
        "members": {}
    }

    save_data()

    await ctx.send(
        f"✅ '{team_name}' 팀이 생성되었습니다."
    )


# ==========================================
# 👤 팀 등록
# ==========================================

@bot.command(name="팀등록")
async def join_team(
    ctx,
    team_name: str,
    member: discord.Member = None
):

    target_user = (
        member
        if member
        else ctx.author
    )

    user_id_str = str(
        target_user.id
    )

    # 다른 사람 등록은 관리자만 가능
    if (
        member
        and not ctx.author.guild_permissions.administrator
    ):

        await ctx.send(
            "❌ 다른 사용자를 팀에 등록하려면 "
            "관리자 권한이 필요합니다."
        )

        return

    if team_name not in teams:

        await ctx.send(
            f"❌ '{team_name}'(은)는 "
            f"존재하지 않는 팀입니다."
        )

        return

    if user_id_str in user_team:

        current_team = user_team[user_id_str]

        await ctx.send(
            f"❌ {target_user.mention}님은 "
            f"이미 '{current_team}' 팀에 "
            f"소속되어 있습니다."
        )

        return

    teams[team_name]["members"][user_id_str] = 0

    user_team[user_id_str] = team_name

    save_data()

    await ctx.send(
        f"✅ {target_user.mention}님이 "
        f"'{team_name}' 팀에 등록되었습니다."
    )


# ==========================================
# 🔥 내기 시작
# ==========================================

@bot.command(name="내기시작")
async def start_match(
    ctx,
    *args
):

    if len(args) < 2:

        await ctx.send(
            "⚠️ 사용법: "
            "`!내기시작 [팀이름1] "
            "[팀이름2] ... [목표점수]`"
        )

        return

    try:

        target_score = int(
            args[-1]
        )

    except ValueError:

        await ctx.send(
            "❌ 마지막 입력값은 "
            "목표 점수(숫자)여야 합니다."
        )

        return

    target_teams = list(
        args[:-1]
    )

    user_id_str = str(
        ctx.author.id
    )

    my_team = user_team.get(
        user_id_str
    )

    if not my_team:

        await ctx.send(
            "❌ 본인이 소속된 팀이 있어야 "
            "내기를 시작할 수 있습니다."
        )

        return

    if my_team not in target_teams:

        target_teams.append(
            my_team
        )

    for team_name in target_teams:

        if team_name not in teams:

            await ctx.send(
                f"❌ '{team_name}'(은)는 "
                f"존재하지 않는 팀입니다."
            )

            return

    # 중복 제거
    target_teams = list(
        dict.fromkeys(target_teams)
    )

    match_data["is_active"] = True

    match_data["target_score"] = target_score

    match_data["participating_teams"] = target_teams

    save_data()

    await ctx.send(
        f"🔥 내기가 시작되었습니다! 🔥\n"
        f"> ⚔️ 참여 팀: {', '.join(target_teams)}\n"
        f"> 🎯 목표 점수: {target_score}점"
    )


# ==========================================
# 📊 점수 확인
# ==========================================

@bot.command(name="점수")
async def check_score(ctx):

    user_id_str = str(
        ctx.author.id
    )

    my_team = user_team.get(
        user_id_str
    )

    if not my_team:

        await ctx.send(
            "❌ 소속된 팀이 없습니다."
        )

        return

    if my_team not in teams:

        await ctx.send(
            "❌ 팀 데이터를 찾을 수 없습니다."
        )

        return

    team_score = teams[my_team]["score"]

    if (
        match_data["is_active"]
        and my_team in match_data["participating_teams"]
    ):

        left = (
            match_data["target_score"]
            - team_score
        )

        await ctx.send(
            f"📊 [{my_team}] "
            f"현재 팀 점수: {team_score}점 "
            f"(목표까지 {left}점 남음)"
        )

    else:

        await ctx.send(
            f"📊 [{my_team}] "
            f"현재 팀 점수: {team_score}점"
        )


# ==========================================
# 📈 내기 현황
# ==========================================

@bot.command(name="현황")
async def match_status(ctx):

    if not match_data["is_active"]:

        await ctx.send(
            "⚠️ 현재 진행 중인 내기가 없습니다."
        )

        return

    user_id_str = str(
        ctx.author.id
    )

    my_team = user_team.get(
        user_id_str
    )

    target = match_data["target_score"]

    participating_teams = (
        match_data["participating_teams"]
    )

    sorted_teams = sorted(
        participating_teams,
        key=lambda t: teams[t]["score"],
        reverse=True
    )

    embed = discord.Embed(
        title="📈 현재 솔랭 내기 현황",
        color=discord.Color.green()
    )

    for i, team_name in enumerate(sorted_teams):

        team_score = teams[team_name]["score"]

        left = target - team_score

        member_texts = []

        for (
            uid_str,
            personal_score
        ) in teams[team_name]["members"].items():

            try:

                user = ctx.guild.get_member(
                    int(uid_str)
                )

                name = (
                    user.display_name
                    if user
                    else "알수없음"
                )

            except Exception:

                name = "알수없음"

            member_texts.append(
                f"{name} ({personal_score}점)"
            )

        members_str = (
            ", ".join(member_texts)
            if member_texts
            else "팀원 없음"
        )

        diff_str = ""

        if (
            my_team
            and my_team in participating_teams
        ):

            if team_name != my_team:

                my_score = teams[my_team]["score"]

                diff = (
                    team_score
                    - my_score
                )

                sign = (
                    "+"
                    if diff > 0
                    else ""
                )

                diff_str = (
                    f" "
                    f"(우리팀과 "
                    f"{sign}{diff}점 차이)"
                )

            else:

                diff_str = " (우리팀)"

        else:

            if i == 0:

                diff_str = " (현재 1등 👑)"

            else:

                first_score = teams[
                    sorted_teams[0]
                ]["score"]

                diff = (
                    team_score
                    - first_score
                )

                diff_str = (
                    f" "
                    f"(1등과 {diff}점 차이)"
                )

        embed.add_field(
            name=f"{i + 1}위: {team_name}",
            value=(
                f"점수: {team_score}점"
                f"{diff_str}\n"
                f"목표까지: {left}점 남음\n"
                f"팀원: {members_str}"
            ),
            inline=False
        )

    await ctx.send(
        embed=embed
    )


# ==========================================
# 🛑 내기 강제 종료
# ==========================================

@bot.command(name="내기종료")
@commands.has_permissions(
    administrator=True
)
async def force_end_match(ctx):

    if not match_data["is_active"]:

        reset_all_data()

        await ctx.send(
            "🧹 모든 팀이 해산되고 "
            "데이터가 초기화되었습니다."
        )

        return

    await ctx.send(
        "🛑 관리자에 의해 내기가 종료되었습니다. "
        "결과를 발표합니다."
    )

    await match_result_display(
        ctx.channel
    )

    reset_all_data()

    await ctx.send(
        "🧹 모든 팀이 해산되고 "
        "점수가 완전히 초기화되었습니다."
    )


# ==========================================
# 🏆 내기 결과
# ==========================================

@bot.command(name="내기결과")
async def show_results(ctx):

    if not match_data["participating_teams"]:

        await ctx.send(
            "⚠️ 현재 진행 중인 내기가 없거나 "
            "이미 해산되었습니다."
        )

        return

    await match_result_display(
        ctx.channel
    )


# ==========================================
# 📜 명령어 안내
# ==========================================

@bot.command(name="명령어")
async def show_help(ctx):
    embed = discord.Embed(
        title="🤖 솔랭 내기 봇 명령어 안내",
        description="사용 가능한 명령어와 설명입니다.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="👥 팀 및 등록",
        value=(
            "`!팀생성 [팀이름]` - 새로운 팀을 만듭니다.\n"
            "`!팀등록 [팀이름]` - 해당 팀에 가입합니다. (본인)\n"
            "`!팀등록 [팀이름] @멘션` - 다른 멤버를 팀에 등록합니다. (관리자 전용)"
        ),
        inline=False
    )

    embed.add_field(
        name="🔥 내기 및 점수",
        value=(
            "`!내기시작 [팀1] [팀2] ... [목표점수]` - 내기를 시작합니다.\n"
            "`+점수` 또는 `-점수` (예: `+15`, `-10`) - 채팅창에 입력하면 팀과 본인 점수가 반영됩니다.\n"
            "`!점수` - 내 소속 팀의 현재 점수와 남은 점수를 확인합니다.\n"
            "`!현황` - 현재 진행 중인 내기의 전체 순위와 점수 차이를 확인합니다.\n"
            "`!내기결과` - 현재 내기 결과(순위)를 다시 출력합니다."
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️ 관리자 전용",
        value=(
            "`!내기종료` - 강제로 내기를 종료하고 결과를 발표한 뒤 초기화합니다."
        ),
        inline=False
    )

    await ctx.send(embed=embed)


# ==========================================
# ❌ 명령어 오류 처리
# ==========================================

@bot.event
async def on_command_error(
    ctx,
    error
):

    if isinstance(
        error,
        commands.MissingRequiredArgument
    ):

        await ctx.send(
            "❌ 명령어 사용법이 잘못되었습니다."
        )

        return

    if isinstance(
        error,
        commands.MissingPermissions
    ):

        await ctx.send(
            "❌ 이 명령어를 사용하려면 "
            "관리자 권한이 필요합니다."
        )

        return

    if isinstance(
        error,
        commands.MemberNotFound
    ):

        await ctx.send(
            "❌ 해당 사용자를 찾을 수 없습니다."
        )

        return

    if isinstance(
        error,
        commands.CommandNotFound
    ):

        return

    print(
        f"🚨 명령어 오류: {repr(error)}"
    )


# ==========================================
# 🚀 Bot 실행
# ==========================================

print("🚀 Discord Bot을 시작합니다...")

try:

    bot.run(
        DISCORD_TOKEN
    )

except Exception as e:

    print(
        f"🚨 Discord Bot 실행 실패: {e}"
    )

    raise