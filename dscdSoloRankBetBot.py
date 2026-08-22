import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands
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

        # 복수 active_matches를 우선적으로 불러오기
        active_matches = doc.get("active_matches", [])

        # active_matches가 비어있고 예전 방식인 match_data가 남아있는 경우 호환 처리
        if not active_matches and "match_data" in doc:
            raw_match = doc.get("match_data", {})
            if isinstance(raw_match, dict) and raw_match.get("is_active"):
                active_matches = [raw_match]

        # 완료된 기록(finished_matches) 불러오기
        finished_matches = doc.get("finished_matches", [])

        print("✅ 기존 데이터를 MongoDB에서 불러왔습니다.")

    else:
        teams = {}
        user_team = {}
        active_matches = []
        finished_matches = []

        print("ℹ️ 기존 데이터가 없어 새 데이터로 시작합니다.")

except Exception as e:
    raise RuntimeError(f"🚨 MongoDB 데이터 불러오기 실패: {e}")

# ==========================================
# 💾 데이터 저장 함수
# ==========================================
def save_data():
    try:
        collection.update_one(
            {"_id": "main_data"},
            {
                "$set": {
                    "teams": teams,
                    "user_team": user_team,
                    "active_matches": active_matches,
                    "finished_matches": finished_matches
                }
            },
            upsert=True
        )
    except Exception as e:
        print(f"🚨 MongoDB 저장 실패: {e}")

# ==========================================
# 🔄 전체 데이터 초기화 함수
# ==========================================

def reset_all_data():
    teams.clear()
    user_team.clear()
    active_matches.clear()
    finished_matches.clear()
    save_data()

# ==========================================
# 🏆 경기 결과 표시 및 점수 정산 처리
# ==========================================

