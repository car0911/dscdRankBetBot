import discord
from discord.ext import commands
import re
import os
import asyncio
import aiohttp
from aiohttp import web
import pymongo

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

PREFIX = '!'
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ==========================================
# 💾 MongoDB 데이터베이스 연결 및 초기화
# ==========================================
MONGO_URI = os.environ.get('MONGODB_URI')
if not MONGO_URI:
    print("🚨 치명적 오류: MONGODB_URI 환경변수가 없습니다!")
    # 테스트용으로 로컬에서 돌릴 땐 아래 주석을 풀고 직접 넣어서 테스트할 수 있습니다.
    # MONGO_URI = "mongodb+srv://아이디:비밀번호@cluster0.xxx.mongodb.net/?retryWrites=true&w=majority"

# 몽고DB 클라이언트 접속
mongo_client = pymongo.MongoClient(MONGO_URI)
db_client = mongo_client["discord_bet_bot"]
collection = db_client["bet_data"]

# DB에서 데이터 불러오기
doc = collection.find_one({"_id": "main_data"})
if doc:
    teams = doc.get("teams", {})
    user_team = doc.get("user_team", {})
    match_data = doc.get("match_data", {
        'is_active': False,
        'target_score': 0,
        'participating_teams': []
    })
else:
    teams = {}
    user_team = {}
    match_data = {
        'is_active': False,
        'target_score': 0,
        'participating_teams': []
    }

# 데이터 변경 시 MongoDB에 실시간 저장하는 함수
def save_data():
    collection.update_one(
        {"_id": "main_data"},
        {"$set": {
            "teams": teams,
            "user_team": user_team,
            "match_data": match_data
        }},
        upsert=True # 문서가 없으면 새로 생성
    )

def reset_all_data():
    teams.clear()
    user_team.clear()
    match_data['is_active'] = False
    match_data['target_score'] = 0
    match_data['participating_teams'].clear()
    save_data()

async def match_result_display(channel):
    if not match_data['participating_teams']:
        return
    sorted_teams = sorted(match_data['participating_teams'], key=lambda t: teams[t]['score'], reverse=True)
    ranks = ["Winner", "2nd", "3rd", "4th", "5th", "6th"]
    result_lines = []

    for i, t in enumerate(sorted_teams):
        rank_tag = ranks[i] if i < len(ranks) else f"{i+1}th"
        member_names = []
        for uid_str in teams[t]['members']:
            user = bot.get_user(int(uid_str))
            name = user.display_name if user else f"알수없음"
            member_names.append(name)

        m_str = " ".join(member_names)
        result_lines.append(f"{rank_tag} '{t}' {m_str} ({teams[t]['score']}점)")

    embed = discord.Embed(title="🏆 최종 내기 결과 🏆", description="\n\n".join(result_lines), color=discord.Color.gold())
    await channel.send(embed=embed)

# ==========================================
# 🌐 Koyeb 24시간 구동을 위한 Web 서버 & 셀프 핑
# ==========================================
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()

async def ping_self():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            koyeb_url = os.environ.get('KOYEB_URL')
            if koyeb_url:
                async with aiohttp.ClientSession() as s:
                    await s.get(koyeb_url)
        except Exception as e:
            pass
        await asyncio.sleep(180)

@bot.event
async def on_ready():
    print(f'✅ 성공적으로 로그인되었습니다: {bot.user}')
    bot.loop.create_task(start_web_server())
    bot.loop.create_task(ping_self())

# ==========================================
# 기존 기능들 (명령어)
# ==========================================
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.content.startswith(PREFIX):
        content_body = message.content[len(PREFIX):].strip()
        match = re.match(r'^([+-]?|0)(\d+)$', content_body)

        if match:
            sign = match.group(1)
            num = int(match.group(2))
            points = -num if sign in ['-', '0'] else num
            user_id_str = str(message.author.id)

            if user_id_str not in user_team:
                await message.channel.send(f"❌ {message.author.mention}님은 소속된 팀이 없습니다.")
                return

            my_team = user_team[user_id_str]
            teams[my_team]['score'] += points
            teams[my_team]['members'][user_id_str] += points
            save_data()

            team_score = teams[my_team]['score']
            my_score = teams[my_team]['members'][user_id_str]
            sign_str = f"+{points}" if points > 0 else f"{points}"

            await message.channel.send(f"📈 {my_team} 점수 변동: {sign_str}점\n> 🏆 팀 총점: {team_score}점 | 👤 {message.author.display_name} 개인 점수: {my_score}점")

            if match_data['is_active'] and my_team in match_data['participating_teams']:
                if team_score >= match_data['target_score']:
                    await message.channel.send(f"\n🎉🎉 축하합니다! '{my_team}' 팀이 목표 점수({match_data['target_score']}점)에 가장 먼저 도달했습니다! 🎉🎉")
                    await match_result_display(message.channel)
                    reset_all_data()
                    await message.channel.send("🧹 내기가 종료되어 모든 팀이 해산되고 점수가 초기화되었습니다.")
            return
    await bot.process_commands(message)

@bot.command(name="팀생성")
async def create_team(ctx, team_name: str):
    if team_name in teams:
        await ctx.send(f"❌ '{team_name}'(은)는 이미 존재하는 팀입니다.")
        return
    teams[team_name] = {'score': 0, 'members': {}}
    save_data()
    await ctx.send(f"✅ '{team_name}' 팀이 생성되었습니다.")

