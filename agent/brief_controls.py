"""Discord components for the same persistent settings used by the web and MCP."""
import discord
from .owner_controls import BASE,REPORTS


class BriefHomeView(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=None);self.bot=bot
        button=discord.ui.Button(label='보고서 수신 설정',custom_id='daevis_brief_settings',style=discord.ButtonStyle.primary)
        button.callback=self.open;self.add_item(button)
    async def open(self,interaction):
        if interaction.user.id!=self.bot.owner_id:
            return await interaction.response.send_message('본인만 설정할 수 있어요.',ephemeral=True)
        view=BriefView(self.bot)
        await interaction.response.send_message(view.content(),view=view,ephemeral=True)


class BriefView(discord.ui.View):
    def __init__(self,bot):
        super().__init__(timeout=300);self.bot=bot
        current=bot.control_store.preferences()
        for key,name in REPORTS.items():
            button=discord.ui.Button(label=name+(' · 켜짐' if current[key] else ' · 꺼짐'),style=discord.ButtonStyle.success if current[key] else discord.ButtonStyle.secondary,row=0 if len(self.children)<3 else 1)
            async def toggle(interaction,kind=key):
                if interaction.user.id!=bot.owner_id:return await interaction.response.send_message('본인만 설정할 수 있어요.',ephemeral=True)
                saved=bot.control_store.preferences();bot.control_store.preferences({kind:not saved[kind]})
                view=BriefView(bot);await interaction.response.edit_message(content=view.content(),view=view)
            button.callback=toggle;self.add_item(button)
        web=discord.ui.Button(label='웹 수신 설정 열기',row=2)
        async def web_login(interaction):
            if interaction.user.id!=bot.owner_id:return await interaction.response.send_message('본인만 설정할 수 있어요.',ephemeral=True)
            url=BASE+'/briefing/#'+bot.control_store.issue_login()
            await interaction.response.send_message('15분 안에 한 번 사용할 수 있는 본인 로그인 링크입니다.\n'+url,ephemeral=True)
        web.callback=web_login;self.add_item(web)
    def content(self):return '보고서 5종의 자동 수신 설정입니다. 각각 따로 켜고 끌 수 있으며 웹·자연어 채팅에도 같은 설정이 적용됩니다.'