async def match_result_display(channel, match_item):
    participating_teams = match_item.get("participating_teams", [])
    if not participating_teams:
        return

    sorted_teams = sorted(
        participating_teams,
        key=lambda t: teams.get(t, {}).get("score", 0),
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

        member_details = []
        team_members = teams.get(team_name, {}).get("members", {})

        for uid_str in team_members:
            try:
                user = channel.guild.get_member(int(uid_str))
                if user:
                    server_name = user.display_name
                    mention_str = user.mention
                else:
                    server_name = "알수없음"
                    mention_str = f"<@{uid_str}>"
            except Exception:
                server_name = "알수없음"
                mention_str = f"<@{uid_str}>"

            member_details.append(
                f"{server_name}({mention_str})"
            )

        member_string = " ".join(member_details)
        t_score = teams.get(team_name, {}).get("score", 0)

        result_lines.append(
            f"{rank_tag} '{team_name}' "
            f"{member_string} "
            f"({t_score}점)"
        )

    embed = discord.Embed(
        title=f"🏆 내기 결과 [{match_item['match_id']}] 🏆",
        description="\n\n".join(result_lines),
        color=discord.Color.gold()
    )

    await channel.send(embed=embed)

    # 1. 완료된 내기를 finished_matches로 이동
    finished_matches.append(match_item)

    # 2. [디버그 핵심] 현재 남아있는 '다른 활성 내기'가 있는지 확인
    # 방금 끝난 match_item은 이미 active_matches에서 제거되었거나 제거될 예정이므로,
    # active_matches에 남아있는 개수가 0개일 때만 점수를 0으로 리셋합니다.
    if not active_matches:
        for t_name in teams:
            teams[t_name]["score"] = 0
            for uid in teams[t_name]["members"]:
                teams[t_name]["members"][uid] = 0
        await channel.send("🧹 진행 중인 모든 내기가 종료되어 모든 팀과 개인의 점수가 초기화되었습니다.")
    else:
        await channel.send("ℹ️ 아직 진행 중인 다른 내기가 있으므로 점수 초기화는 보류됩니다.")

    save_data()

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
    print("✅ Discord 로그인 성공!")
    print(f"🤖 Bot: {bot.user}")
    print(f"🆔 Bot ID: {bot.user.id}")
    print("=" * 50)

    if not hasattr(bot, "web_server_started"):
        bot.web_server_started = True
        bot.loop.create_task(
            start_web_server()
        )

# ==========================================
# 💬 메시지 처리 (타 사용자 점수 변동 지원)
# ==========================================

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    content_body = message.content.strip()

    # 정규식: 부호(+,-,0) + 숫자 + (선택적) 멘션
    match = re.match(
        r"^([+0-]?)\s*(\d+)(?:\s+<@!?\d+>)?\s*$",
        content_body
    )

    if match:
        prefix = match.group(1)
        raw_num = match.group(2)
        num = int(raw_num)

        # 멘션된 사용자가 있으면 대상 변경, 없으면 본인
        target_user = (
            message.mentions[0]
            if message.mentions
            else message.author
        )

        if prefix in ("0", "-"):
            points = -num
        else:
            points = num

        target_id_str = str(
            target_user.id
        )

        if target_id_str not in user_team:
            await message.channel.send(
                f"❌ {target_user.mention}님은 "
                f"소속된 팀이 없습니다."
            )
            return

        my_team = user_team[target_id_str]

        if my_team not in teams:
            await message.channel.send(
                "❌ 소속된 팀 정보를 찾을 수 없습니다."
            )
            return

        # 점수 반영 (대상 유저 기준)
        teams[my_team]["score"] += points

        if target_id_str not in teams[my_team]["members"]:
            teams[my_team]["members"][target_id_str] = 0

        teams[my_team]["members"][target_id_str] += points

        save_data()

        team_score = teams[my_team]["score"]
        target_score = teams[my_team]["members"][target_id_str]

        sign_str = (
            f"{points}"
            if points < 0
            else f"+{points}"
        )

        await message.channel.send(
            f"📈 {my_team} 점수 변동: "
            f"{sign_str}점\n"
            f"> 🏆 팀 총점: {team_score}점 | "
            f"👤 {target_user.display_name} "
            f"개인 점수: {target_score}점"
        )

        completed_matches = []
        for match_item in active_matches:
            if my_team in match_item["participating_teams"]:
                if team_score >= match_item["target_score"]:
                    completed_matches.append(match_item)

        for match_item in completed_matches:
            await message.channel.send(
                f"\n🎉🎉 축하합니다! "
                f"'{my_team}' 팀이 내기 [{match_item['match_id']}]의 "
                f"목표 점수({match_item['target_score']}점)에 가장 먼저 도달했습니다! 🎉🎉"
            )

            active_matches.remove(match_item)
            save_data()

            await match_result_display(message.channel, match_item)

            await message.channel.send(
                f"🧹 내기 [{match_item['match_id']}]가 종료되었습니다."
            )

        return

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
    *members: discord.Member
):
    if team_name not in teams:
        await ctx.send(
            f"❌ '{team_name}'(은)는 "
            f"존재하지 않는 팀입니다."
        )
        return

    target_members = (
        list(members)
        if members
        else [ctx.author]
    )

    is_registering_others = any(
        m.id != ctx.author.id
        for m in target_members
    )

    if (
        is_registering_others
        and not ctx.author.guild_permissions.administrator
    ):
        await ctx.send(
            "❌ 다른 사용자를 팀에 등록하려면 "
            "관리자 권한이 필요합니다."
        )
        return

    registered_names = []

    for target_user in target_members:
        user_id_str = str(
            target_user.id
        )

        if user_id_str in user_team:
            old_team = user_team[user_id_str]
            if (
                old_team in teams
                and user_id_str in teams[old_team]["members"]
            ):
                del teams[old_team]["members"][user_id_str]

        teams[team_name]["members"][user_id_str] = 0
        user_team[user_id_str] = team_name
        registered_names.append(
            target_user.mention
        )

    save_data()

    await ctx.send(
        f"✅ {', '.join(registered_names)}님이 "
        f"'{team_name}' 팀에 등록되었습니다."
    )

# ==========================================
# 📋 팀 명단 / 팀 목록 조회
# ==========================================

@bot.command(
    name="팀명단",
    aliases=["팀목록"]
)
async def show_teams(ctx):
    if not teams:
        await ctx.send(
            "⚠️ 현재 생성된 팀이 없습니다."
        )
        return

    embed = discord.Embed(
        title="👥 전체 팀 및 팀원 명단",
        color=discord.Color.blue()
    )

    for team_name, data in teams.items():
        team_score = data.get(
            "score",
            0
        )
        members_dict = data.get(
            "members",
            {}
        )

        member_texts = []

        for uid_str, p_score in members_dict.items():
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
                f"{name} ({p_score}점)"
            )

        members_str = (
            ", ".join(member_texts)
            if member_texts
            else "팀원 없음"
        )

        embed.add_field(
            name=f"🛡️ {team_name} (총점: {team_score}점)",
            value=f"└ 팀원: {members_str}",
            inline=False
        )

    await ctx.send(embed=embed)

# ==========================================
# 🗑️ 팀원 삭제
# ==========================================