@bot.command(name="팀등록")
async def join_team(ctx, team_name: str, member: discord.Member = None):
    target_user = member if member else ctx.author
    user_id_str = str(target_user.id)
    if member and not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ 다른 사용자를 팀에 등록하려면 관리자 권한이 필요합니다.")
        return
    if team_name not in teams:
        await ctx.send(f"❌ '{team_name}'(은)는 존재하지 않는 팀입니다.")
        return
    if user_id_str in user_team:
        current_team = user_team[user_id_str]
        await ctx.send(f"❌ {target_user.mention}님은 이미 '{current_team}' 팀에 소속되어 있습니다.")
        return
    teams[team_name]['members'][user_id_str] = 0
    user_team[user_id_str] = team_name
    save_data()
    await ctx.send(f"✅ {target_user.mention}님이 '{team_name}' 팀에 등록되었습니다.")

@bot.command(name="내기시작")
async def start_match(ctx, *args):
    if len(args) < 2:
        await ctx.send("⚠️ 사용법: `!내기시작 [팀이름1] [팀이름2] ... [목표점수]`")
        return
    try:
        target_score = int(args[-1])
    except ValueError:
        await ctx.send("❌ 마지막 입력값은 목표 점수(숫자)여야 합니다.")
        return
    target_teams = list(args[:-1])
    user_id_str = str(ctx.author.id)
    my_team = user_team.get(user_id_str)
    if not my_team:
        await ctx.send("❌ 본인이 소속된 팀이 있어야 내기를 시작할 수 있습니다.")
        return
    if my_team not in target_teams:
        target_teams.append(my_team)
    for t in target_teams:
        if t not in teams:
            await ctx.send(f"❌ '{t}'(은)는 존재하지 않는 팀입니다.")
            return
    target_teams = list(set(target_teams))
    match_data['is_active'] = True
    match_data['target_score'] = target_score
    match_data['participating_teams'] = target_teams
    save_data()
    await ctx.send(f"🔥 내기가 시작되었습니다! 🔥\n> ⚔️ 참여 팀: {', '.join(target_teams)}\n> 🎯 목표 점수: {target_score}점")

@bot.command(name="점수")
async def check_score(ctx):
    user_id_str = str(ctx.author.id)
    my_team = user_team.get(user_id_str)
    if not my_team:
        await ctx.send("❌ 소속된 팀이 없습니다.")
        return
    team_score = teams[my_team]['score']
    if match_data['is_active'] and my_team in match_data['participating_teams']:
        left = match_data['target_score'] - team_score
        await ctx.send(f"📊 [{my_team}] 현재 팀 점수: {team_score}점 (목표까지 {left}점 남음)")
    else:
        await ctx.send(f"📊 [{my_team}] 현재 팀 점수: {team_score}점")

@bot.command(name="현황")
async def match_status(ctx):
    if not match_data['is_active']:
        await ctx.send("⚠️ 현재 진행 중인 내기가 없습니다.")
        return
    user_id_str = str(ctx.author.id)
    my_team = user_team.get(user_id_str)
    target = match_data['target_score']
    p_teams = match_data['participating_teams']
    sorted_teams = sorted(p_teams, key=lambda t: teams[t]['score'], reverse=True)

    embed = discord.Embed(title="📈 현재 솔랭 내기 현황", color=discord.Color.green())
    for i, t in enumerate(sorted_teams):
        t_score = teams[t]['score']
        left = target - t_score
        member_texts = []
        for uid_str, p_score in teams[t]['members'].items():
            user = ctx.guild.get_member(int(uid_str))
            name = user.display_name if user else f"알수없음"
            member_texts.append(f"{name} ({p_score}점)")
        members_str = ", ".join(member_texts) if member_texts else "팀원 없음"

        diff_str = ""
        if my_team and my_team in p_teams:
            if t != my_team:
                my_score = teams[my_team]['score']
                diff = t_score - my_score
                sign = "+" if diff > 0 else ""
                diff_str = f" (우리팀과 {sign}{diff}점 차이)"
            else:
                diff_str = " (우리팀)"
        else:
            if i == 0:
                diff_str = " (현재 1등 👑)"
            else:
                first_score = teams[sorted_teams[0]]['score']
                diff = t_score - first_score
                diff_str = f" (1등과 {diff}점 차이)"
        embed.add_field(name=f"{i+1}위: {t}", value=f"점수: {t_score}점 {diff_str}\n목표까지: {left}점 남음\n팀원: {members_str}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="내기종료")
@commands.has_permissions(administrator=True)
async def force_end_match(ctx):
    if not match_data['is_active']:
        reset_all_data()
        await ctx.send("🧹 모든 팀이 해산되고 데이터가 초기화되었습니다.")
        return
    await ctx.send("🛑 관리자에 의해 내기가 종료되었습니다. 결과를 발표합니다.")
    await match_result_display(ctx.channel)
    reset_all_data()
    await ctx.send("🧹 모든 팀이 해산되고 점수가 완전히 초기화되었습니다.")

@bot.command(name="내기결과")
async def show_results(ctx):
    if not match_data['participating_teams']:
        await ctx.send("⚠️ 현재 진행 중인 내기가 없거나 이미 해산되었습니다.")
        return
    await match_result_display(ctx.channel)

bot.run(os.environ.get('DISCORD_TOKEN'))