@bot.command(name="팀원삭제")
async def remove_member(ctx, team_name: str, member: discord.Member):
    if team_name not in teams:
        await ctx.send(f"❌ '{team_name}' 팀을 찾을 수 없습니다.")
        return

    member_id_str = str(member.id)

    if member_id_str not in teams[team_name]["members"]:
        await ctx.send(f"❌ {member.mention}님은 '{team_name}' 팀에 등록되어 있지 않습니다.")
        return

    member_score = teams[team_name]["members"][member_id_str]
    teams[team_name]["score"] -= member_score

    del teams[team_name]["members"][member_id_str]
    del user_team[member_id_str]

    save_data()

    await ctx.send(
        f"✅ '{team_name}' 팀에서 {member.mention}님을 삭제했습니다.\n"
        f"└ 차감된 팀 점수: {member_score}점"
    )

# ==========================================
# 🔥 내기 시작
# ==========================================

@bot.command(name="내기시작")
async def start_match(
    ctx,
    *args
):
    if len(args) < 3:
        await ctx.send(
            "⚠️ 사용법:\n"
            "`!내기시작 [팀1] [팀2] [목표점수]` (내기 이름 자동생성: 월일시각)\n"
            "`!내기시작 [내기이름] [팀1] [팀2] [목표점수]`"
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

    middle_args = args[:-1]

    now_str = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime("%m%d%H")

    if middle_args[0] not in teams:
        match_id = middle_args[0]
        participating_teams = list(
            middle_args[1:]
        )
    else:
        match_id = now_str
        participating_teams = list(
            middle_args
        )

    if len(participating_teams) < 2:
        await ctx.send(
            "❌ 내기에 참여할 팀은 "
            "최소 2개 이상이어야 합니다."
        )
        return

    base_id = match_id
    counter = 1

    while any(
        m["match_id"] == match_id
        for m in active_matches
    ):
        match_id = f"{base_id}_{counter}"
        counter += 1

    for team_name in participating_teams:
        if team_name not in teams:
            await ctx.send(
                f"❌ '{team_name}'(은)는 "
                f"존재하지 않는 팀입니다."
            )
            return

    participating_teams = list(
        dict.fromkeys(
            participating_teams
        )
    )

    new_match = {
        "match_id": match_id,
        "is_active": True,
        "target_score": target_score,
        "participating_teams": participating_teams
    }

    active_matches.append(new_match)
    save_data()

    await ctx.send(
        f"🔥 새로운 내기가 시작되었습니다! (식별 ID: `{match_id}`) 🔥\n"
        f"> ⚔️ 참여 팀: {', '.join(participating_teams)}\n"
        f"> 🎯 목표 점수: {target_score}점"
    )

# ==========================================
# 📈 현황
# ==========================================

@bot.command(name="현황")
async def match_status(ctx):
    if not active_matches:
        await ctx.send("⚠️ 현재 진행 중인 내기가 없습니다.")
        return

    embed = discord.Embed(
        title="📈 전체 솔랭 내기 현황",
        color=discord.Color.green()
    )

    for idx, match_item in enumerate(active_matches):
        match_id = match_item["match_id"]
        target_score = match_item["target_score"]
        participating_teams = match_item["participating_teams"]

        sorted_teams = sorted(
            participating_teams,
            key=lambda t: teams.get(t, {}).get("score", 0),
            reverse=True
        )

        team_status_lines = []
        top_score = 0

        for i, team_name in enumerate(sorted_teams):
            team_score = teams.get(team_name, {}).get("score", 0)

            if i == 0:
                top_score = team_score

            left = target_score - team_score

            if i == 0:
                score_diff_text = f"목표까지 {left}점"
            else:
                diff = top_score - team_score
                score_diff_text = f"1등까지 +{diff}점 / 목표까지 {left}점"

            member_texts = []
            team_members = teams.get(team_name, {}).get("members", {})
            for uid_str, p_score in team_members.items():
                try:
                    user = ctx.guild.get_member(int(uid_str))
                    name = user.display_name if user else "알수없음"
                except Exception:
                    name = "알수없음"

                member_texts.append(f"{name} ({p_score}점)")
            members_str = ", ".join(member_texts) if member_texts else "팀원 없음"

            team_status_lines.append(
                f"{i + 1}위: {team_name} ({team_score}점 / {score_diff_text})\n"
                f"└ 팀원: {members_str}"
            )

        embed.add_field(
            name=f"[{idx + 1}] 내기 ID: {match_id} (목표: {target_score}점)",
            value="\n".join(team_status_lines) if team_status_lines else "참여 팀 없음",
            inline=False
        )

    await ctx.send(embed=embed)

# ==========================================
# 🛑 내기 종료 (특정 ID 지정 가능)
# ==========================================

@bot.command(name="내기종료")
@commands.has_permissions(
    administrator=True
)
async def force_end_match(ctx, match_id: str = None):
    if not active_matches:
        await ctx.send(
            "⚠️ 현재 진행 중인 내기가 없습니다."
        )
        return

    if match_id:
        target_match = None
        for m in active_matches:
            if m["match_id"] == match_id:
                target_match = m
                break
        if not target_match:
            await ctx.send(
                f"❌ 식별 ID가 `{match_id}`인 내기를 찾을 수 없습니다."
            )
            return

        await ctx.send(
            f"🛑 관리자에 의해 내기 `{match_id}`가 종료되었습니다. 결과를 발표합니다."
        )
        await match_result_display(ctx.channel, target_match)

        active_matches.remove(target_match)
        save_data()
        await ctx.send(
            f"🧹 내기 `{match_id}`가 정리되었습니다."
        )
    else:
        if len(active_matches) == 1:
            target_match = active_matches[0]
            await ctx.send(
                f"🛑 관리자에 의해 내기 `{target_match['match_id']}`가 종료되었습니다. 결과를 발표합니다."
            )
            await match_result_display(ctx.channel, target_match)

            active_matches.clear()
            save_data()
            await ctx.send(
                "🧹 모든 내기가 종료되고 정리되었습니다."
            )
        else:
            ids = ", ".join([m["match_id"] for m in active_matches])
            await ctx.send(
                f"⚠️ 진행 중인 내기가 여러 개입니다. 종료할 ID를 지정해주세요.\n> 목록 ID: {ids}"
            )

# ==========================================
# 📜 기록 조회 및 삭제 명령어
# ==========================================

@bot.command(name="기록")
async def show_finished_matches(ctx):
    visible_matches = [
        m for m in finished_matches
        if not m.get("is_hidden", False)
    ]

    if not visible_matches:
        await ctx.send(
            "📜 완료된 내기 기록이 없습니다."
        )
        return

    embed = discord.Embed(
        title="📜 완료된 내기 기록",
        color=discord.Color.purple()
    )

    for m in visible_matches:
        teams_str = ", ".join(
            m.get(
                "participating_teams", []
            )
        )
        embed.add_field(
            name=f"ID: {m['match_id']}",
            value=(
                f"참여팀: {teams_str} "
                f"(목표: {m.get('target_score')}점)"
            ),
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name="기록삭제")
@commands.has_permissions(
    administrator=True
)
async def hide_finished_match(
    ctx,
    match_id: str
):
    found = False

    for m in finished_matches:
        if m["match_id"] == match_id:
            m["is_hidden"] = True
            found = True
            break

    if found:
        save_data()
        await ctx.send(
            f"✅ 완료된 내기 기록 "
            f"[{match_id}]를 `!기록` 목록에서 숨겼습니다."
        )
    else:
        await ctx.send(
            f"❌ 해당 ID의 기록을 찾을 수 없습니다."
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
            "`!팀등록 [팀이름]` - 본인을 해당 팀에 등록합니다.\n"
            "`!팀등록 [팀이름] @사용자1 @사용자2` - 여러 명을 한 번에 팀에 등록합니다. (관리자 전용)\n"
            "`!팀원삭제 [팀이름] @사용자명` - 특정 팀원에서 사용자를 제거하고 점수를 정산합니다.\n"
            "`!팀명단` (또는 `!팀목록`) - 생성된 모든 팀과 소속 팀원, 점수를 확인합니다."
        ),
        inline=False
    )

    embed.add_field(
        name="🔥 내기 및 점수",
        value=(
            "`!내기시작 [팀1] [팀2] [목표점수]` - 내기를 시작합니다. (내기 이름 미지정 시 한국 시간 기준 월일시각 자동 생성)\n"
            "`!내기시작 [내기이름] [팀1] [팀2] [목표점수]` - 이름을 지정하여 내기를 시작합니다.\n"
            "`숫자` 또는 `+숫자` - 점수를 획득합니다. (예: `23`, `+23`)\n"
            "`0숫자` 또는 `-숫자` - 점수가 차감됩니다. (예: `023`, `-23`)\n"
            "`[점수] @사용자명` - 다른 사람의 점수를 대신 변동시킵니다. (예: `23 @홍길동`, `023 @홍길동`)\n"
            "`!현황` - 현재 진행 중인 모든 내기의 순위와 점수 차이를 확인합니다."
        ),
        inline=False
    )

    embed.add_field(
        name="📜 기록 및 관리자 전용",
        value=(
            "`!기록` - 완료된 내기 기록들을 확인합니다.\n"
            "`!기록삭제 [내기ID]` - 지정한 ID의 종료된 내기 기록을 목록에서 숨깁니다. (관리자 전용)\n"
            "`!내기종료` - 진행 중인 내기를 강제 종료합니다. (ID 지정 가능, 관리자 전용)"
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